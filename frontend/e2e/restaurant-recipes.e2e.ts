import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import { call, createOrder, setupRestaurant, type Resto } from './restaurant-support';
import { assortArticles, loginUi } from './support';

/**
 * Palier R8 (ADR-0053) — recettes, parcours réels : création d'une fiche par l'interface (plat
 * non géré en stock, ingrédients gérés), coût de revient et portions réalisables par site, vente
 * qui déduit les ingrédients (une seule fois), plat bloqué au menu faute d'ingrédients, fiche
 * technique depuis l'écran cuisine ; ordinateur et mobile.
 */

const PASSWORD = 'E2e-Recettes-2026';
let r: Resto;
let dish: { id: string; designation: string; reference: string };
let rice: string;
let egg: string;
let menuItem: string;

test.describe.configure({ mode: 'serial' });

async function article(
  request: APIRequestContext,
  category: string,
  body: Record<string, unknown>,
): Promise<{ id: string; designation: string; reference: string }> {
  const created = await call<{ id: string; designation: string; reference: string }>(
    request,
    r.token,
    'post',
    '/catalog/articles',
    { category_id: category, sale_price: '0', ...body },
  );
  await assortArticles(request, r.token, [created.id]);
  return created;
}

async function units(page: Page) {
  await page.getByRole('tab', { name: 'Disponibilité' }).click();
  return page.getByTestId('recipe-units');
}

test.beforeAll(async ({ request }) => {
  r = await setupRestaurant(request, 'Recettes', PASSWORD);
  for (const code of ['restaurant.kitchen', 'restaurant.recipes']) {
    await call(request, r.token, 'put', `/sites/${r.site}/modules/${code}`, { enabled: true });
  }
  const category = await call(request, r.token, 'post', '/catalog/categories', {
    name: `Cuisine ${r.suffix}`,
  });
  const letters = r.suffix.replace(/\d/g, (d) => 'ABCDEFGHIJ'.charAt(Number(d)));
  dish = await article(request, category.id, {
    reference: `RIZGRAS-${letters}`,
    designation: `Riz gras ${letters}`,
    unit: 'plat',
    sale_price: '1500',
    stock_managed: false,
  });
  rice = (
    await article(request, category.id, {
      reference: `RIZ-${letters}`,
      designation: `Riz ${letters}`,
      unit: 'kg',
      decimal_quantity_allowed: true,
    })
  ).id;
  egg = (
    await article(request, category.id, {
      reference: `OEUF-${letters}`,
      designation: `Oeuf ${letters}`,
      unit: 'u',
    })
  ).id;
  const entry = await call(request, r.token, 'post', '/stock/entries', {
    site_id: r.site,
    kind: 'INITIAL_STOCK',
    lines: [
      { article_id: rice, quantity: '10', unit_cost: '800' },
      { article_id: egg, quantity: '8', unit_cost: '100' },
    ],
  });
  await call(request, r.token, 'post', `/stock/entries/${entry.id}/validate`, {});
  const section = await call(request, r.token, 'post', '/restaurant/menu/sections', {
    site_id: r.site,
    name: `Cuisine ${letters}`,
  });
  menuItem = (
    await call(request, r.token, 'post', '/restaurant/menu/items', {
      site_id: r.site,
      section_id: section.id,
      article_id: dish.id,
    })
  ).id;
});

async function pick(page: Page, inputId: string, reference: string) {
  await page.locator(`#${inputId}`).fill(reference);
  await page
    .getByRole('option', { name: new RegExp(reference) })
    .first()
    .click();
}

test('création par l’interface, coût de revient et portions réalisables', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginUi(page, r.email, PASSWORD, r.name);
  await page.goto('/restaurant/recipes');
  await expect(page.getByRole('heading', { name: 'Recettes', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Nouvelle recette' }).click();
  await pick(page, 'recipe-dish', dish.reference);
  await page.getByLabel(/Rendement/).fill('4');
  await pick(page, 'recipe-line-0-article', `RIZ-`);
  await page.locator('#recipe-line-0-quantity').fill('1');
  await page.getByRole('button', { name: 'Ajouter un ingrédient' }).click();
  await pick(page, 'recipe-line-1-article', 'OEUF-');
  await page.locator('#recipe-line-1-quantity').fill('4');
  await page.getByLabel('Instructions de préparation').fill('Cuire le riz, ajouter les œufs.');
  await page.getByRole('button', { name: 'Créer la recette' }).click();
  await expect(page.getByRole('heading', { name: dish.designation })).toBeVisible();
  const composition = page.getByRole('table', { name: 'Ingrédients' });
  await expect(composition).toContainText('RIZ-');
  await expect(composition).toContainText('OEUF-');
  // Coût par portion : (1 × 800 + 4 × 100) / 4 = 300.
  await page.getByRole('tab', { name: 'Coût de revient' }).click();
  await expect(page.getByTestId('recipe-portion-cost')).toContainText('300');
  // 8 œufs pour 4 portions par 4 œufs : 8 portions ; œufs limitants.
  await expect(await units(page)).toContainText('8 portions réalisables');
});

test('vente : ingrédients déduits une fois ; plus d’œufs, plat bloqué au menu', async ({
  page,
  request,
}) => {
  const sale = await call<{ id: string; total: string }>(request, r.token, 'post', '/sales', {
    site_id: r.site,
    lines: [{ article_id: dish.id, quantity: '8' }],
  });
  await call(request, r.token, 'post', `/sales/${sale.id}/validate`, {
    payments: [{ amount: sale.total, method: 'CASH' }],
  });
  const levels = await call<{ items: { article_id: string; quantity: string }[] }>(
    request,
    r.token,
    'get',
    `/stock/levels?site_id=${r.site}&limit=100`,
  );
  const quantity = (id: string) => levels.items.find((l) => l.article_id === id)?.quantity;
  expect(quantity(egg)).toBe('0.000');
  expect(quantity(rice)).toBe('8.000');
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginUi(page, r.email, PASSWORD, r.name);
  await page.goto('/restaurant/recipes');
  await page.getByText(dish.designation).click();
  await expect(await units(page)).toContainText('Aucune portion réalisable');
  // Menu : non commandable, motif affiché.
  await page.goto('/restaurant/menu');
  await page.getByRole('tab', { name: /Éléments/ }).click();
  await expect(page.getByText('ingrédients insuffisants')).toBeVisible();
});

test('cuisine : fiche technique de la ligne commandée, sans coût', async ({ page, request }) => {
  // Stock reçu à nouveau, commande de 2 plats.
  const entry = await call(request, r.token, 'post', '/stock/entries', {
    site_id: r.site,
    kind: 'INITIAL_STOCK',
    lines: [{ article_id: egg, quantity: '8', unit_cost: '100' }],
  });
  await call(request, r.token, 'post', `/stock/entries/${entry.id}/validate`, {});
  await createOrder(request, r.token, r.site, menuItem, {
    lines: [{ menu_item_id: menuItem, quantity: '2' }],
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginUi(page, r.email, PASSWORD, r.name);
  await page.goto('/restaurant/kitchen');
  await page
    .getByRole('button', { name: `Fiche technique de ${dish.designation}` })
    .first()
    .click();
  const sheet = page.getByRole('dialog', { name: `Fiche technique — ${dish.designation}` });
  await expect(sheet).toContainText('2 portion');
  await expect(sheet).toContainText('0,5 kg');
  await expect(sheet).toContainText('Cuire le riz');
  await expect(sheet).not.toContainText(/coût|CMUP/i);
});

test('@mobile recette lisible, onglets accessibles, sans débordement', async ({
  page,
  request,
}) => {
  // Projet mobile : entreprise propre (beforeAll) ; recette créée par l'API.
  await call(request, r.token, 'post', '/restaurant/recipes', {
    article_id: dish.id,
    yield_quantity: '4',
    lines: [
      { article_id: rice, quantity: '1' },
      { article_id: egg, quantity: '4' },
    ],
  });
  await loginUi(page, r.email, PASSWORD, r.name);
  await page.goto('/restaurant/recipes');
  await page.getByText(dish.designation).first().click();
  await expect(page.getByRole('heading', { name: dish.designation })).toBeVisible();
  for (const tab of ['Coût de revient', 'Disponibilité', 'Versions', 'Composition']) {
    await page.getByRole('tab', { name: tab }).click();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
  }
});
