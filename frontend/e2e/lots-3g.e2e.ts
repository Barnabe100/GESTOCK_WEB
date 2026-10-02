import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import { bearer, loginUi, ownerSql, provisionTenant, tokenFor } from './support';

/**
 * Lot 3-G (ADR-0045), sur une entreprise créée pour l'exécution :
 * - fermeture P1-b : le suivi par lot n'est pas proposé dans la fiche article et l'API refuse
 *   son activation (la consommation des lots arrive avec le Lot 3-H) ;
 * - mécanisme RÉSERVÉ AUX TESTS : un article est marqué « suivi par lot » directement en base
 *   avec le rôle propriétaire (``ownerSql``, comme les autres données que seule l'administration
 *   peut fixer) — aucune voie de l'application ne le permet ;
 * - réception par lot (deux lots d'un même article, péremption passée acceptée), lot connu avec
 *   une autre péremption refusé, page Lots (états, filtres), fiche lot, fiche article, journal,
 *   annulation ; article non suivi sans champ de lot ; formulaire de réception sur mobile.
 */

const RUN = Date.now().toString().slice(-7);
const TENANT = `Lots E2E ${RUN}`;
const EMAIL = `lots-${RUN}@example.com`;
const PASSWORD = 'E2e-Lots-2026';
const MILK = `LAIT3G-${RUN}`;
const SUGAR = `SUCRE3G-${RUN}`;
const LOT_SOON = `LS-${RUN}`;
const LOT_PAST = `LP-${RUN}`;

const day = (offset: number) => {
  // Fuseau de l'entreprise de test : Afrique/Ouagadougou (UTC).
  const date = new Date();
  date.setUTCDate(date.getUTCDate() + offset);
  return date.toISOString().slice(0, 10);
};

interface World {
  token: string;
  site: { id: string; name: string };
  milk: string;
  sugar: string;
}

let world: World;

async function post<T = { id: string }>(
  request: APIRequestContext,
  path: string,
  data: unknown,
): Promise<T> {
  const response = await request.post(`/api/v1${path}`, { headers: bearer(world.token), data });
  expect(response.status(), await response.text()).toBeLessThan(300);
  return (await response.json()) as T;
}

async function get<T>(request: APIRequestContext, path: string): Promise<T> {
  const response = await request.get(`/api/v1${path}`, { headers: bearer(world.token) });
  expect(response.status(), await response.text()).toBe(200);
  return (await response.json()) as T;
}

async function choose(page: Page, inputId: string, label: string | RegExp) {
  await page.locator('.p-dropdown', { has: page.locator(`#${inputId}`) }).click();
  await page.locator('.p-dropdown-panel').last().getByRole('option', { name: label }).click();
}

async function addLine(page: Page, index: number, reference: string) {
  await page.getByRole('button', { name: 'Ajouter une ligne' }).click();
  await page.locator(`#line-${index}-article`).fill(reference);
  await page
    .getByRole('option', { name: new RegExp(reference) })
    .first()
    .click();
}

async function newEntry(page: Page) {
  await page.goto('/stock/entries/new');
  await expect(page.getByRole('heading', { name: 'Nouvelle entrée' })).toBeVisible();
  if (await page.locator('#doc-site').isVisible()) await choose(page, 'doc-site', world.site.name);
  await choose(page, 'doc-kind', 'Stock initial');
}

async function validate(page: Page) {
  await page.getByRole('button', { name: 'Valider', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Valider' }).click();
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
    name: string;
  }[];
  world = { token, site: sites[0] as { id: string; name: string } } as World;
  const category = (await post(request, '/catalog/categories', { name: `Frais 3G ${RUN}` })).id;
  const article = async (reference: string, designation: string) =>
    (
      await post(request, '/catalog/articles', {
        reference,
        designation,
        category_id: category,
        unit: 'brique',
        sale_price: '900',
      })
    ).id;
  world.milk = await article(MILK, `Lait UHT 3G ${RUN}`);
  world.sugar = await article(SUGAR, `Sucre 3G ${RUN}`);
  // Préparation RÉSERVÉE AUX TESTS (rôle propriétaire de la base) : article suivi par lot,
  // l'application refusant l'activation tant que le Lot 3-H n'est pas livré (P1-b).
  ownerSql(
    `UPDATE catalog_articles SET lot_tracked = true, expiry_tracked = true WHERE id = '${world.milk}'`,
  );
});

test.describe('Lots et péremption — Lot 3-G', () => {
  test('P1-b : suivi par lot ni proposé ni accepté', async ({ page, request }) => {
    expect(await get(request, '/catalog/lot-tracking')).toEqual({ available: false });
    const refused = await request.patch(`/api/v1/catalog/articles/${world.sugar}`, {
      headers: bearer(world.token),
      data: { lot_tracked: true, expiry_tracked: true },
    });
    expect(refused.status()).toBe(422);
    expect(((await refused.json()) as { code: string }).code).toBe('lot_tracking_unavailable');

    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto(`/catalog/articles/${world.sugar}`);
    await page.getByRole('button', { name: 'Modifier' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByTestId('lot-tracking-unavailable')).toBeVisible();
    await expect(dialog.getByLabel('Suivi par lot')).toHaveCount(0);

    // L'article préparé par le mécanisme réservé aux tests est bien suivi par lot.
    const milk = await get<{ lot_tracked: boolean }>(request, `/catalog/articles/${world.milk}`);
    expect(milk.lot_tracked).toBe(true);
  });

  test('réception : deux lots d’un article, péremption passée acceptée', async ({ page }) => {
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await newEntry(page);
    // Article non suivi : aucun champ de lot.
    await addLine(page, 0, SUGAR);
    await expect(page.locator('#line-0-lot')).toHaveCount(0);
    await page.locator('#line-0-quantity').fill('5');
    await page.locator('#line-0-cost').fill('400');
    // Article suivi : lot et péremption obligatoires, deux lignes pour deux lots.
    await addLine(page, 1, MILK);
    await expect(page.locator('#line-1-lot')).toBeVisible();
    await page.locator('#line-1-quantity').fill('24');
    await page.locator('#line-1-cost').fill('700');
    await page.getByRole('button', { name: 'Enregistrer le brouillon' }).click();
    await expect(page.getByText('Champ obligatoire').first()).toBeVisible();
    await page.locator('#line-1-lot').fill(LOT_SOON);
    await page.locator('#line-1-expiry').fill(day(10));
    await page.locator('#line-1-made').fill(day(-20));
    await addLine(page, 2, MILK);
    await page.locator('#line-2-quantity').fill('6');
    await page.locator('#line-2-cost').fill('700');
    await page.locator('#line-2-lot').fill(LOT_PAST);
    await page.locator('#line-2-expiry').fill(day(-1));
    await validate(page);
    await expect(page.getByText(/Document ENT-\d{6} validé/)).toBeVisible();
    const soon = page.getByRole('row').filter({ hasText: `Lot ${LOT_SOON}` });
    await expect(soon.getByText('Bientôt périmé')).toBeVisible();
    const past = page.getByRole('row').filter({ hasText: `Lot ${LOT_PAST}` });
    await expect(past.getByText('Périmé', { exact: true })).toBeVisible();
  });

  test('lot connu avec une autre date de péremption : refusé', async ({ page }) => {
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await newEntry(page);
    await addLine(page, 0, MILK);
    await page.locator('#line-0-quantity').fill('1');
    await page.locator('#line-0-cost').fill('700');
    await page.locator('#line-0-lot').fill(LOT_SOON.toLowerCase()); // casse indifférente
    await page.locator('#line-0-expiry').fill(day(11));
    await page.getByRole('button', { name: 'Enregistrer le brouillon' }).click();
    await expect(page.getByText(/existe déjà avec une autre date de péremption/)).toBeVisible();
  });

  test('page Lots, fiche lot, fiche article et journal', async ({ page, request }) => {
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto('/stock/lots');
    await expect(page.getByRole('heading', { name: 'Lots', exact: true })).toBeVisible();
    await expect(page.getByTestId('lots-threshold')).toContainText('30 jours');
    const rows = page.getByRole('row');
    await expect(
      rows.filter({ hasText: LOT_PAST }).getByText('Périmé', { exact: true }),
    ).toBeVisible();
    await expect(rows.filter({ hasText: LOT_SOON }).getByText('Bientôt périmé')).toBeVisible();
    // Échéance la plus proche d'abord.
    const order = await page.locator('tbody tr td:first-child').allTextContents();
    expect(order.indexOf(LOT_PAST)).toBeLessThan(order.indexOf(LOT_SOON));
    await choose(page, 'lots-state', /^Périmé$/);
    await expect(rows.filter({ hasText: LOT_SOON })).toHaveCount(0);
    await expect(rows.filter({ hasText: LOT_PAST })).toHaveCount(1);
    await choose(page, 'lots-state', 'Tous les états');

    await rows.filter({ hasText: LOT_SOON }).getByText(LOT_SOON).click();
    await expect(page.getByRole('heading', { name: `Lot ${LOT_SOON}` })).toBeVisible();
    const balances = page.getByRole('region', { name: 'Solde par site' });
    await expect(balances.getByText('24 brique')).toBeVisible();
    await expect(
      page.getByRole('region', { name: 'Réceptions du lot' }).getByText(/ENT-\d{6}/),
    ).toBeVisible();
    await expect(
      page.getByRole('region', { name: 'Mouvements du lot' }).getByText('+24 brique'),
    ).toBeVisible();

    await page.getByRole('button', { name: 'Fiche article' }).click();
    await expect(page.getByText('Par lot, avec date de péremption')).toBeVisible();
    const lots = page.getByRole('region', { name: 'Lots' });
    await expect(lots.getByText(LOT_SOON)).toBeVisible();
    await expect(lots.getByText(LOT_PAST)).toBeVisible();

    await page.goto(`/stock/movements?search=${MILK}`);
    await expect(page.getByText(new RegExp(`Lot ${LOT_SOON} · péremption`))).toBeVisible();

    // Invariant : Σ lots = stock du site.
    const level = await get<{ items: { quantity: string }[] }>(
      request,
      `/stock/levels?search=${MILK}&site_id=${world.site.id}`,
    );
    expect(level.items[0]?.quantity).toBe('30.000');
  });

  test('annulation de la réception : soldes des lots ramenés à zéro', async ({ page, request }) => {
    const lots = await get<{ items: { id: string; number: string }[] }>(
      request,
      `/stock/lots?search=${LOT_SOON}`,
    );
    const lot = lots.items.find((l) => l.number === LOT_SOON);
    const entries = await get<{ items: { id: string; number: string }[] }>(
      request,
      `/stock/entries?lot_id=${lot?.id}`,
    );
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto(`/stock/entries/${entries.items[0]?.id}`);
    await page.getByRole('button', { name: 'Annuler le document' }).first().click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel(/Motif/).fill('Erreur de saisie des lots');
    await dialog.getByRole('button', { name: 'Annuler le document' }).click();
    await expect(page.getByText(/annulé/).first()).toBeVisible();
    const after = await get<{ quantity: string }>(request, `/stock/lots/${lot?.id}`);
    expect(after.quantity).toBe('0.000');
  });

  test('réception par lot sur mobile, sans débordement @mobile', async ({ page }) => {
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await newEntry(page);
    await addLine(page, 0, MILK);
    await expect(page.locator('#line-0-lot')).toBeVisible();
    const overflow = () =>
      page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    expect(await overflow()).toBe(false);
    for (const id of ['#line-0-lot', '#line-0-expiry', '#line-0-made', '#line-0-quantity']) {
      const box = await page.locator(id).boundingBox();
      expect(box, id).toBeTruthy();
      expect((box?.x ?? 0) + (box?.width ?? 0), id).toBeLessThanOrEqual(390);
    }
    await page.goto('/stock/lots');
    await expect(page.getByRole('heading', { name: 'Lots', exact: true })).toBeVisible();
    expect(await overflow()).toBe(false);
  });
});
