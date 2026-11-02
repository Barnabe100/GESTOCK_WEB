import { expect, test, type Page } from '@playwright/test';

import { call, setupRestaurant, type Resto } from './restaurant-support';
import { loginUi } from './support';

/**
 * Navigation responsive (R8) : toutes les rubriques et TOUTES leurs entrées restent accessibles
 * quels que soient l'appareil, la taille et l'orientation de l'écran — panneau contextuel recalé
 * dans la hauteur visible (ordinateur), tiroir défilable (tablette, téléphone). Permissions
 * inchangées : le menu affiche ce que le serveur autorise.
 */

const PASSWORD = 'E2e-Navigation-2026';
let r: Resto;

test.describe.configure({ mode: 'serial' });

test.beforeAll(async ({ request }) => {
  // Restaurant aux nombreuses entrées : tous les modules de restauration activés sur le site.
  r = await setupRestaurant(request, 'Navigation', PASSWORD);
  for (const code of [
    'restaurant.tables',
    'restaurant.kitchen',
    'restaurant.qr',
    'restaurant.recipes',
  ]) {
    await call(request, r.token, 'put', `/sites/${r.site}/modules/${code}`, { enabled: true });
  }
});

const groupButtons = (page: Page) =>
  page.getByRole('navigation', { name: 'Menu' }).locator('button.sm-nav-main[aria-controls]');

const noHorizontalOverflow = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);

/** Ordinateur : chaque panneau s'ouvre ENTIÈREMENT dans la fenêtre, sans défilement de page. */
async function checkFlyouts(page: Page) {
  const buttons = groupButtons(page);
  const count = await buttons.count();
  expect(count).toBeGreaterThan(4);
  for (let i = 0; i < count; i += 1) {
    const button = buttons.nth(i);
    await button.click();
    const panelId = await button.getAttribute('aria-controls');
    const panel = page.locator(`[id="${panelId}"]`);
    await expect(panel).toBeVisible();
    await expect(panel).toBeInViewport({ ratio: 1 });
    const links = panel.getByRole('link');
    const total = await links.count();
    expect(total).toBeGreaterThan(0);
    for (let j = 0; j < total; j += 1) {
      await links.nth(j).scrollIntoViewIfNeeded();
      await expect(links.nth(j)).toBeInViewport({ ratio: 1 });
    }
    await button.click(); // referme (panneau épinglé)
    await expect(panel).toBeHidden();
  }
}

/** Tablette, téléphone : tiroir ; chaque rubrique dépliée, chaque entrée atteignable. */
async function checkDrawer(page: Page) {
  await page.getByRole('button', { name: 'Menu', exact: true }).click();
  const sidebar = page.locator('#app-sidebar');
  await expect(sidebar).toBeInViewport();
  const buttons = groupButtons(page);
  const count = await buttons.count();
  expect(count).toBeGreaterThan(4);
  for (let i = 0; i < count; i += 1) {
    const button = buttons.nth(i);
    if ((await button.getAttribute('aria-expanded')) !== 'true') await button.click();
    const panelId = await button.getAttribute('aria-controls');
    const links = page.locator(`[id="${panelId}"]`).getByRole('link');
    const total = await links.count();
    expect(total).toBeGreaterThan(0);
    for (let j = 0; j < total; j += 1) {
      await links.nth(j).scrollIntoViewIfNeeded();
      await expect(links.nth(j)).toBeInViewport({ ratio: 1 });
    }
  }
  // Le bas du tiroir (dernière entrée de la dernière rubrique) est cliquable.
  const last = page
    .locator(`[id="${await buttons.last().getAttribute('aria-controls')}"]`)
    .getByRole('link')
    .last();
  const href = await last.getAttribute('href');
  await last.scrollIntoViewIfNeeded();
  await last.click();
  await expect(page).toHaveURL(new RegExp(`${href}$`));
  expect(await noHorizontalOverflow(page)).toBe(true);
}

for (const [label, viewport] of [
  ['ordinateur', { width: 1440, height: 900 }],
  ['ordinateur, fenêtre basse', { width: 1280, height: 560 }],
] as const) {
  test(`${label} : panneaux contextuels entièrement visibles`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await loginUi(page, r.email, PASSWORD, r.name);
    await checkFlyouts(page);
    // Mode compact : mêmes panneaux, mêmes entrées.
    await page.getByRole('button', { name: 'Réduire le menu' }).click();
    await checkFlyouts(page);
    await page.getByRole('button', { name: 'Développer le menu' }).click();
  });
}

for (const [label, viewport] of [
  ['tablette portrait', { width: 768, height: 1024 }],
  ['tablette paysage', { width: 1024, height: 768 }],
] as const) {
  test(`${label} : tiroir défilable, toutes les entrées atteignables`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await loginUi(page, r.email, PASSWORD, r.name);
    await checkDrawer(page);
  });
}

test('@mobile téléphone portrait : tiroir défilable, toutes les entrées atteignables', async ({
  page,
}) => {
  await loginUi(page, r.email, PASSWORD, r.name);
  await checkDrawer(page);
});

test('@mobile téléphone paysage : écran bas, menu toujours complet', async ({ page }) => {
  await page.setViewportSize({ width: 844, height: 390 });
  await loginUi(page, r.email, PASSWORD, r.name);
  await checkDrawer(page);
});
