import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import { apiToken, assortArticles, bearer, loginUi, provisionTenant, tokenFor } from './support';

/**
 * Lot 3-E (ADR-0043), sur une entreprise créée pour l'exécution : fiche fournisseur ouverte
 * depuis la liste ; synthèse et articles reçus calculés sur les seules réceptions VALIDÉES
 * (réception annulée et brouillon visibles dans la liste des réceptions, exclus des agrégats et
 * du dernier coût) ; dernier coût par unité de base (réception en cartons) ; fournisseur
 * principal ; chronologie réelle après une modification ; recherche et filtre fournisseur des
 * entrées ; coûts absents sans `cost_view` (interface ET API) ; isolation entre entreprises ;
 * affichage mobile.
 */

const RUN = Date.now().toString().slice(-7);
const TENANT = `Fournisseurs E2E ${RUN}`;
const EMAIL = `fournisseurs-${RUN}@example.com`;
const PASSWORD = 'E2e-Fournisseurs-2026';
const OTHER_TENANT = `Autre fournisseurs E2E ${RUN}`;
const OTHER_EMAIL = `autre-fournisseurs-${RUN}@example.com`;
const SUPPLIER = `Faso Import ${RUN}`;
const OTHER_SUPPLIER = `Sahel Distribution ${RUN}`;
const SODA = `Soda 3E ${RUN}`;
const RICE = `Riz 3E ${RUN}`;
const MAIN_ONLY = `Huile 3E ${RUN}`;

interface World {
  token: string;
  site: string;
  supplier: string;
  numbers: Record<'r1' | 'r2' | 'r3' | 'r4' | 'other', string>;
}

let world: World;

async function post<T = { id: string; number: string }>(
  request: APIRequestContext,
  path: string,
  data: unknown,
  token = world.token,
): Promise<T> {
  const response = await request.post(`/api/v1${path}`, { headers: bearer(token), data });
  expect(response.status(), await response.text()).toBeLessThan(300);
  return (await response.json()) as T;
}

const day = (offset: number) =>
  new Date(Date.now() - offset * 86_400_000).toISOString().slice(0, 10);

async function reception(
  request: APIRequestContext,
  supplier: string,
  lines: { article_id: string; quantity: string; unit_cost: string; packaging_id?: string }[],
  offset: number,
  then: 'validate' | 'cancel' | 'draft',
): Promise<string> {
  const entry = await post(request, '/stock/entries', {
    site_id: world.site,
    supplier_id: supplier,
    operation_date: day(offset),
    document_reference: `BL-${offset}-${RUN}`,
    lines,
  });
  if (then !== 'draft') await post(request, `/stock/entries/${entry.id}/validate`, {});
  if (then === 'cancel') {
    await post(request, `/stock/entries/${entry.id}/cancel`, { reason: 'Erreur de saisie E2E' });
  }
  return entry.number;
}

async function openSupplier(page: Page) {
  await page.goto('/suppliers');
  await page.getByRole('searchbox').fill(SUPPLIER);
  await page.getByRole('cell', { name: SUPPLIER, exact: true }).click();
  await expect(page.getByRole('heading', { name: SUPPLIER })).toBeVisible();
}

test.describe.configure({ mode: 'serial' });

test.beforeAll(async ({ request }) => {
  await provisionTenant(request, {
    name: TENANT,
    profile: 'retail.alimentation',
    email: EMAIL,
    password: PASSWORD,
  });
  const token = await tokenFor(request, EMAIL, PASSWORD, TENANT);
  const sites = (await (await request.get('/api/v1/sites', { headers: bearer(token) })).json()) as {
    id: string;
  }[];
  world = { token, site: sites[0]?.id ?? '' } as World;
  const category = (await post(request, '/catalog/categories', { name: `Épicerie 3E ${RUN}` })).id;
  const article = async (designation: string, reference: string) =>
    (
      await post(request, '/catalog/articles', {
        reference,
        designation,
        category_id: category,
        unit: 'u',
        purchase_price: '80',
      })
    ).id;
  const soda = await article(SODA, `SODA3E-${RUN}`);
  const rice = await article(RICE, `RIZ3E-${RUN}`);
  const oil = await article(MAIN_ONLY, `HUILE3E-${RUN}`);
  // Assortiment (ADR-0046) : articles ajoutés EXPLICITEMENT aux sites de l'entreprise.
  await assortArticles(request, token, [soda, rice, oil]);
  const carton = (
    await post(request, `/catalog/articles/${soda}/packagings`, {
      name: 'Carton 24',
      conversion: '24',
    })
  ).id;
  world.supplier = (await post(request, '/suppliers', { name: SUPPLIER })).id;
  const other = (await post(request, '/suppliers', { name: OTHER_SUPPLIER })).id;
  const patched = await request.patch(`/api/v1/catalog/articles/${oil}`, {
    headers: bearer(token),
    data: { main_supplier_id: world.supplier },
  });
  expect(patched.status()).toBe(200);
  world.numbers = {
    // R1 (J-10) : 10 sodas à 90 + 5 riz à 200 = 1 900.
    r1: await reception(
      request,
      world.supplier,
      [
        { article_id: soda, quantity: '10', unit_cost: '90' },
        { article_id: rice, quantity: '5', unit_cost: '200' },
      ],
      10,
      'validate',
    ),
    // R2 (J-5) : 1 carton de 24 à 2 400 (100 / u) — dernière réception validée du soda.
    r2: await reception(
      request,
      world.supplier,
      [{ article_id: soda, packaging_id: carton, quantity: '1', unit_cost: '2400' }],
      5,
      'validate',
    ),
    // R3 (J-2) : annulée ; R4 (J-1) : brouillon — jamais dans les agrégats.
    r3: await reception(
      request,
      world.supplier,
      [{ article_id: soda, quantity: '1', unit_cost: '999' }],
      2,
      'cancel',
    ),
    r4: await reception(
      request,
      world.supplier,
      [{ article_id: soda, quantity: '1', unit_cost: '777' }],
      1,
      'draft',
    ),
    other: await reception(
      request,
      other,
      [{ article_id: rice, quantity: '1', unit_cost: '50' }],
      3,
      'validate',
    ),
  };
});

test.describe('Fiche fournisseur — Lot 3-E', () => {
  test('1 — liste → fiche : synthèse des seules réceptions validées, réceptions avec statuts', async ({
    page,
  }) => {
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await openSupplier(page);
    const metrics = page.getByRole('group', { name: 'Réceptions validées' });
    await expect(metrics.getByText('2', { exact: true })).toBeVisible();
    await expect(metrics.getByText(/4\s300\sF\s?CFA/)).toBeVisible();
    const rows = page.getByRole('tabpanel').getByRole('row');
    for (const [number, status] of [
      [world.numbers.r1, 'Validé'],
      [world.numbers.r2, 'Validé'],
      [world.numbers.r3, 'Annulé'],
      [world.numbers.r4, 'Brouillon'],
    ] as const) {
      await expect(rows.filter({ hasText: number }).getByText(status)).toBeVisible();
    }
    await expect(rows.filter({ hasText: world.numbers.other })).toHaveCount(0);
  });

  test('2 — articles reçus (dernier coût par unité de base) et fournisseur principal', async ({
    page,
  }) => {
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await openSupplier(page);
    await page.getByRole('tab', { name: 'Articles reçus' }).click();
    const soda = page.getByRole('row').filter({ hasText: SODA });
    await expect(soda.getByText('34 u')).toBeVisible(); // 10 + 24, en unité de base
    await expect(soda.getByText(world.numbers.r2)).toBeVisible();
    await expect(soda.getByText(/100\sF\s?CFA \/ u/)).toBeVisible(); // ni 999 (annulée) ni 777
    await expect(page.getByRole('row').filter({ hasText: RICE }).getByText('5 u')).toBeVisible();
    await expect(page.getByRole('row').filter({ hasText: MAIN_ONLY })).toHaveCount(0);
    await page.getByRole('tab', { name: 'Fournisseur principal' }).click();
    await expect(page.getByRole('row').filter({ hasText: MAIN_ONLY })).toBeVisible();
    await expect(page.getByRole('row').filter({ hasText: SODA })).toHaveCount(0);
  });

  test('3 — modification puis chronologie réelle', async ({ page }) => {
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await openSupplier(page);
    await page.getByRole('button', { name: 'Modifier' }).click();
    await page.getByRole('dialog').getByLabel('Téléphone').fill('70 11 22 33');
    await page.getByRole('dialog').getByRole('button', { name: 'Enregistrer' }).click();
    await expect(page.getByRole('dialog')).toBeHidden();
    await expect(page.getByText('70 11 22 33')).toBeVisible();
    await page.getByRole('tab', { name: 'Chronologie' }).click();
    const timeline = page.getByRole('list', { name: 'Chronologie' });
    await expect(timeline.getByText('Fournisseur créé')).toBeVisible();
    await expect(timeline.getByText('Fournisseur modifié')).toBeVisible();
    await expect(timeline.getByText('Champs modifiés : Téléphone')).toBeVisible();
  });

  test('4 — entrées : recherche par nom et filtre fournisseur', async ({ page }) => {
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto('/stock/entries');
    await page.getByPlaceholder('N°, référence de pièce ou fournisseur…').fill(OTHER_SUPPLIER);
    await expect(page.getByRole('cell', { name: world.numbers.other })).toBeVisible();
    await expect(page.getByRole('cell', { name: world.numbers.r1 })).toHaveCount(0);
    await page.getByRole('button', { name: 'Réinitialiser' }).click();
    await page.locator('.p-dropdown', { hasText: 'Tous les fournisseurs' }).click();
    await page.locator('.p-dropdown-panel').last().getByRole('option', { name: SUPPLIER }).click();
    await expect(page.getByRole('cell', { name: world.numbers.r1 })).toBeVisible();
    await expect(page.getByRole('cell', { name: world.numbers.other })).toHaveCount(0);
  });

  test('5 — sans cost_view : ni total ni dernier coût (interface et API)', async ({
    page,
    request,
  }) => {
    const role = await post<{ id: string }>(request, '/roles', {
      name: `Achats sans coûts ${RUN}`,
      permissions: ['suppliers.supplier.view', 'stock.entry.view', 'catalog.article.view'],
    });
    const email = `achats-${RUN}@example.com`;
    const temporary = 'Provisoire-E2E-2026';
    await post(request, '/members', {
      email,
      full_name: `Achats ${RUN}`,
      password: temporary,
      roles: [{ role_id: role.id }],
      all_sites: true,
    });
    const first = await apiToken(request, email, temporary);
    const changed = await request.post('/api/v1/me/password', {
      headers: bearer(first),
      data: { current_password: temporary, new_password: PASSWORD },
    });
    expect(changed.status()).toBe(204);
    const token = await apiToken(request, email, PASSWORD);
    const summary = (await (
      await request.get(`/api/v1/stock/suppliers/${world.supplier}/summary`, {
        headers: bearer(token),
      })
    ).json()) as Record<string, unknown>;
    expect(summary.validated_count).toBe(2);
    expect(summary).not.toHaveProperty('received_total');
    const articles = (await (
      await request.get(`/api/v1/stock/suppliers/${world.supplier}/articles`, {
        headers: bearer(token),
      })
    ).json()) as { items: Record<string, unknown>[] };
    expect(articles.items[0]).not.toHaveProperty('last_unit_cost');

    await loginUi(page, email, PASSWORD, TENANT);
    await openSupplier(page);
    await expect(page.getByRole('group', { name: 'Réceptions validées' })).toBeVisible();
    await expect(page.getByText('Total reçu')).toHaveCount(0);
    await expect(page.getByRole('tab', { name: 'Chronologie' })).toHaveCount(0);
    await page.getByRole('tab', { name: 'Articles reçus' }).click();
    await expect(page.getByRole('row').filter({ hasText: SODA })).toBeVisible();
    await expect(page.getByRole('columnheader', { name: 'Dernier coût' })).toHaveCount(0);
    await expect(page.getByText(/F\s?CFA/)).toHaveCount(0);
  });

  test('6 — isolation : le fournisseur d’une autre entreprise est introuvable', async ({
    request,
  }) => {
    await provisionTenant(request, {
      name: OTHER_TENANT,
      profile: 'retail.alimentation',
      email: OTHER_EMAIL,
      password: PASSWORD,
    });
    const other = await tokenFor(request, OTHER_EMAIL, PASSWORD, OTHER_TENANT);
    for (const path of [
      `/stock/suppliers/${world.supplier}/summary`,
      `/stock/suppliers/${world.supplier}/articles`,
      `/suppliers/${world.supplier}/history`,
      `/suppliers/${world.supplier}`,
    ]) {
      const response = await request.get(`/api/v1${path}`, { headers: bearer(other) });
      expect(response.status(), path).toBe(404);
    }
    const search = (await (
      await request.get(`/api/v1/stock/entries?search=${encodeURIComponent(SUPPLIER)}`, {
        headers: bearer(other),
      })
    ).json()) as { total: number };
    expect(search.total).toBe(0);
  });

  test('fiche fournisseur sur mobile, sans débordement @mobile', async ({ page }) => {
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await openSupplier(page);
    const overflow = () =>
      page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    await expect(page.getByRole('group', { name: 'Réceptions validées' })).toBeVisible();
    expect(await overflow()).toBe(false);
    await page.getByRole('tab', { name: 'Articles reçus' }).click();
    await expect(page.getByRole('row').filter({ hasText: SODA })).toBeVisible();
    expect(await overflow()).toBe(false);
  });
});
