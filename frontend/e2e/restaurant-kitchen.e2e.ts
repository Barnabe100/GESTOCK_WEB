import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import {
  addMember,
  call,
  choose,
  createOrder,
  raw,
  setupRestaurant,
  type Resto,
} from './restaurant-support';
import { assortArticles, createActiveSite, loginUi, menuEntry } from './support';

/**
 * Module `restaurant.kitchen` livré (palier R6, ADR-0050, migration 0050) — parcours réel :
 * activation EXPLICITE sur le site (facultatif pour un Maquis), postes et affectations du menu,
 * routage automatique de chaque ligne (élément → section → « Sans poste »), écran cuisine
 * (colonnes, priorité avant ancienneté, actions par ticket), commande multi-postes prête
 * seulement quand tous ses postes le sont, correction manuelle du poste, permissions des rôles,
 * isolation entre sites, écran sur tablette et sur mobile.
 */

const PASSWORD = 'E2e-Kitchen-2026';
let r: Resto;
const ids = { grill: '', drinks: '', drinkItem: '', dessertItem: '' };

test.describe.configure({ mode: 'serial' });

/** Deuxième et troisième éléments du menu (Boissons, Desserts), en stock sur le site. */
async function addItem(request: APIRequestContext, owner: Resto, label: string, section: string) {
  const category = await call(request, owner.token, 'post', '/catalog/categories', {
    name: `${section} ${owner.suffix}`,
  });
  const article = await call(request, owner.token, 'post', '/catalog/articles', {
    reference: `${label.slice(0, 4).toUpperCase()}-${owner.suffix}`,
    category_id: category.id,
    designation: `${label} ${owner.suffix.replace(/\d/g, (d) => 'KLMNOPQRST'.charAt(Number(d)))}`,
    unit: 'portion',
    purchase_price: '200',
    sale_price: '500',
  });
  await assortArticles(request, owner.token, [article.id]);
  const entry = await call(request, owner.token, 'post', '/stock/entries', {
    site_id: owner.site,
    kind: 'INITIAL_STOCK',
    lines: [{ article_id: article.id, quantity: '100', unit_cost: '200' }],
  });
  await call(request, owner.token, 'post', `/stock/entries/${entry.id}/validate`, {});
  const created = await call(request, owner.token, 'post', '/restaurant/menu/sections', {
    site_id: owner.site,
    name: section,
  });
  const item = await call(request, owner.token, 'post', '/restaurant/menu/items', {
    site_id: owner.site,
    section_id: created.id,
    article_id: article.id,
  });
  return { item: item.id, section: created.id };
}

test.beforeAll(async ({ request }) => {
  r = await setupRestaurant(request, 'Maquis Cuisine', PASSWORD);
});

const ticket = (page: Page, number: number, station: string) =>
  page.getByTestId('kitchen-ticket').and(page.getByLabel(`Commande n° ${number} — ${station}`));
const column = (page: Page, name: string) => page.getByRole('region', { name });

async function openKitchen(page: Page) {
  await page.goto('/restaurant/kitchen');
  await expect(page.getByRole('heading', { name: 'Cuisine', exact: true })).toBeVisible();
}

test('activation explicite : absente du menu tant que le site ne l’active pas', async ({
  page,
  request,
}) => {
  // Nouveau site Maquis : cuisine facultative, désactivée ; commandes proposées par le profil.
  const modules = await call<{ code: string; effective: boolean }[]>(
    request,
    r.token,
    'get',
    `/sites/${r.site}/modules`,
  );
  const state = Object.fromEntries(modules.map((m) => [m.code, m.effective]));
  expect([state['restaurant.orders'], state['restaurant.kitchen']]).toEqual([true, false]);
  // Module désactivé : ses routes refusent (aucun accès détourné par l'API).
  expect(
    (await raw(request, r.token, 'get', `/restaurant/kitchen/board?site_id=${r.site}`)).status,
  ).toBe(403);
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginUi(page, r.email, PASSWORD, r.name);
  await expect(menuEntry(page, 'Cuisine')).toHaveCount(0);
  await page.goto('/organization/modules');
  const row = page.getByRole('row', { name: /^Cuisine/ });
  await row.getByRole('switch').click();
  await expect(row.getByTestId('module-state-active')).toBeVisible();
  await page.reload();
  await expect(menuEntry(page, 'Cuisine')).toHaveCount(1);
  await expect(menuEntry(page, 'Postes de préparation')).toHaveCount(1);
});

test('postes et affectations du menu : création, unicité, section affectée', async ({
  page,
  request,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginUi(page, r.email, PASSWORD, r.name);
  await page.goto('/restaurant/kitchen/stations');
  await expect(page.getByRole('heading', { name: 'Postes de préparation' })).toBeVisible();
  for (const [name, order] of [
    ['Grillades', '10'],
    ['Boissons', '20'],
  ] as const) {
    await page.getByRole('button', { name: 'Nouveau poste' }).first().click();
    const dialog = page.getByRole('dialog', { name: 'Nouveau poste' });
    await dialog.getByLabel(/Nom du poste/).fill(name);
    await dialog.locator('#station-order').fill(order);
    await dialog.getByRole('button', { name: 'Enregistrer' }).click();
    await expect(dialog).toBeHidden();
  }
  // Nom unique sur le site, sans distinction de casse : refus du serveur affiché.
  await page.getByRole('button', { name: 'Nouveau poste' }).first().click();
  const dialog = page.getByRole('dialog', { name: 'Nouveau poste' });
  await dialog.getByLabel(/Nom du poste/).fill('grillades');
  await dialog.getByRole('button', { name: 'Enregistrer' }).click();
  await expect(dialog.getByRole('alert').or(page.locator('.p-toast'))).toContainText(/existe déjà/);
  await dialog.getByRole('button', { name: 'Annuler' }).click();

  const stations = await call<{ items: { id: string; name: string }[] }>(
    request,
    r.token,
    'get',
    `/restaurant/kitchen/stations?site_id=${r.site}&sort=position`,
  );
  expect(stations.items.map((s) => s.name)).toEqual(['Grillades', 'Boissons']);
  ids.grill = stations.items[0]?.id ?? '';
  ids.drinks = stations.items[1]?.id ?? '';

  // Section « Plats » → Grillades depuis l'interface ; Boissons affectée par l'API ; Desserts
  // sans affectation (« Sans poste »).
  const drinks = await addItem(request, r, 'Bissap', 'Boissons');
  const dessert = await addItem(request, r, 'Gateau', 'Desserts');
  ids.drinkItem = drinks.item;
  ids.dessertItem = dessert.item;
  await call(request, r.token, 'put', '/restaurant/kitchen/routes', {
    site_id: r.site,
    section_id: drinks.section,
    station_id: ids.drinks,
  });
  await page.reload();
  await page.getByRole('tab', { name: 'Affectations du menu' }).click();
  const plats = page.getByRole('region', { name: 'Plats' });
  await plats.locator('.p-dropdown').first().click();
  await page.locator('.p-dropdown-panel').last().getByRole('option', { name: 'Grillades' }).click();
  await expect(page.locator('.p-toast')).toContainText('Affectation enregistrée');
  await expect(plats.locator('.sm-kitchen-route-row').nth(1)).toContainText(
    'Suit la section (Grillades)',
  );
  const routes = await call<{ station_name: string; section_id: string | null }[]>(
    request,
    r.token,
    'get',
    `/restaurant/kitchen/routes?site_id=${r.site}`,
  );
  expect(routes.map((x) => x.station_name).sort()).toEqual(['Boissons', 'Grillades']);
});

test('routage, écran cuisine et commande multi-postes prête quand tous ses postes le sont', async ({
  page,
  request,
}) => {
  const order = await createOrder(request, r.token, r.site, r.item, {
    lines: [
      { menu_item_id: r.item, quantity: '2' },
      { menu_item_id: ids.drinkItem, quantity: '1' },
      { menu_item_id: ids.dessertItem, quantity: '1' },
    ],
  });
  const detail = await call<{ lines: { label: string; station_name: string | null }[] }>(
    request,
    r.token,
    'get',
    `/restaurant/orders/${order.id}`,
  );
  // Chaque ligne routée UNE fois, à la création : élément → section → « Sans poste ».
  expect(detail.lines.map((l) => l.station_name)).toEqual(['Grillades', 'Boissons', null]);

  await page.setViewportSize({ width: 1440, height: 900 });
  await loginUi(page, r.email, PASSWORD, r.name);
  await openKitchen(page);
  const pending = column(page, 'En attente');
  for (const station of ['Grillades', 'Boissons', 'Sans poste']) {
    await expect(pending.getByLabel(`Commande n° ${order.daily_number} — ${station}`)).toHaveCount(
      1,
    );
  }
  const grill = ticket(page, order.daily_number, 'Grillades');
  await expect(grill).toContainText(r.itemLabel);
  await expect(grill).toContainText('Reçue à');
  await expect(grill).toContainText(/depuis \d+ min/);
  await grill.getByRole('button', { name: 'Commencer' }).click();
  await expect(column(page, 'En préparation').getByLabel(/— Grillades$/)).toHaveCount(1);
  await ticket(page, order.daily_number, 'Grillades')
    .getByRole('button', { name: 'Prête' })
    .click();
  await expect(column(page, 'Prêtes').getByLabel(/— Grillades$/)).toHaveCount(1);

  // Grillades prêtes, Boissons et « Sans poste » encore reçues : la commande n'est pas prête.
  let state = await call<{ prep_status: string }>(
    request,
    r.token,
    'get',
    `/restaurant/orders/${order.id}`,
  );
  expect(state.prep_status).not.toBe('READY');
  for (const station of ['Boissons', 'Sans poste']) {
    const other = ticket(page, order.daily_number, station);
    await other.getByRole('button', { name: 'Commencer' }).click();
    await ticket(page, order.daily_number, station).getByRole('button', { name: 'Prête' }).click();
  }
  await expect(column(page, 'Prêtes').getByTestId('kitchen-ticket')).toHaveCount(3);
  state = await call(request, r.token, 'get', `/restaurant/orders/${order.id}`);
  expect(state.prep_status).toBe('READY');
  // Servir reste au service : aucun bouton « Servir » sur l'écran cuisine.
  await expect(page.getByRole('button', { name: /Servir/ })).toHaveCount(0);
  await call(request, r.token, 'post', `/restaurant/orders/${order.id}/serve`);
  await page.reload();
  await expect(page.getByLabel(new RegExp(`^Commande n° ${order.daily_number} —`))).toHaveCount(0);
});

test('priorité avant ancienneté ; correction manuelle du poste', async ({ page, request }) => {
  const older = await createOrder(request, r.token, r.site, r.item);
  const newer = await createOrder(request, r.token, r.site, r.item);
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginUi(page, r.email, PASSWORD, r.name);
  await openKitchen(page);
  const labels = () =>
    column(page, 'En attente')
      .getByTestId('kitchen-ticket')
      .evaluateAll((els) => els.map((el) => el.getAttribute('aria-label')));
  await expect
    .poll(labels)
    .toEqual([
      `Commande n° ${older.daily_number} — Grillades`,
      `Commande n° ${newer.daily_number} — Grillades`,
    ]);
  // La plus récente devient urgente : elle passe en tête, son ancienneté reste affichée.
  const urgent = ticket(page, newer.daily_number, 'Grillades');
  await urgent.locator('.sm-kitchen-priority').click();
  await page.locator('.p-dropdown-panel').last().getByRole('option', { name: 'Urgente' }).click();
  await expect(page.locator('.p-toast')).toContainText('Priorité enregistrée.');
  await expect
    .poll(labels)
    .toEqual([
      `Commande n° ${newer.daily_number} — Grillades`,
      `Commande n° ${older.daily_number} — Grillades`,
    ]);
  await expect(ticket(page, newer.daily_number, 'Grillades')).toContainText('Urgente');

  // Correction manuelle sur la fiche de la commande : la ligne passe aux Boissons.
  await page.goto(`/restaurant/orders/${older.id}`);
  await expect(page.getByTestId('order-priority')).toHaveText(/Normale/);
  const line = page.getByRole('row', { name: new RegExp(r.itemLabel) });
  await expect(line).toContainText('Grillades');
  await line.getByRole('button', { name: 'Changer de poste' }).click();
  const dialog = page.getByRole('dialog');
  await choose(page, dialog, 'kitchen-line-station', 'Boissons');
  await dialog.getByLabel(/Motif/).fill('Préparé au bar');
  await dialog.getByRole('button', { name: 'Changer de poste' }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole('row', { name: new RegExp(r.itemLabel) })).toContainText('Boissons');
  await expect(page.getByTestId('order-event').last()).toContainText(
    'Poste de préparation modifié',
  );
  await expect(page.getByTestId('order-event').last()).toContainText('Préparé au bar');
  // Nettoyage : commandes annulées (lignes reçues).
  for (const o of [older, newer]) {
    await call(request, r.token, 'post', `/restaurant/orders/${o.id}/cancel`, {
      reason: 'Fin du test',
    });
  }
});

test('écran cuisine sur tablette : trois colonnes, sans débordement', async ({ page, request }) => {
  const order = await createOrder(request, r.token, r.site, r.item);
  await page.setViewportSize({ width: 1024, height: 768 });
  await loginUi(page, r.email, PASSWORD, r.name);
  await openKitchen(page);
  await expect(ticket(page, order.daily_number, 'Grillades')).toBeVisible();
  const columns = page.locator('.sm-kitchen-column');
  const boxes = await columns.evaluateAll((els) => els.map((el) => el.getBoundingClientRect().top));
  expect(new Set(boxes.map((t) => Math.round(t))).size).toBe(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(
    false,
  );
  await call(request, r.token, 'post', `/restaurant/orders/${order.id}/cancel`, {
    reason: 'Fin du test',
  });
});

test('permissions des rôles et isolation entre sites', async ({ request }) => {
  // Vendeur : écran, priorité et poste d'une ligne ; jamais la configuration des postes.
  const seller = await addMember(request, r, 'seller');
  expect(
    (await raw(request, seller.token, 'get', `/restaurant/kitchen/board?site_id=${r.site}`)).status,
  ).toBe(200);
  const denied = await raw(request, seller.token, 'post', '/restaurant/kitchen/stations', {
    site_id: r.site,
    name: 'Pirate',
  });
  expect([denied.status, denied.code]).toEqual([403, 'permission_denied']);
  // Préparateur (modèle facultatif ajouté à la demande) : poste d'une ligne, pas de priorité.
  await call(request, r.token, 'post', '/roles/from-template', { template_code: 'preparer' });
  const preparer = await addMember(request, r, 'preparer');
  const order = await createOrder(request, r.token, r.site, r.item);
  const priority = await raw(
    request,
    preparer.token,
    'put',
    `/restaurant/kitchen/orders/${order.id}/priority`,
    { priority: 'URGENT' },
  );
  expect([priority.status, priority.code]).toEqual([403, 'permission_denied']);
  const detail = await call<{ lines: { id: string }[] }>(
    request,
    r.token,
    'get',
    `/restaurant/orders/${order.id}`,
  );
  await call(request, preparer.token, 'put', `/restaurant/kitchen/orders/${order.id}/route`, {
    line_ids: [detail.lines[0]?.id],
    station_id: null,
  });
  const routed = await call<{ lines: { station_id: string | null }[] }>(
    request,
    r.token,
    'get',
    `/restaurant/orders/${order.id}`,
  );
  expect(routed.lines[0]?.station_id).toBeNull();

  // Second site : ses postes restent les siens ; un gérant limité à ce site ne voit jamais ceux
  // du premier (liste, identifiant, écran) et ne peut y router une ligne.
  const annex = await createActiveSite(request, r.token, {
    name: `Annexe ${r.suffix}`,
    code: `KAX${r.suffix.slice(-4)}`,
    business_profile_code: 'restaurant.maquis',
  });
  await call(request, r.token, 'put', `/sites/${annex.id}/modules/restaurant.kitchen`, {
    enabled: true,
  });
  await call(request, r.token, 'post', '/restaurant/kitchen/stations', {
    site_id: annex.id,
    name: 'Grillades',
  });
  const manager = await addMember(request, r, 'manager', [annex.id]);
  const visible = await call<{ items: { site_id: string }[] }>(
    request,
    manager.token,
    'get',
    '/restaurant/kitchen/stations',
  );
  expect(visible.items.map((s) => s.site_id)).toEqual([annex.id]);
  expect(
    (await raw(request, manager.token, 'get', `/restaurant/kitchen/stations/${ids.grill}`)).status,
  ).toBe(404);
  expect(
    (await raw(request, manager.token, 'get', `/restaurant/kitchen/board?site_id=${r.site}`))
      .status,
  ).toBe(403);
  const foreign = await raw(
    request,
    manager.token,
    'put',
    `/restaurant/kitchen/orders/${order.id}/route`,
    { line_ids: [detail.lines[0]?.id], station_id: ids.grill },
  );
  expect(foreign.status).toBe(404);
  // Un poste d'un autre site n'est jamais affectable à une ligne de ce site.
  const annexStation = (visible.items[0] as unknown as { id: string }).id;
  const crossed = await raw(
    request,
    r.token,
    'put',
    `/restaurant/kitchen/orders/${order.id}/route`,
    { line_ids: [detail.lines[0]?.id], station_id: annexStation },
  );
  expect([crossed.status, crossed.code]).toEqual([404, 'station_not_found']);
  await call(request, r.token, 'post', `/restaurant/orders/${order.id}/cancel`, {
    reason: 'Fin du test',
  });
});

test('@mobile écran cuisine : colonnes empilées, actions accessibles, sans débordement', async ({
  page,
  request,
}) => {
  // Projet mobile : entreprise propre (les tests précédents ne s'y exécutent pas).
  const m = await setupRestaurant(request, 'Maquis Cuisine Mobile', PASSWORD);
  await call(request, m.token, 'put', `/sites/${m.site}/modules/restaurant.kitchen`, {
    enabled: true,
  });
  const station = await call(request, m.token, 'post', '/restaurant/kitchen/stations', {
    site_id: m.site,
    name: 'Grillades',
  });
  const sections = await call<{ items: { id: string }[] }>(
    request,
    m.token,
    'get',
    `/restaurant/menu/sections?site_id=${m.site}`,
  );
  await call(request, m.token, 'put', '/restaurant/kitchen/routes', {
    site_id: m.site,
    section_id: sections.items[0]?.id,
    station_id: station.id,
  });
  const order = await createOrder(request, m.token, m.site, m.item);
  await loginUi(page, m.email, PASSWORD, m.name);
  await openKitchen(page);
  const card = ticket(page, order.daily_number, 'Grillades');
  await expect(card).toBeVisible();
  const tops = await page
    .locator('.sm-kitchen-column')
    .evaluateAll((els) => els.map((el) => el.getBoundingClientRect().top));
  expect(tops[1]).toBeGreaterThan(tops[0] ?? 0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(
    false,
  );
  await card.getByRole('button', { name: 'Commencer' }).click();
  await expect(
    column(page, 'En préparation').getByLabel(`Commande n° ${order.daily_number} — Grillades`),
  ).toHaveCount(1);
  await page.goto('/restaurant/kitchen/stations');
  await expect(page.getByRole('heading', { name: 'Postes de préparation' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(
    false,
  );
});
