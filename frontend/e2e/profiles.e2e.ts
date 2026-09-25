import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import { bearer, loginUi, OWNER, provisionTenant, tokenFor } from './support';

/**
 * Phase 3.1 — Profils d'activité et profils UX. Chaque exécution crée, par la CLI TechNova,
 * une supérette (retail.alimentation) et un restaurant (restaurant.restaurant) appartenant au
 * même propriétaire, puis vérifie menu, tableau de bord, vocabulaire, thème et isolation.
 * La quincaillerie est l'entreprise E2E principale (retail.quincaillerie, voir README).
 */

const RUN = Date.now().toString().slice(-7);
const PASSWORD = 'E2e-Profils-2026';
const EMAIL = `e2e-profils-${RUN}@example.com`;
const SHOP = `Supérette E2E ${RUN}`;
const RESTO = `Maquis E2E ${RUN}`;

test.beforeAll(async ({ request }) => {
  await provisionTenant(request, {
    name: SHOP,
    profile: 'retail.alimentation',
    email: EMAIL,
    password: PASSWORD,
  });
  await provisionTenant(request, {
    name: RESTO,
    profile: 'restaurant.restaurant',
    email: EMAIL,
    password: PASSWORD,
  });
});

interface Capabilities {
  tenant: { name: string };
  profile: { code: string; sector: { code: string } | null };
  ux: { navigation: { group: string; modules: string[] }[]; upcoming: string[] };
}

async function capabilities(request: APIRequestContext, token: string) {
  const response = await request.get('/api/v1/me/capabilities', { headers: bearer(token) });
  expect(response.ok()).toBeTruthy();
  return (await response.json()) as Capabilities;
}

/** Titres des rubriques du menu, dans l'ordre (la rubrique d'accueil n'a pas de titre). */
async function menuGroups(page: Page) {
  return page.locator('.sm-sidebar .sm-nav-group-title').allTextContents();
}

async function openMenu(page: Page) {
  if ((page.viewportSize()?.width ?? 0) <= 800) {
    await page.getByRole('button', { name: 'Menu' }).first().click();
  }
}

const sidebar = (page: Page) => page.locator('.sm-sidebar');

test.describe('Profils d’activité', () => {
  test('Alimentation : menu caisse rapide, puis vente au point de vente', async ({
    page,
    request,
  }) => {
    const token = await tokenFor(request, EMAIL, PASSWORD, SHOP);
    const caps = await capabilities(request, token);
    expect(caps.profile.code).toBe('retail.alimentation');

    // Article et stock préparés par l'API (le test porte sur l'interface du profil).
    const post = async (path: string, data: unknown) => {
      const response = await request.post(`/api/v1${path}`, { headers: bearer(token), data });
      expect(response.status(), await response.text()).toBeLessThan(300);
      return (await response.json()) as { id: string };
    };
    const sites = (await (
      await request.get('/api/v1/sites', { headers: bearer(token) })
    ).json()) as { id: string }[];
    const category = await post('/catalog/categories', { name: `Épicerie ${RUN}` });
    const article = await post('/catalog/articles', {
      reference: `RIZ-${RUN}`,
      designation: `Riz parfumé ${RUN}`,
      category_id: category.id,
      unit: 'u',
      purchase_price: '9000',
      sale_price: '12000',
    });
    const entry = await post('/stock/entries', {
      site_id: sites[0]?.id,
      kind: 'INITIAL_STOCK',
      lines: [{ article_id: article.id, quantity: '10', unit_cost: '9000' }],
    });
    await post(`/stock/entries/${entry.id}/validate`, {});

    await loginUi(page, EMAIL, PASSWORD, SHOP);
    await expect(page.getByTestId('business-profile')).toHaveText('Alimentation / Supérette');
    await expect(page.locator('.sm-shell')).toHaveAttribute('data-accent', 'green');
    await openMenu(page);
    expect(await menuGroups(page)).toEqual([
      'Ventes et clients',
      'Caisse',
      'Catalogue',
      'Stock',
      'Administration',
    ]);
    // Point de vente en tête de la rubrique des ventes.
    const salesLinks = sidebar(page).locator('ul[aria-labelledby="nav-sales"] a');
    await expect(salesLinks.first()).toHaveText('Point de vente');
    await salesLinks.first().click();

    await expect(page.getByRole('heading', { name: 'Point de vente' })).toBeVisible();
    if (await page.getByText('Choisissez le site de vente pour commencer.').isVisible()) {
      await page.locator('.sm-pos-header .p-dropdown').click();
      await page.locator('.p-dropdown-panel li').first().click();
    }
    await page.getByLabel('Rechercher un article (F2)').fill(`Riz parfumé ${RUN}`);
    await page.getByRole('button', { name: `Ajouter Riz parfumé ${RUN} au panier` }).click();
    await page.keyboard.press('F8');
    const payment = page.getByRole('dialog', { name: 'Paiements (F8)' });
    await payment
      .getByRole('group', { name: 'Ajouter un moyen de paiement' })
      .getByRole('button', { name: 'Mobile Money' })
      .click();
    await payment.getByLabel('Montant du paiement 1').fill('12000');
    await payment.getByRole('button', { name: 'Appliquer' }).click();
    await page.keyboard.press('F10');
    await page
      .getByRole('dialog', { name: 'Valider la vente ?' })
      .getByRole('button', { name: 'Valider la vente (F10)' })
      .click();
    await expect(page.getByRole('dialog', { name: /Vente VTE-\d{6} enregistrée/ })).toBeVisible();
  });

  test('Restaurant : expérience dédiée, fonctionnalités futures jamais accessibles', async ({
    page,
    request,
  }) => {
    const caps = await capabilities(request, await tokenFor(request, EMAIL, PASSWORD, RESTO));
    expect(caps.profile.code).toBe('restaurant.restaurant');
    expect(caps.ux.navigation.map((g) => g.group)).not.toContain('restaurant');
    expect(caps.ux.upcoming).toEqual(
      expect.arrayContaining(['restaurant.tables', 'restaurant.kitchen']),
    );

    await loginUi(page, EMAIL, PASSWORD, RESTO);
    await expect(page.getByTestId('business-profile')).toHaveText('Restaurant');
    await expect(page.locator('.sm-shell')).toHaveAttribute('data-accent', 'orange');
    await openMenu(page);
    // Ventes et caisse d'abord ; les produits sont rangés dans « Stock ».
    expect(await menuGroups(page)).toEqual([
      'Ventes et clients',
      'Caisse',
      'Stock',
      'Administration',
    ]);
    const stockLinks = sidebar(page).locator('ul[aria-labelledby="nav-stock"] a');
    await expect(stockLinks.first()).toHaveText('Produits');
    const links = await sidebar(page).getByRole('link').allTextContents();
    expect(links.join(' | ')).not.toMatch(/Tables|Cuisine|Salle|Commandes|Menu/);

    // Annoncées « à venir », sans lien.
    const upcoming = page.getByRole('list', { name: 'À venir pour votre activité' });
    await expect(upcoming).toContainText('Tables');
    await expect(upcoming).toContainText('Cuisine');
    await expect(upcoming.getByRole('link')).toHaveCount(0);
    await expect(page.getByText('Produits en rupture')).toBeVisible();

    // Aucune route fictive : l'adresse d'un module planifié n'existe pas.
    await page.goto('/restaurant/tables');
    await expect(page.getByRole('main').getByText('Page introuvable')).toBeVisible();
  });

  test('Quincaillerie : catalogue et stock en tête, point de vente disponible', async ({
    page,
  }) => {
    await loginUi(page, OWNER.email, OWNER.password);
    await expect(page.getByTestId('business-profile')).toHaveText('Quincaillerie');
    await openMenu(page);
    expect((await menuGroups(page)).slice(0, 3)).toEqual([
      'Catalogue',
      'Stock',
      'Ventes et clients',
    ]);
    await sidebar(page).getByRole('link', { name: 'Stock par site' }).click();
    await expect(page.getByRole('heading', { name: 'Stock par site' })).toBeVisible();
    await openMenu(page);
    await sidebar(page).getByRole('link', { name: 'Point de vente', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Point de vente' })).toBeVisible();
  });

  test('isolation : chaque entreprise son profil, changement de contexte sans fuite', async ({
    page,
    request,
  }) => {
    const shopToken = await tokenFor(request, EMAIL, PASSWORD, SHOP);
    const restoToken = await tokenFor(request, EMAIL, PASSWORD, RESTO);
    expect((await capabilities(request, shopToken)).profile.code).toBe('retail.alimentation');
    expect((await capabilities(request, restoToken)).profile.code).toBe('restaurant.restaurant');
    // Le tenant vient du jeton : un identifiant d'une autre entreprise dans la requête est ignoré.
    const resto = (await (
      await request.get('/api/v1/tenant', { headers: bearer(restoToken) })
    ).json()) as { id: string };
    const refused = await request.put('/api/v1/tenant/business-profile', {
      headers: bearer(shopToken),
      data: { code: 'retail.alimentation', tenant_id: resto.id },
    });
    expect(refused.ok()).toBeTruthy();
    expect((await capabilities(request, restoToken)).profile.code).toBe('restaurant.restaurant');

    // Même compte, deux entreprises : le menu suit l'entreprise active.
    await loginUi(page, EMAIL, PASSWORD, SHOP);
    await expect(page.getByTestId('business-profile')).toHaveText('Alimentation / Supérette');
    await page.goto('/select-tenant');
    await page.getByRole('button', { name: RESTO }).click();
    await expect(page.getByTestId('business-profile')).toHaveText('Restaurant');
    await expect(page.locator('.sm-shell')).toHaveAttribute('data-accent', 'orange');
    await openMenu(page);
    await expect(sidebar(page).locator('ul[aria-labelledby="nav-stock"] a').first()).toHaveText(
      'Produits',
    );
  });

  for (const [tenant, label] of [
    [SHOP, 'Alimentation / Supérette'],
    [RESTO, 'Restaurant'],
  ] as const) {
    test(`${label} sur mobile : menu et tableau de bord sans débordement @mobile`, async ({
      page,
    }) => {
      const overflow = () =>
        page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
      await loginUi(page, EMAIL, PASSWORD, tenant);
      await expect(page.getByTestId('business-profile')).toHaveText(label);
      expect(await overflow()).toBe(false);
      await openMenu(page);
      await expect(
        sidebar(page).getByRole('link', { name: 'Point de vente', exact: true }),
      ).toBeVisible();
      expect(await overflow()).toBe(false);
    });
  }
});
