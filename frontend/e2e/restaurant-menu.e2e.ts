import { expect, test, type APIRequestContext, type Locator, type Page } from '@playwright/test';

import { bearer, loginUi, OWNER, provisionTenant, siteIdsOf, tokenFor } from './support';

/**
 * Palier R1 (ADR-0049) — menu des sites de restauration : activation explicite sur un site
 * existant, sections et présentations du catalogue (unité de base, conditionnement ; sans prix :
 * non proposé), unicité, « épuisé » vu d'un autre onglet en 15 s au plus, aucun menu pour un
 * commerce. Entreprise dédiée (maquis), créée à chaque exécution.
 */

const RUN = Date.now().toString().slice(-7);
const PASSWORD = 'E2e-Menu-2026';
const EMAIL = `e2e-menu-${RUN}@example.com`;
const NAME = `Maquis Menu ${RUN}`;
// Référence sans chiffre : jamais confondue avec un prix ou une quantité affichés.
const REFERENCE = `MENU-${RUN.replace(/\d/g, (d) => 'ABCDEFGHIJ'.charAt(Number(d)))}`;
const DESIGNATION = `Bière ${REFERENCE}`;

const world = { token: '', site: '', article: '' };

async function api(
  request: APIRequestContext,
  method: 'post' | 'put' | 'get',
  path: string,
  data?: unknown,
) {
  const response = await request[method](`/api/v1${path}`, {
    headers: bearer(world.token),
    ...(data === undefined ? {} : { data }),
  });
  expect(response.status(), await response.text()).toBeLessThan(300);
  return response.status() === 204 ? null : ((await response.json()) as { id: string });
}

test.describe.configure({ mode: 'serial' });

test.beforeAll(async ({ request }) => {
  await provisionTenant(request, {
    name: NAME,
    profile: 'restaurant.maquis',
    email: EMAIL,
    password: PASSWORD,
  });
  world.token = await tokenFor(request, EMAIL, PASSWORD, NAME);
  [world.site] = await siteIdsOf(request, world.token);
  const category = await api(request, 'post', '/catalog/categories', { name: `Boissons ${RUN}` });
  const article = await api(request, 'post', '/catalog/articles', {
    reference: REFERENCE,
    designation: DESIGNATION,
    category_id: category?.id,
    unit: 'bouteille',
    purchase_price: '400',
    sale_price: '700',
    site_ids: [world.site],
  });
  world.article = article?.id ?? '';
  await api(request, 'post', `/catalog/articles/${article?.id}/packagings`, {
    name: 'Casier 12',
    conversion: '12',
    sale_price: '8000',
  });
  await api(request, 'post', `/catalog/articles/${article?.id}/packagings`, {
    name: 'Pack 6',
    conversion: '6',
    sale_price: null,
  });
});

/** Liste déroulante PrimeReact d'un champ (identifiant de l'entrée) dans un conteneur. */
async function choose(page: Page, scope: Locator, inputId: string, option: string | RegExp) {
  await scope.locator('.p-dropdown', { has: page.locator(`#${inputId}`) }).click();
  await page.locator('.p-dropdown-panel').last().getByRole('option', { name: option }).click();
}

const itemRow = (page: Page, label: string | RegExp) =>
  page.getByRole('row', { name: label }).first();

test('site existant : le menu reste désactivé jusqu’à son activation explicite', async ({
  page,
  request,
}) => {
  // État d'un site existant à la livraison (migration 0041) : menu désactivé.
  await api(request, 'put', `/sites/${world.site}/modules/restaurant.menu`, { enabled: false });
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginUi(page, EMAIL, PASSWORD, NAME);
  await expect(page.locator('.sm-sidebar').getByRole('link', { name: 'Menu' })).toHaveCount(0);
  await page.goto('/organization/modules');
  const row = page.getByRole('row', { name: /^Menu/ });
  await expect(row.getByRole('switch')).toBeVisible();
  await row.getByRole('switch').click();
  await expect(row.getByTestId('module-state-active')).toBeVisible();
  // Les commandes restent « à venir » : jamais activables (module non livré).
  await expect(page.getByRole('row', { name: /^Commandes/ }).getByRole('switch')).toHaveCount(0);
  await page.reload();
  await expect(page.locator('.sm-sidebar').getByRole('link', { name: 'Menu' })).toBeVisible();
});

test('sections et présentations : unité de base, conditionnement ; sans prix non proposé', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginUi(page, EMAIL, PASSWORD, NAME);
  await page.goto('/restaurant/menu');
  await expect(page.getByRole('heading', { name: 'Menu', exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'Nouvelle section' }).click();
  const sectionDialog = page.getByRole('dialog');
  await sectionDialog.getByLabel(/Nom de la section/).fill('Boissons');
  await sectionDialog.getByRole('button', { name: 'Enregistrer' }).click();
  await expect(sectionDialog).toBeHidden();

  const addItem = async (presentation: string | RegExp | null) => {
    await page.getByRole('button', { name: 'Ajouter au menu' }).first().click();
    const dialog = page.getByRole('dialog');
    await dialog.locator('#menu-item-article').fill(REFERENCE);
    await page
      .getByRole('option', { name: new RegExp(REFERENCE) })
      .first()
      .click();
    if (presentation) await choose(page, dialog, 'menu-item-presentation', presentation);
    await choose(page, dialog, 'menu-item-section', 'Boissons');
    await dialog.getByRole('button', { name: 'Enregistrer' }).click();
    return dialog;
  };

  // Conditionnement sans prix : affiché, jamais sélectionnable.
  await page.getByRole('button', { name: 'Ajouter au menu' }).first().click();
  const probe = page.getByRole('dialog');
  await probe.locator('#menu-item-article').fill(REFERENCE);
  await page
    .getByRole('option', { name: new RegExp(REFERENCE) })
    .first()
    .click();
  await probe.locator('.p-dropdown', { has: page.locator('#menu-item-presentation') }).click();
  await expect(
    page
      .locator('.p-dropdown-panel')
      .last()
      .getByRole('option', { name: /Pack 6/ }),
  ).toHaveAttribute('data-p-disabled', 'true');
  // Refermer la liste seule (Échap fermerait aussi la boîte de dialogue).
  await probe.locator('.p-dropdown', { has: page.locator('#menu-item-presentation') }).click();
  await expect(page.locator('.p-dropdown-panel')).toHaveCount(0);
  await probe.getByRole('button', { name: 'Annuler' }).click();

  await expect(await addItem(null)).toBeHidden();
  await expect(itemRow(page, new RegExp(`^${DESIGNATION}`))).toContainText('700');
  await expect(await addItem(/Casier 12/)).toBeHidden();
  await expect(itemRow(page, /Casier 12/)).toContainText('8');
  // Une présentation au plus une fois au menu du site : refus du serveur, traduit.
  const duplicate = await addItem(null);
  await expect(page.getByText('Cette présentation figure déjà au menu de ce site.')).toBeVisible();
  await duplicate.getByRole('button', { name: 'Annuler' }).click();
});

test('« épuisé » vu depuis un autre poste en 15 s au plus, sans recharger', async ({
  page,
  browser,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginUi(page, EMAIL, PASSWORD, NAME);
  await page.goto('/restaurant/menu');
  // Autre poste : session distincte, connectée séparément.
  const other = await browser.newContext();
  const watcher = await other.newPage();
  await watcher.setViewportSize({ width: 1440, height: 900 });
  await loginUi(watcher, EMAIL, PASSWORD, NAME);
  await watcher.goto('/restaurant/menu');
  const watched = itemRow(watcher, new RegExp(`^${DESIGNATION}(?! —)`));
  await expect(watched).toContainText('Disponible');

  const row = itemRow(page, new RegExp(`^${DESIGNATION}(?! —)`));
  await row.getByRole('button', { name: 'Marquer épuisé' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel(/Motif/).fill('Plus de glace');
  await dialog.getByRole('button', { name: 'Marquer épuisé' }).click();
  await expect(row).toContainText('Épuisé');

  await expect(watched).toContainText('Épuisé', { timeout: 16_000 });
  await expect(watched).toContainText('Plus de glace');
  await expect(watched).toContainText('Non commandable');
  await other.close();
});

test('mobile : menu lisible et « épuisé » réversible @mobile', async ({ page, request }) => {
  // Projet mobile : entreprise propre à ce projet, menu préparé par l'API.
  const section = await api(request, 'post', '/restaurant/menu/sections', {
    site_id: world.site,
    name: 'Boissons',
  });
  await api(request, 'post', '/restaurant/menu/items', {
    site_id: world.site,
    section_id: section?.id,
    article_id: world.article,
  });
  await loginUi(page, EMAIL, PASSWORD, NAME);
  await page.goto('/restaurant/menu');
  const row = itemRow(page, new RegExp(`^${DESIGNATION}(?! —)`));
  await expect(row).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  );
  expect(overflow).toBe(false);
  const action = row.getByRole('button', { name: /Remettre disponible|Marquer épuisé/ });
  await expect(action).toBeVisible();
});

test('commerce : aucun menu proposé ni servi', async ({ request }) => {
  const token = await tokenFor(request, OWNER.email, OWNER.password, OWNER.tenant);
  const caps = (await (
    await request.get('/api/v1/me/capabilities', { headers: bearer(token) })
  ).json()) as { navigation: string[] };
  expect(caps.navigation).not.toContain('restaurant.menu');
  const refused = await request.get('/api/v1/restaurant/menu/sections', { headers: bearer(token) });
  expect(refused.status()).toBe(403);
  expect(((await refused.json()) as { code: string }).code).toBe('module_unavailable');
});
