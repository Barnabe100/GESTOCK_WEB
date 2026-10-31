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
import { ensureCashOpen, loginUi, SALE_NUMBER } from './support';

/**
 * Module `restaurant.orders` livré (migration 0048) — parcours réels sur la pile complète :
 * prise de commande sur place (avec table), au comptoir et à emporter ; ajout et annulation de
 * lignes ; prise en charge, LIBÉRATION explicite et délai entre deux prises (5 min par défaut,
 * 5 ou 10) ; client associé ; règlement en vente ; historique ; accès concurrents (API).
 */

const PASSWORD = 'E2e-Commandes-2026';
let r: Resto;
const world = { table: '', customer: '', orderA: '', numberA: 0 };

test.describe.configure({ mode: 'serial' });

test.beforeAll(async ({ request }) => {
  r = await setupRestaurant(request, 'Maquis Commandes', PASSWORD);
  await call(request, r.token, 'put', `/sites/${r.site}/modules/restaurant.tables`, {
    enabled: true,
  });
  world.table = (
    await call(request, r.token, 'post', '/restaurant/tables', { site_id: r.site, label: 'T5' })
  ).id;
  world.customer = (
    await call(request, r.token, 'post', '/customers', {
      customer_type: 'INDIVIDUAL',
      name: `Awa Client ${r.suffix}`,
    })
  ).id;
  await ensureCashOpen(request, r.token, r.site);
});

const events = (page: Page) => page.getByTestId('order-event');

async function newOrder(page: Page, mode: 'Sur place' | 'Comptoir' | 'À emporter') {
  await page.goto('/restaurant/orders/new');
  await expect(page.getByRole('heading', { name: 'Nouvelle commande' })).toBeVisible();
  await page.getByRole('button', { name: mode, exact: true }).click();
  if (mode === 'Sur place') await choose(page, page.locator('main'), 'order-table', 'T5');
  else await expect(page.locator('#order-table')).toHaveCount(0);
  await page.getByRole('button', { name: `Ajouter ${r.itemLabel}` }).click();
  await page.getByRole('button', { name: 'Enregistrer la commande' }).click();
  await expect(
    page.getByText('Commande enregistrée. Annoncez ce numéro au client :'),
  ).toBeVisible();
  return new URL(page.url()).pathname.split('/').pop() ?? '';
}

test('prise de commande : sur place avec table, comptoir, à emporter', async ({
  page,
  request,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginUi(page, r.email, PASSWORD, r.name);
  world.orderA = await newOrder(page, 'Sur place');
  const detail = await call<{
    service_mode: string;
    table: { label: string } | null;
    total: string;
    daily_number: number;
  }>(request, r.token, 'get', `/restaurant/orders/${world.orderA}`);
  expect([detail.service_mode, detail.table?.label, detail.total]).toEqual([
    'ON_SITE',
    'T5',
    '2500.00',
  ]);
  world.numberA = detail.daily_number;
  await expect(page.getByText('T5', { exact: false }).first()).toBeVisible();

  const counter = await newOrder(page, 'Comptoir');
  const takeaway = await newOrder(page, 'À emporter');
  for (const [id, mode] of [
    [counter, 'COUNTER'],
    [takeaway, 'TAKEAWAY'],
  ] as const) {
    const order = await call<{ service_mode: string; table_id: string | null }>(
      request,
      r.token,
      'get',
      `/restaurant/orders/${id}`,
    );
    expect([order.service_mode, order.table_id]).toEqual([mode, null]);
  }
  // Suivi : les trois commandes reçues, numérotées par le serveur.
  await page.goto('/restaurant/orders');
  await expect(page.getByRole('region', { name: 'Reçues' })).toContainText(`${world.numberA}`);
});

test('lignes : ajout, annulation motivée, montant recalculé, historique', async ({
  page,
  request,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginUi(page, r.email, PASSWORD, r.name);
  await page.goto(`/restaurant/orders/${world.orderA}`);
  await page.getByRole('button', { name: 'Ajouter des lignes' }).click();
  const add = page.getByRole('dialog', { name: 'Ajouter des lignes' });
  await add.getByRole('button', { name: `Ajouter ${r.itemLabel}` }).click();
  await add.getByRole('button', { name: `Ajouter ${r.itemLabel}` }).click();
  await add.getByRole('button', { name: 'Ajouter des lignes' }).click();
  await expect(add).toBeHidden();
  let order = await call<{ total: string; lines: { id: string; quantity: string }[] }>(
    request,
    r.token,
    'get',
    `/restaurant/orders/${world.orderA}`,
  );
  expect(order.total).toBe('7500.00');
  // Annulation de la ligne ajoutée (reçue) : motif obligatoire, montant recalculé.
  const rows = page.getByRole('row', { name: new RegExp(r.itemLabel) });
  await rows.last().getByRole('button', { name: 'Annuler la ligne' }).click();
  const cancel = page.getByRole('dialog', { name: 'Annuler la ligne' });
  const confirm = cancel.getByRole('button', { name: "Confirmer l'annulation" });
  await expect(confirm).toBeDisabled();
  await cancel.getByLabel(/Motif/).fill('Erreur de saisie');
  await confirm.click();
  await expect(cancel).toBeHidden();
  order = await call(request, r.token, 'get', `/restaurant/orders/${world.orderA}`);
  expect(order.total).toBe('2500.00');
  await expect(events(page).filter({ hasText: 'Lignes ajoutées' })).toHaveCount(1);
  await expect(events(page).filter({ hasText: 'Ligne annulée' })).toContainText('Erreur de saisie');
});

test('serveurs : prise, libération explicite, délai entre deux prises, réglage 5 ou 10 min', async ({
  page,
  request,
}) => {
  const waiter = await addMember(request, r, 'seller');
  const other = await createOrder(request, r.token, r.site, r.item, {
    service_mode: 'COUNTER',
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginUi(page, waiter.email, PASSWORD, r.name);
  await page.goto(`/restaurant/orders/${world.orderA}`);
  await page.getByRole('button', { name: 'Prendre' }).click();
  await expect(page.getByRole('button', { name: 'Libérer' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Prendre' })).toHaveCount(0);

  // Libération par le responsable, motif facultatif ; la commande redevient « à prendre ».
  await page.getByRole('button', { name: 'Libérer' }).click();
  const dialog = page.getByRole('dialog', { name: 'Libérer' });
  await dialog.getByLabel(/Motif/).fill('Fin de service');
  await dialog.getByRole('button', { name: 'Libérer' }).click();
  await expect(page.getByText('Commande libérée.')).toBeVisible();
  await expect(page.getByText('Non prise en charge')).toBeVisible();
  await expect(events(page).filter({ hasText: 'Libérée' })).toContainText('Fin de service');
  const released = await call<{ assigned_user_id: string | null }>(
    request,
    r.token,
    'get',
    `/restaurant/orders/${world.orderA}`,
  );
  expect(released.assigned_user_id).toBeNull();
  // Délai entre deux prises (5 min par défaut) : la libération ne le remet pas à zéro.
  await page.goto(`/restaurant/orders/${other.id}`);
  await page.getByRole('button', { name: 'Prendre' }).click();
  await expect(
    page.getByText('Délai entre deux prises en cours : réessayez dans un instant.'),
  ).toBeVisible();
  const refused = await raw(request, waiter.token, 'post', `/restaurant/orders/${other.id}/claim`);
  expect([refused.status, refused.code]).toEqual([409, 'claim_cooldown_active']);
  // Un collègue n'est pas concerné ; seul le responsable libère (serveur).
  await call(request, r.token, 'post', `/restaurant/orders/${other.id}/claim`);
  const notMine = await raw(
    request,
    waiter.token,
    'post',
    `/restaurant/orders/${other.id}/release`,
    {},
  );
  expect([notMine.status, notMine.code]).toEqual([409, 'order_not_claimed_by_you']);

  // Responsable du site : choix du délai, 5 ou 10 minutes seulement (nouvelle session).
  await page.context().clearCookies();
  await page.evaluate(() => sessionStorage.clear());
  await loginUi(page, r.email, PASSWORD, r.name);
  await page.goto('/restaurant/orders/settings');
  await page.getByRole('button', { name: '10 minutes' }).click();
  await page.getByRole('button', { name: 'Enregistrer' }).click();
  await expect(page.getByText('Réglages enregistrés.')).toBeVisible();
  const settings = await call<{ claim_cooldown_minutes: number }>(
    request,
    r.token,
    'get',
    `/restaurant/settings/${r.site}`,
  );
  expect(settings.claim_cooldown_minutes).toBe(10);
  const invalid = await raw(request, r.token, 'put', `/restaurant/settings/${r.site}`, {
    payment_timing: 'AT_END',
    claim_cooldown_minutes: 7,
  });
  expect(invalid.status).toBe(422);
});

test('accès concurrents : une seule prise, une seule libération', async ({ request }) => {
  const order = await createOrder(request, r.token, r.site, r.item, { service_mode: 'TAKEAWAY' });
  const members = await Promise.all([1, 2, 3].map(() => addMember(request, r, 'seller')));
  const claims = await Promise.all(
    members.map((m) => raw(request, m.token, 'post', `/restaurant/orders/${order.id}/claim`)),
  );
  expect(claims.filter((c) => c.status === 200)).toHaveLength(1);
  expect(claims.filter((c) => c.status === 409).map((c) => c.code)).toEqual([
    'order_claim_protected',
    'order_claim_protected',
  ]);
  const holder = members[claims.findIndex((c) => c.status === 200)];
  const releases = await Promise.all(
    [0, 1].map(() =>
      raw(request, holder?.token ?? '', 'post', `/restaurant/orders/${order.id}/release`, {}),
    ),
  );
  expect(releases.map((x) => x.status).sort()).toEqual([200, 409]);
  const history = await call<{ event_type: string }[]>(
    request,
    r.token,
    'get',
    `/restaurant/orders/${order.id}/events`,
  );
  expect(history.filter((e) => e.event_type === 'CLAIMED')).toHaveLength(1);
  expect(history.filter((e) => e.event_type === 'RELEASED')).toHaveLength(1);
});

test('client associé, règlement en vente, service et clôture tracés', async ({ page, request }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginUi(page, r.email, PASSWORD, r.name);
  await page.goto(`/restaurant/orders/${world.orderA}`);
  await page.getByRole('button', { name: 'Associer un client' }).click();
  const customer = page.getByRole('dialog', { name: 'Associer un client' });
  await customer.getByRole('combobox').fill(`Awa Client ${r.suffix}`);
  await page.getByRole('option', { name: new RegExp(`Awa Client ${r.suffix}`) }).click();
  await customer.getByRole('button', { name: 'Enregistrer' }).click();
  await expect(customer).toBeHidden();
  await expect(events(page).filter({ hasText: 'Client associé' })).toContainText(
    `Awa Client ${r.suffix}`,
  );

  // Règlement : même dialogue que les ventes (espèces, session de caisse ouverte).
  await page.getByRole('button', { name: 'Régler' }).click();
  const pay = page.getByRole('dialog', { name: 'Valider la vente' });
  // Client associé : le dialogue des ventes propose le crédit ; paiement immédiat choisi.
  await pay.getByRole('checkbox', { name: 'Encaisser un paiement maintenant' }).check();
  await expect(pay.getByLabel(/^Montant reçu/)).toHaveValue(/2\s?500/);
  await pay.getByRole('button', { name: 'Valider la vente' }).click();
  await expect(page.getByText(/Commande réglée : vente VENT-/)).toBeVisible();
  const settled = await call<{
    settlement_status: string;
    sale_id: string;
    customer_id: string;
    status: string;
  }>(request, r.token, 'get', `/restaurant/orders/${world.orderA}`);
  expect([settled.settlement_status, settled.customer_id, settled.status]).toEqual([
    'SETTLED',
    world.customer,
    'OPEN',
  ]);
  const sale = await call<{ number: string; channel: string; customer_id: string; total: string }>(
    request,
    r.token,
    'get',
    `/sales/${settled.sale_id}`,
  );
  expect(sale.number).toMatch(SALE_NUMBER);
  expect([sale.channel, sale.customer_id, sale.total]).toEqual([
    'RESTAURANT',
    world.customer,
    '2500.00',
  ]);
  // Préparation et service : la commande réglée et servie se clôture (définitive).
  for (const action of ['start', 'ready', 'serve']) {
    await call(request, r.token, 'post', `/restaurant/orders/${world.orderA}/${action}`);
  }
  await page.reload();
  await expect(page.getByText(/Commande clôturée/)).toBeVisible();
  for (const label of ['Commande créée', 'Prise en charge', 'Libérée', 'Réglée', 'Clôturée']) {
    await expect(events(page).filter({ hasText: label }).first()).toBeVisible();
  }
  await expect(page.getByRole('button', { name: 'Régler' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Ajouter des lignes' })).toHaveCount(0);
});
