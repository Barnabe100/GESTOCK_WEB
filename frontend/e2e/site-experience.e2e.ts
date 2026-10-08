import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import { bearer, createActiveSite, loginUi, provisionTenant, siteIdsOf, tokenFor } from './support';

/**
 * Palier E — Profil, modules, navigation, tableau de bord et thème du SITE actif, sur une
 * entreprise dédiée à deux sites de profils différents (Quincaillerie / Entrepôt).
 */

const PASSWORD = 'E2e-SiteUx-2026';

const overflow = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);

async function setup(request: APIRequestContext, prefix: string) {
  const stamp = Date.now().toString().slice(-7);
  const name = `Site Ux ${prefix} ${stamp}`;
  const email = `site-ux-${prefix}-${stamp}@example.com`;
  await provisionTenant(request, {
    name,
    profile: 'retail.quincaillerie',
    email,
    password: PASSWORD,
  });
  const token = await tokenFor(request, email, PASSWORD, name);
  await createActiveSite(request, token, {
    name: 'Dépôt Central',
    code: 'DEPOT-UX',
    kind: 'warehouse',
    business_profile_code: 'distribution.entrepot',
  });
  const sites = (await (await request.get('/api/v1/sites', { headers: bearer(token) })).json()) as {
    name: string;
    business_profile_code: string;
  }[];
  expect(sites.map((s) => s.business_profile_code).sort()).toEqual([
    'distribution.entrepot',
    'retail.quincaillerie',
  ]);
  return { name, email, token };
}

async function selectSite(page: Page, label: string) {
  await page.locator('.p-dropdown', { has: page.locator('#site-selector') }).click();
  await page.getByRole('option', { name: new RegExp(`^${label}`) }).click();
}

test('changer de site change profil, menu, thème, tableau de bord et modules', async ({
  page,
  request,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const w = await setup(request, 'd');
  await loginUi(page, w.email, PASSWORD, w.name);
  const shell = page.locator('.sm-shell');

  await selectSite(page, 'Site principal');
  await expect(page.getByTestId('business-profile')).toHaveText('Quincaillerie');
  await expect(page.getByTestId('business-profile-scope')).toHaveText(
    'Profil du site Site principal',
  );
  await expect(page.getByRole('link', { name: 'Point de vente' })).toBeVisible();
  const storeAccent = await shell.getAttribute('data-accent');
  await expect(page.getByTestId('site-hero')).toContainText('Site principal');

  await selectSite(page, 'Dépôt Central');
  await expect(page.getByTestId('business-profile')).toHaveText('Entrepôt');
  await expect(page.getByRole('link', { name: 'Point de vente' })).toHaveCount(0);
  expect(await shell.getAttribute('data-accent')).not.toBe(storeAccent);
  await expect(page.getByTestId('site-hero')).toContainText('Dépôt Central');
  await expect(page.getByTestId('site-hero-profile')).toHaveText('Entrepôt');

  // Route d'un module absent du site actif : message explicite, jamais un écran blanc.
  await page.goto('/pos');
  await expect(page.getByText('Fonction non disponible sur ce site')).toBeVisible();

  // Modules du site : le point de vente n'est pas proposé par le profil Entrepôt.
  await page.goto('/organization/modules');
  await expect(page.getByTestId('site-profile')).toHaveText(
    'Dépôt Central — profil du site : Entrepôt',
  );
  const pos = page.getByRole('row', { name: /Point de vente/ });
  await expect(pos.getByText('Non proposé')).toBeVisible();
  await expect(pos.getByRole('switch')).toHaveCount(0);

  // Sites : profil et modules de chaque site.
  await page.goto('/organization/sites');
  await expect(page.getByRole('row', { name: /Dépôt Central/ })).toContainText('Entrepôt');
  await expect(page.getByTestId('site-modules-DEPOT-UX')).toContainText(/modules? actifs?/);
  expect(await overflow(page)).toBe(false);
});

test('palier G : les données suivent le site actif, jamais celles de l’autre site', async ({
  page,
  request,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const w = await setup(request, 'g');
  const headers = bearer(w.token);
  const post = async (path: string, data: unknown) => {
    const response = await request.post(`/api/v1${path}`, { headers, data });
    expect(response.ok(), await response.text()).toBeTruthy();
    return (await response.json()) as { id: string };
  };
  // Référence sans chiffre : la quantité vérifiée (37) ne peut pas apparaître ailleurs.
  const reference = `G-${Date.now()
    .toString()
    .slice(-7)
    .replace(/\d/g, (d) => 'ABCDEFGHIJ'.charAt(Number(d)))}`;
  const category = await post('/catalog/categories', { name: `Cat ${reference}` });
  const article = await post('/catalog/articles', {
    reference,
    designation: `Article ${reference}`,
    category_id: category.id,
    unit: 'u',
    purchase_price: '100',
    sale_price: '150',
    site_ids: await siteIdsOf(request, w.token),
  });
  const supplier = await post('/suppliers', { name: `Fournisseur ${reference}` });
  const sites = (await (await request.get('/api/v1/sites', { headers })).json()) as {
    id: string;
    code: string;
  }[];
  const shop = sites.find((s) => s.code !== 'DEPOT-UX') as { id: string };
  const entry = await post('/stock/entries', {
    site_id: shop.id,
    supplier_id: supplier.id,
    lines: [{ article_id: article.id, quantity: '37', unit_cost: '100' }],
  });
  await post(`/stock/entries/${entry.id}/validate`, {});

  await loginUi(page, w.email, PASSWORD, w.name);
  await selectSite(page, 'Site principal');
  await page.goto('/stock/levels');
  await page.getByRole('searchbox').first().fill(reference);
  const row = page.getByRole('row').filter({ hasText: reference });
  await expect(row).toContainText('37 u');

  // Changement de site : le stock du site principal n'est jamais affiché pour le dépôt.
  await selectSite(page, 'Dépôt Central');
  await expect(page.getByTestId('business-profile')).toHaveText('Entrepôt');
  await page.getByRole('searchbox').first().fill(reference);
  const depot = page.getByRole('row').filter({ hasText: reference });
  await expect(depot).toContainText('Non stocké');
  await expect(depot).not.toContainText('37');

  // Retour au site principal : sa donnée revient.
  await selectSite(page, 'Site principal');
  await page.getByRole('searchbox').first().fill(reference);
  await expect(page.getByRole('row').filter({ hasText: reference })).toContainText('37 u');
});

test('expérience du site sur mobile, sans débordement @mobile', async ({ page, request }) => {
  const w = await setup(request, 'm');
  await loginUi(page, w.email, PASSWORD, w.name);
  await expect(page.getByTestId('site-hero')).toBeVisible();
  expect(await overflow(page)).toBe(false);
  for (const path of ['/organization/sites', '/organization/modules']) {
    await page.goto(path);
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    expect(await overflow(page), path).toBe(false);
  }
});
