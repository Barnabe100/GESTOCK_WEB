import { expect, test, type Page } from '@playwright/test';

import {
  addMember,
  call,
  choose,
  createOrder,
  raw,
  setupRestaurant,
  type Resto,
} from './restaurant-support';
import { createActiveSite, ensureCashOpen, loginUi, menuEntry, paymentMethodId } from './support';

/**
 * Module `restaurant.tables` livré (migration 0048) — parcours réel : activation EXPLICITE sur le
 * site, zones et tables (création, modification), plan de salle calculé par le serveur,
 * occupation par une commande et libération automatique à sa clôture, permissions des rôles et
 * isolation entre sites. Entreprise dédiée (Maquis : tables facultatives, désactivées).
 */

const PASSWORD = 'E2e-Tables-2026';
let r: Resto;
const ids = { zone: '', t1: '', t2: '' };

test.describe.configure({ mode: 'serial' });

test.beforeAll(async ({ request }) => {
  r = await setupRestaurant(request, 'Maquis Tables', PASSWORD);
});

async function openTables(page: Page, tab: 'Plan de salle' | 'Tables' | 'Zones') {
  await page.goto('/restaurant/tables');
  await expect(page.getByRole('heading', { name: 'Tables', exact: true })).toBeVisible();
  await page.getByRole('tab', { name: tab }).click();
}

const card = (page: Page, label: string) =>
  page.getByTestId('floor-table').and(page.getByLabel(label, { exact: true }));

test('activation explicite : absentes du menu tant que le site ne les active pas', async ({
  page,
  request,
}) => {
  // Nouveau site Maquis : tables facultatives, désactivées ; commandes proposées par le profil.
  const modules = await call<{ code: string; effective: boolean }[]>(
    request,
    r.token,
    'get',
    `/sites/${r.site}/modules`,
  );
  const state = Object.fromEntries(modules.map((m) => [m.code, m.effective]));
  expect([state['restaurant.orders'], state['restaurant.tables']]).toEqual([true, false]);
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginUi(page, r.email, PASSWORD, r.name);
  await expect(menuEntry(page, 'Tables')).toHaveCount(0);
  await page.goto('/organization/modules');
  const row = page.getByRole('row', { name: /^Tables/ });
  await row.getByRole('switch').click();
  await expect(row.getByTestId('module-state-active')).toBeVisible();
  await page.reload();
  await expect(menuEntry(page, 'Tables')).toHaveCount(1);
});

test('zones et tables : création, modification, unicité', async ({ page, request }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginUi(page, r.email, PASSWORD, r.name);
  await openTables(page, 'Zones');
  await page.getByRole('button', { name: 'Nouvelle zone' }).first().click();
  let dialog = page.getByRole('dialog', { name: 'Nouvelle zone' });
  await dialog.getByLabel(/Nom de la zone/).fill('Terrasse');
  await dialog.getByRole('button', { name: 'Enregistrer' }).click();
  await expect(dialog).toBeHidden();
  const zoneRow = page.getByRole('row', { name: /Terrasse/ });
  await zoneRow.getByRole('button', { name: 'Modifier' }).click();
  dialog = page.getByRole('dialog', { name: 'Modifier la zone' });
  await dialog.getByLabel(/Nom de la zone/).fill('Terrasse VIP');
  await dialog.getByRole('button', { name: 'Enregistrer' }).click();
  await expect(page.getByRole('row', { name: /Terrasse VIP/ })).toBeVisible();

  await page.getByRole('tab', { name: 'Tables' }).click();
  for (const [label, seats] of [
    ['T1', '4'],
    ['T2', '2'],
  ] as const) {
    await page.getByRole('button', { name: 'Nouvelle table' }).first().click();
    dialog = page.getByRole('dialog', { name: 'Nouvelle table' });
    await dialog.getByLabel(/Libellé/).fill(label);
    await choose(page, dialog, 'table-zone', 'Terrasse VIP');
    await dialog.locator('#table-seats').fill(seats);
    await dialog.getByRole('button', { name: 'Enregistrer' }).click();
    await expect(dialog).toBeHidden();
  }
  // Libellé unique sur le site, sans distinction de casse : refus du serveur affiché.
  await page.getByRole('button', { name: 'Nouvelle table' }).first().click();
  dialog = page.getByRole('dialog', { name: 'Nouvelle table' });
  await dialog.getByLabel(/Libellé/).fill('t1');
  await dialog.getByRole('button', { name: 'Enregistrer' }).click();
  await expect(dialog.getByRole('alert').or(page.locator('.p-toast'))).toContainText(/déjà/);
  await dialog.getByRole('button', { name: 'Annuler' }).click();
  // Modification : T2 passe à 6 places.
  await page.getByRole('row', { name: /^T2/ }).getByRole('button', { name: 'Modifier' }).click();
  dialog = page.getByRole('dialog', { name: 'Modifier la table' });
  await dialog.locator('#table-seats').fill('6');
  await dialog.getByRole('button', { name: 'Enregistrer' }).click();
  await expect(dialog).toBeHidden();

  const tables = await call<{ items: { id: string; label: string; seats: number | null }[] }>(
    request,
    r.token,
    'get',
    `/restaurant/tables?site_id=${r.site}&sort=label`,
  );
  expect(tables.items.map((t) => [t.label, t.seats])).toEqual([
    ['T1', 4],
    ['T2', 6],
  ]);
  ids.t1 = tables.items[0]?.id ?? '';
  ids.t2 = tables.items[1]?.id ?? '';
});

test('plan de salle : occupation par une commande, libération automatique à la clôture', async ({
  page,
  request,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginUi(page, r.email, PASSWORD, r.name);
  await openTables(page, 'Plan de salle');
  await expect(card(page, 'T1')).toContainText('Libre');
  await expect(card(page, 'T1')).toContainText('4 places');
  await expect(page.getByRole('heading', { name: 'Terrasse VIP' })).toBeVisible();

  const order = await createOrder(request, r.token, r.site, r.item, { table_id: ids.t1 });
  await page.reload();
  await page.getByRole('tab', { name: 'Plan de salle' }).click();
  await expect(card(page, 'T1')).toContainText('Occupée');
  await expect(card(page, 'T1')).toContainText('1 commande en cours');
  await expect(card(page, 'T2')).toContainText('Libre');
  const link = card(page, 'T1').getByRole('link', { name: `n° ${order.daily_number}` });
  await expect(link).toHaveAttribute('href', `/restaurant/orders/${order.id}`);

  // Préparation, service puis règlement : la commande se clôture, la table se libère.
  for (const action of ['start', 'ready', 'serve']) {
    await call(request, r.token, 'post', `/restaurant/orders/${order.id}/${action}`);
  }
  await page.reload();
  await page.getByRole('tab', { name: 'Plan de salle' }).click();
  await expect(card(page, 'T1')).toContainText('À régler');
  const session = await ensureCashOpen(request, r.token, r.site);
  const cash = await paymentMethodId(request, r.token, 'Espèces');
  const settled = await call<{ order: { status: string } }>(
    request,
    r.token,
    'post',
    `/restaurant/orders/${order.id}/settle`,
    {
      payments: [
        {
          payment_method_id: cash,
          amount_received: '2500',
          cash_register_id: session.cash_register_id,
        },
      ],
      idempotency_key: crypto.randomUUID(),
    },
  );
  expect(settled.order.status).toBe('CLOSED');
  await page.reload();
  await page.getByRole('tab', { name: 'Plan de salle' }).click();
  await expect(card(page, 'T1')).toContainText('Libre');
  await expect(card(page, 'T1').getByRole('link')).toHaveCount(0);
});

test('permissions : le Vendeur consulte sans configurer ; isolation entre sites', async ({
  page,
  request,
}) => {
  // Vendeur : consultation des tables, aucune configuration (refus du serveur).
  const seller = await addMember(request, r, 'seller');
  expect(
    (await raw(request, seller.token, 'get', `/restaurant/tables/board?site_id=${r.site}`)).status,
  ).toBe(200);
  const denied = await raw(request, seller.token, 'post', '/restaurant/tables/zones', {
    site_id: r.site,
    name: 'Salle',
  });
  expect([denied.status, denied.code]).toEqual([403, 'permission_denied']);
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginUi(page, seller.email, PASSWORD, r.name);
  await openTables(page, 'Tables');
  await expect(page.getByRole('row', { name: /^T1/ })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Nouvelle table' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Modifier' })).toHaveCount(0);

  // Second site : ses tables restent les siennes ; un gérant limité à ce site ne voit jamais
  // celles du premier (ni par la liste, ni par l'identifiant, ni par le plan de salle).
  const depot = await createActiveSite(request, r.token, {
    name: `Annexe ${r.suffix}`,
    code: `ANX${r.suffix.slice(-4)}`,
    business_profile_code: 'restaurant.maquis',
  });
  await call(request, r.token, 'put', `/sites/${depot.id}/modules/restaurant.tables`, {
    enabled: true,
  });
  await call(request, r.token, 'post', '/restaurant/tables', { site_id: depot.id, label: 'A1' });
  const manager = await addMember(request, r, 'manager', [depot.id]);
  const visible = await call<{ items: { label: string }[] }>(
    request,
    manager.token,
    'get',
    '/restaurant/tables',
  );
  expect(visible.items.map((t) => t.label)).toEqual(['A1']);
  expect((await raw(request, manager.token, 'get', `/restaurant/tables/${ids.t1}`)).status).toBe(
    404,
  );
  const board = await raw(
    request,
    manager.token,
    'get',
    `/restaurant/tables/board?site_id=${r.site}`,
  );
  expect(board.status).toBe(403);
  const foreign = await raw(request, manager.token, 'put', `/restaurant/tables/${ids.t1}`, {
    label: 'Pirate',
  });
  expect(foreign.status).toBe(404);
});
