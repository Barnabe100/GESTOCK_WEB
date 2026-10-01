import { expect, test, type APIRequestContext, type Locator, type Page } from '@playwright/test';

import { apiToken, bearer, createActiveSite, loginUi, provisionTenant, tokenFor } from './support';

/**
 * Lot 3-F (ADR-0044), sur une entreprise créée pour l'exécution (boutique + dépôt) : emplacements
 * créés par l'interface (site choisi, nom déjà pris refusé, même nom sur un autre site
 * accepté) ; affectation depuis les niveaux de stock (colonne, « Non rangé », filtre) ;
 * plusieurs articles au même emplacement ; inventaire trié par emplacement ; entrée affichant
 * l'emplacement courant ; fiche article par site, transfert sans copie de l'emplacement ;
 * désactivation (affectation conservée, plus affectable) ; membre limité à la boutique ;
 * isolation entre entreprises ; affichage mobile.
 */

const RUN = Date.now().toString().slice(-7);
const TENANT = `Emplacements E2E ${RUN}`;
const EMAIL = `emplacements-${RUN}@example.com`;
const PASSWORD = 'E2e-Emplacements-2026';
const OTHER_TENANT = `Autre emplacements E2E ${RUN}`;
const OTHER_EMAIL = `autre-emplacements-${RUN}@example.com`;
const DEPOT = { name: `Dépôt 3F ${RUN}`, code: `D3F${RUN.slice(-4)}` };
const SODA = `SODA3F-${RUN}`;
const RICE = `RIZ3F-${RUN}`;
const OIL = `HUILE3F-${RUN}`;
const RAYON = `Rayon boissons ${RUN}`;
const RESERVE = `Réserve ${RUN}`;
const PALETTES = `Zone palettes ${RUN}`;

interface World {
  token: string;
  main: { id: string; name: string };
  depot: { id: string; name: string };
  soda: string;
  rice: string;
  oil: string;
  entry: string;
}

let world: World;

async function post<T = { id: string }>(
  request: APIRequestContext,
  path: string,
  data: unknown,
  token = world.token,
): Promise<T> {
  const response = await request.post(`/api/v1${path}`, { headers: bearer(token), data });
  expect(response.status(), await response.text()).toBeLessThan(300);
  return (await response.json()) as T;
}

async function locationId(request: APIRequestContext, name: string): Promise<string> {
  const page = (await (
    await request.get(`/api/v1/stock/locations?search=${encodeURIComponent(name)}`, {
      headers: bearer(world.token),
    })
  ).json()) as { items: { id: string; name: string }[] };
  const found = page.items.find((l) => l.name === name);
  expect(found, name).toBeTruthy();
  return found?.id ?? '';
}

/** Liste déroulante PrimeReact d'un champ (``inputId`` ou libellé) dans un conteneur. */
function dropdown(page: Page, scope: Locator, field: string) {
  const input = field.startsWith('#')
    ? page.locator(field)
    : page.getByLabel(field, { exact: true });
  return scope.locator('.p-dropdown', { has: input }).first();
}

async function choose(page: Page, scope: Locator, field: string, option: string | RegExp) {
  await dropdown(page, scope, field).click();
  await page.locator('.p-dropdown-panel').last().getByRole('option', { name: option }).click();
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
    code: string;
  }[];
  world = { token, main: sites[0] as { id: string; name: string } } as World;
  world.depot =
    sites.find((s) => s.code === DEPOT.code) ??
    (await createActiveSite<{ id: string; name: string }>(request, token, {
      ...DEPOT,
      kind: 'warehouse',
    }));
  const category = (await post(request, '/catalog/categories', { name: `Épicerie 3F ${RUN}` })).id;
  const article = async (reference: string, designation: string) =>
    (
      await post(request, '/catalog/articles', {
        reference,
        designation,
        category_id: category,
        unit: 'u',
        sale_price: '500',
      })
    ).id;
  world.soda = await article(SODA, `Soda 3F ${RUN}`);
  world.rice = await article(RICE, `Riz 3F ${RUN}`);
  world.oil = await article(OIL, `Huile 3F ${RUN}`);
  const entry = await post(request, '/stock/entries', {
    site_id: world.main.id,
    kind: 'INITIAL_STOCK',
    lines: [
      { article_id: world.soda, quantity: '50', unit_cost: '300' },
      { article_id: world.rice, quantity: '20', unit_cost: '300' },
      { article_id: world.oil, quantity: '10', unit_cost: '300' },
    ],
  });
  await post(request, `/stock/entries/${entry.id}/validate`, {});
  world.entry = entry.id;
});

test.describe('Emplacements par site — Lot 3-F', () => {
  test('1 — création par site : nom déjà pris refusé, même nom sur un autre site accepté', async ({
    page,
    request,
  }) => {
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto('/stock/locations');
    await expect(page.getByRole('heading', { name: 'Emplacements' })).toBeVisible();
    for (const name of [RAYON, RESERVE, RAYON.toUpperCase()]) {
      await page.getByRole('button', { name: 'Nouvel emplacement' }).first().click();
      const dialog = page.getByRole('dialog');
      await choose(page, dialog, '#location-site', world.main.name);
      await dialog.getByLabel(/Nom de l'emplacement/).fill(name);
      await dialog.getByRole('button', { name: 'Enregistrer' }).click();
      if (name === RAYON.toUpperCase()) {
        await expect(
          page.getByText('Un emplacement de ce nom existe déjà sur ce site.'),
        ).toBeVisible();
        await dialog.getByRole('button', { name: 'Annuler' }).click();
      } else {
        await expect(dialog).toBeHidden();
      }
    }
    await expect(page.getByRole('cell', { name: RAYON, exact: true })).toBeVisible();
    // Le même nom sur le dépôt : un autre emplacement.
    await post(request, '/stock/locations', { site_id: world.depot.id, name: RAYON });
    await post(request, '/stock/locations', { site_id: world.depot.id, name: PALETTES });
    await page.reload();
    await page.getByRole('searchbox').fill(RAYON);
    await expect(page.getByRole('row').filter({ hasText: RAYON })).toHaveCount(2);
  });

  test('2 — niveaux : affectation, plusieurs articles au même emplacement, filtre « Non rangés »', async ({
    page,
  }) => {
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto('/stock/levels');
    await page.getByRole('searchbox').fill(`3F-${RUN}`);
    for (const reference of [SODA, RICE]) {
      const row = page
        .getByRole('row')
        .filter({ hasText: reference })
        .filter({ hasText: world.main.name });
      await expect(row.getByText('Non rangé')).toBeVisible();
      await row.getByRole('button', { name: 'Emplacement' }).click();
      const dialog = page.getByRole('dialog');
      await choose(page, dialog, '#assign-location', RAYON);
      await dialog.getByRole('button', { name: 'Enregistrer' }).click();
      await expect(dialog).toBeHidden();
      await expect(row.getByText(RAYON)).toBeVisible();
    }
    await choose(page, page.getByRole('search'), 'Emplacement', 'Non rangés');
    const rows = page.getByRole('row').filter({ hasText: world.main.name });
    await expect(rows.filter({ hasText: OIL })).toBeVisible();
    await expect(rows.filter({ hasText: SODA })).toHaveCount(0);
  });

  test('3 — inventaire trié par emplacement ; entrée affichant l’emplacement courant', async ({
    page,
    request,
  }) => {
    const inventory = await post(request, '/inventories', {
      site_id: world.main.id,
      inventory_type: 'TARGETED',
      article_ids: [world.soda, world.rice, world.oil],
    });
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto(`/inventories/${inventory.id}`);
    await page.getByRole('columnheader', { name: /Emplacement/ }).click();
    const rows = page.locator('tbody tr');
    await expect(rows.first()).toContainText(RAYON);
    await expect(rows.last()).toContainText('Non rangé');
    await expect(rows.last()).toContainText(OIL);
    await page.goto(`/stock/entries/${world.entry}`);
    const soda = page.getByRole('row').filter({ hasText: SODA });
    await expect(soda.getByText(RAYON)).toBeVisible();
    await expect(
      page.getByRole('row').filter({ hasText: OIL }).getByText('Non rangé'),
    ).toBeVisible();
  });

  test('4 — fiche article par site ; transfert sans copie de l’emplacement', async ({
    page,
    request,
  }) => {
    const transfer = await post(request, '/stock/transfers', {
      source_site_id: world.main.id,
      destination_site_id: world.depot.id,
      lines: [{ article_id: world.soda, quantity: '5' }],
    });
    await post(request, `/stock/transfers/${transfer.id}/validate`, {});
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto(`/catalog/articles/${world.soda}`);
    const section = page.getByRole('region', { name: 'Stock et emplacement par site' });
    const depot = section.getByRole('row').filter({ hasText: world.depot.name });
    await expect(depot.getByText('Non rangé')).toBeVisible(); // jamais recopié
    await expect(
      section.getByRole('row').filter({ hasText: world.main.name }).getByText(RAYON),
    ).toBeVisible();
    await depot.getByRole('button', { name: 'Emplacement' }).click();
    const dialog = page.getByRole('dialog');
    // Seuls les emplacements du DÉPÔT sont proposés.
    await dropdown(page, dialog, '#assign-location').click();
    const panel = page.locator('.p-dropdown-panel').last();
    await expect(panel.getByRole('option', { name: RESERVE })).toHaveCount(0);
    await panel.getByRole('option', { name: PALETTES }).click();
    await dialog.getByRole('button', { name: 'Enregistrer' }).click();
    await expect(depot.getByText(PALETTES)).toBeVisible();
  });

  test('5 — désactivation : affectation conservée, plus affectable', async ({ page, request }) => {
    const reserve = await locationId(request, RESERVE);
    const level = (articleId: string) =>
      request.put(`/api/v1/stock/levels/${world.main.id}/${articleId}/location`, {
        headers: bearer(world.token),
        data: { location_id: reserve },
      });
    expect((await level(world.oil)).status()).toBe(200); // huile rangée en réserve
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto('/stock/locations');
    await page.getByRole('searchbox').fill(RESERVE);
    const row = page.getByRole('row').filter({ hasText: RESERVE });
    await row.getByRole('button', { name: 'Désactiver' }).click();
    await page
      .getByRole('dialog', { name: "Désactiver l'emplacement" })
      .getByRole('button', { name: 'Désactiver' })
      .click();
    await expect(row.getByText('Inactif')).toBeVisible();
    // Nouvelle affectation refusée…
    const refused = await level(world.rice);
    expect(refused.status()).toBe(422);
    expect(((await refused.json()) as { code: string }).code).toBe('stock_location_inactive');
    // … mais l'huile conserve son emplacement, signalé inactif.
    await page.goto('/stock/levels');
    await page.getByRole('searchbox').fill(OIL);
    const oil = page.getByRole('row').filter({ hasText: OIL }).filter({ hasText: world.main.name });
    await expect(oil.getByText(RESERVE)).toBeVisible();
    await expect(oil.getByText('Inactif')).toBeVisible();
  });

  test('6 — membre limité à la boutique ; isolation entre entreprises', async ({ request }) => {
    const roles = (await (
      await request.get('/api/v1/roles', { headers: bearer(world.token) })
    ).json()) as { id: string; template_code: string | null }[];
    const manager = roles.find((r) => r.template_code === 'manager');
    const email = `magasinier-3f-${RUN}@example.com`;
    const temporary = 'Provisoire-E2E-2026';
    await post(request, '/members', {
      email,
      full_name: `Magasinier ${RUN}`,
      password: temporary,
      roles: [{ role_id: manager?.id }],
      site_ids: [world.main.id],
    });
    const first = await apiToken(request, email, temporary);
    await request.post('/api/v1/me/password', {
      headers: bearer(first),
      data: { current_password: temporary, new_password: PASSWORD },
    });
    const member = await apiToken(request, email, PASSWORD);
    const visible = (await (
      await request.get(`/api/v1/stock/locations?search=${RUN}`, { headers: bearer(member) })
    ).json()) as { items: { site_id: string }[] };
    expect(visible.items.length).toBeGreaterThan(0);
    expect(visible.items.every((l) => l.site_id === world.main.id)).toBe(true);
    const palettes = await locationId(request, PALETTES);
    const rename = await request.patch(`/api/v1/stock/locations/${palettes}`, {
      headers: bearer(member),
      data: { name: 'Piratage' },
    });
    expect(rename.status()).toBe(404);
    const assign = await request.put(
      `/api/v1/stock/levels/${world.depot.id}/${world.rice}/location`,
      {
        headers: bearer(member),
        data: { location_id: palettes },
      },
    );
    expect(assign.status()).toBe(403);

    await provisionTenant(request, {
      name: OTHER_TENANT,
      profile: 'retail.alimentation',
      email: OTHER_EMAIL,
      password: PASSWORD,
    });
    const other = await tokenFor(request, OTHER_EMAIL, PASSWORD, OTHER_TENANT);
    const list = (await (
      await request.get(`/api/v1/stock/locations?search=${RUN}`, { headers: bearer(other) })
    ).json()) as { total: number };
    expect(list.total).toBe(0);
    const foreign = await request.patch(`/api/v1/stock/locations/${palettes}`, {
      headers: bearer(other),
      data: { name: 'Piratage' },
    });
    expect(foreign.status()).toBe(404);
  });

  test('emplacements sur mobile, sans débordement @mobile', async ({ page }) => {
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto('/stock/locations');
    await expect(page.getByRole('heading', { name: 'Emplacements' })).toBeVisible();
    const overflow = () =>
      page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    expect(await overflow()).toBe(false);
    await page.getByRole('button', { name: 'Nouvel emplacement' }).first().click();
    await expect(page.getByRole('dialog').getByLabel(/Nom de l'emplacement/)).toBeVisible();
    expect(await overflow()).toBe(false);
  });
});
