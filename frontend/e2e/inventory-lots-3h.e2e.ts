import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import { bearer, loginUi, ownerSql, provisionTenant, tokenFor } from './support';

/**
 * Lot 3-H (ADR-0045) — inventaires par lot, sur une entreprise créée pour l'exécution : comptage
 * par lot et écarts croisés (écart de l'article nul), lot découvert créé à la validation, comptage
 * en conditionnement + vrac par lot, lot apparu pendant le comptage (« Actualiser les lots »), lot
 * périmé compté et ajusté, validations simultanées, écran étroit.
 *
 * P1-b reste active : les articles sont marqués « suivis par lot » par le mécanisme RÉSERVÉ AUX
 * TESTS (rôle propriétaire de la base, ``ownerSql``) — l'application refuse toujours l'activation.
 */

const RUN = Date.now().toString().slice(-7);
const TENANT = `Lots INV E2E ${RUN}`;
const EMAIL = `lots-inv-${RUN}@example.com`;
const PASSWORD = 'E2e-LotsInv-2026';

const day = (offset: number) => {
  // Fuseau de l'entreprise de test : Afrique/Ouagadougou (UTC).
  const date = new Date();
  date.setUTCDate(date.getUTCDate() + offset);
  return date.toISOString().slice(0, 10);
};

interface Site {
  id: string;
  name: string;
}

interface World {
  token: string;
  site: Site;
  category: string;
}

let world: World;
let counter = 0;

async function call(
  request: APIRequestContext,
  method: 'get' | 'post' | 'put',
  path: string,
  data?: unknown,
) {
  return request[method](`/api/v1${path}`, { headers: bearer(world.token), data });
}

async function api<T = { id: string }>(
  request: APIRequestContext,
  method: 'get' | 'post' | 'put',
  path: string,
  data?: unknown,
): Promise<T> {
  const response = await call(request, method, path, data);
  expect(response.status(), await response.text()).toBeLessThan(300);
  return (await response.json()) as T;
}

interface Article {
  id: string;
  reference: string;
  suffix: string;
}

/** Réception validée d'un lot `[numéro, quantité, décalage de péremption en jours]`. */
async function receive(
  request: APIRequestContext,
  article: Article,
  lot: [string, string, number],
) {
  const [number, quantity, offset] = lot;
  const entry = await api(request, 'post', '/stock/entries', {
    site_id: world.site.id,
    kind: 'INITIAL_STOCK',
    lines: [
      {
        article_id: article.id,
        quantity,
        unit_cost: '200',
        lot_number: `${number}-${article.suffix}`,
        lot_expiry_date: day(offset),
      },
    ],
  });
  await api(request, 'post', `/stock/entries/${entry.id}/validate`, {});
}

/** Article suivi par lot (préparation RÉSERVÉE AUX TESTS), une réception par lot. */
async function trackedArticle(
  request: APIRequestContext,
  lots: [string, string, number][],
): Promise<Article> {
  counter += 1;
  const suffix = `${RUN}-${counter}-${Math.floor(Math.random() * 1000)}`;
  const reference = `LAIT-${suffix}`;
  const article = await api(request, 'post', '/catalog/articles', {
    reference,
    designation: `Lait inventaire ${suffix}`,
    category_id: world.category,
    unit: 'brique',
    sale_price: '500',
  });
  ownerSql(
    `UPDATE catalog_articles SET lot_tracked = true, expiry_tracked = true WHERE id = '${article.id}'`,
  );
  const created = { id: article.id, reference, suffix };
  for (const lot of lots) await receive(request, created, lot);
  return created;
}

const lotName = (article: Article, number: string) => `${number}-${article.suffix}`;

/** Inventaire ciblé démarré (comptage en cours). */
async function startedInventory(request: APIRequestContext, article: Article) {
  const draft = await api<{ id: string; number: string }>(request, 'post', '/inventories', {
    site_id: world.site.id,
    inventory_type: 'TARGETED',
    article_ids: [article.id],
  });
  await api(request, 'post', `/inventories/${draft.id}/start`);
  return draft;
}

async function balances(request: APIRequestContext, article: Article) {
  const page = await api<{ items: { number: string; quantity: string }[] }>(
    request,
    'get',
    `/stock/lots?article_id=${article.id}&site_id=${world.site.id}&limit=50`,
  );
  return Object.fromEntries(page.items.map((l) => [l.number, l.quantity]));
}

async function level(request: APIRequestContext, article: Article) {
  const page = await api<{ items: { quantity: string }[] }>(
    request,
    'get',
    `/stock/levels?article_id=${article.id}&site_id=${world.site.id}`,
  );
  return Number(page.items[0]?.quantity ?? '0').toFixed(3);
}

/** Ajustements de l'inventaire : `[lot, quantité]` triés. */
async function adjustments(request: APIRequestContext, inventoryId: string) {
  const page = await api<{
    items: { movement_type: string; lot_number: string | null; quantity: string }[];
  }>(request, 'get', `/stock/movements?source_id=${inventoryId}&limit=50`);
  return page.items
    .filter((m) => m.movement_type === 'ADJUSTMENT')
    .map((m) => [m.lot_number, m.quantity])
    .sort();
}

/** Saisie d'un comptage de lot (enregistré à la sortie du champ). */
async function countLot(page: Page, number: string, quantity: string) {
  const input = page.getByLabel(`Quantité physique du lot ${number}`, { exact: true });
  await input.fill(quantity);
  const saved = page.waitForResponse(
    (r) => r.url().includes('/lots') && r.request().method() === 'PUT',
  );
  await input.blur();
  expect((await saved).status()).toBe(200);
}

async function completeAndValidate(page: Page) {
  await page.getByRole('button', { name: 'Terminer le comptage' }).click();
  await expect(page.getByText('À valider', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: "Valider l'inventaire" }).click();
  await page.getByRole('dialog').getByRole('button', { name: "Valider l'inventaire" }).click();
}

const overflow = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);

test.describe.configure({ mode: 'serial' });

test.beforeAll(async ({ request }) => {
  await provisionTenant(request, {
    name: TENANT,
    profile: 'retail.alimentation',
    email: EMAIL,
    password: PASSWORD,
  });
  const token = await tokenFor(request, EMAIL, PASSWORD, TENANT);
  const sites = (await (
    await request.get('/api/v1/sites', { headers: bearer(token) })
  ).json()) as Site[];
  world = { token, site: sites[0] as Site, category: '' };
  world.category = (
    await api(request, 'post', '/catalog/categories', { name: `Laitages INV ${RUN}` })
  ).id;
});

test.describe('Inventaires par lot — Lot 3-H', () => {
  test('écarts croisés : A −5 / B +5, écart de l’article nul, deux ajustements par lot', async ({
    page,
    request,
  }) => {
    expect(await api(request, 'get', '/catalog/lot-tracking')).toEqual({ available: false });
    const article = await trackedArticle(request, [
      ['A', '60', 30],
      ['B', '40', 60],
    ]);
    const [a, b] = [lotName(article, 'A'), lotName(article, 'B')];
    const inv = await startedInventory(request, article);
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto(`/inventories/${inv.id}`);
    const panel = page.getByRole('region', { name: `Lots de ${article.reference}` });
    await expect(panel).toBeVisible();
    await expect(page.getByText('Suivi par lot', { exact: true })).toBeVisible();
    await expect(panel.getByText('Théorique au démarrage : 60 brique')).toBeVisible();
    await expect(panel.getByText('Théorique au démarrage : 40 brique')).toBeVisible();
    // Aucune saisie globale pour un article suivi.
    await expect(page.getByLabel(`Quantité physique de ${article.reference}`)).toHaveCount(0);
    await countLot(page, a, '55');
    await countLot(page, b, '45');
    const row = page.getByRole('row').filter({ hasText: article.reference }).first();
    await expect(row).toContainText('100 brique');
    await completeAndValidate(page);
    await expect(page.getByText(`Inventaire ${inv.number} validé : stock ajusté`)).toBeVisible();
    // Écart de l'article nul, mais un ajustement par lot.
    expect(await adjustments(request, inv.id)).toEqual(
      [
        [a, '-5.000'],
        [b, '5.000'],
      ].sort(),
    );
    expect(await balances(request, article)).toEqual({ [a]: '55.000', [b]: '45.000' });
    expect(await level(request, article)).toBe('100.000');
    // Validé : lecture seule.
    await expect(panel.getByRole('textbox')).toHaveCount(0);
  });

  test('lot découvert : créé à la validation seulement, lot attendu non saisi = 0', async ({
    page,
    request,
  }) => {
    const article = await trackedArticle(request, [['P', '10', 30]]);
    const [p, d] = [lotName(article, 'P'), lotName(article, 'D')];
    const inv = await startedInventory(request, article);
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto(`/inventories/${inv.id}`);
    await page.getByRole('button', { name: 'Ajouter un lot découvert' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.locator('#discover-number').fill(d);
    await dialog.locator('#discover-expiry').fill(day(90));
    await dialog.locator('#discover-quantity').fill('7');
    await dialog.getByRole('button', { name: 'Ajouter le lot' }).click();
    await expect(page.getByText(`Lot ${d} ajouté au comptage.`)).toBeVisible();
    const panel = page.getByRole('region', { name: `Lots de ${article.reference}` });
    await expect(panel.getByText('Lot découvert', { exact: true })).toBeVisible();
    // Aucun lot créé avant la validation.
    expect(Object.keys(await balances(request, article))).toEqual([p]);
    await completeAndValidate(page);
    await expect(page.getByText(`Inventaire ${inv.number} validé : stock ajusté`)).toBeVisible();
    expect(await balances(request, article)).toEqual({ [p]: '0.000', [d]: '7.000' });
    expect(await adjustments(request, inv.id)).toEqual(
      [
        [d, '7.000'],
        [p, '-10.000'],
      ].sort(),
    );
    expect(await level(request, article)).toBe('7.000');
  });

  test('conditionnement + vrac par lot : 8 × 24 + 5 = 197', async ({ page, request }) => {
    const article = await trackedArticle(request, [['K', '200', 30]]);
    await api(request, 'post', `/catalog/articles/${article.id}/packagings`, {
      name: 'Carton 24',
      conversion: '24',
      sale_price: '11000',
    });
    const k = lotName(article, 'K');
    const inv = await startedInventory(request, article);
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto(`/inventories/${inv.id}`);
    const panel = page.getByRole('region', { name: `Lots de ${article.reference}` });
    await panel
      .locator('.p-dropdown', { has: page.getByLabel(`Présentation du comptage du lot ${k}`) })
      .click();
    await page
      .locator('.p-dropdown-panel')
      .last()
      .getByRole('option', { name: /Carton 24/ })
      .click();
    await panel.getByLabel(`Nombre de Carton 24 du lot ${k}`).fill('8');
    const loose = panel.getByLabel(`Unités en vrac du lot ${k} (brique)`);
    await loose.fill('5');
    const saved = page.waitForResponse(
      (r) => r.url().includes('/lots') && r.request().method() === 'PUT',
    );
    await loose.blur();
    expect((await saved).status()).toBe(200);
    const row = page.getByRole('row').filter({ hasText: article.reference }).first();
    await expect(row).toContainText('197 brique');
    await completeAndValidate(page);
    await expect(page.getByText(`Inventaire ${inv.number} validé : stock ajusté`)).toBeVisible();
    await expect(panel.getByText('8 Carton 24 + 5 brique = 197 brique')).toBeVisible();
    expect(await adjustments(request, inv.id)).toEqual([[k, '-3.000']]);
  });

  test('lot apparu pendant le comptage : validation refusée, « Actualiser les lots », puis validée', async ({
    page,
    request,
  }) => {
    const article = await trackedArticle(request, [['E', '20', 30]]);
    const [e, n] = [lotName(article, 'E'), lotName(article, 'N')];
    const inv = await startedInventory(request, article);
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto(`/inventories/${inv.id}`);
    await countLot(page, e, '20');
    // Réception d'un nouveau lot sur le site pendant le comptage.
    await receive(request, article, ['N', '6', 60]);
    await completeAndValidate(page);
    // Encadré de la page (le message d'erreur s'affiche aussi en notification).
    const alert = page.locator('.sm-lots-changed[role="alert"]');
    await expect(alert).toBeVisible();
    await expect(alert).toContainText(`${article.reference} — lot ${n} (6)`);
    expect(await level(request, article)).toBe('26.000'); // rien n'est écrit
    await alert.getByRole('button', { name: 'Actualiser les lots' }).click();
    await expect(page.getByText(/Lots actualisés/)).toBeVisible();
    // Retour au comptage : le nouveau lot est attendu (théorique 6).
    const panel = page.getByRole('region', { name: `Lots de ${article.reference}` });
    await expect(panel.getByLabel(`Quantité physique du lot ${n}`, { exact: true })).toBeVisible();
    await countLot(page, n, '6');
    await completeAndValidate(page);
    await expect(page.getByText(`Inventaire ${inv.number} validé : stock ajusté`)).toBeVisible();
    expect(await adjustments(request, inv.id)).toEqual([]);
    expect(await balances(request, article)).toEqual({ [e]: '20.000', [n]: '6.000' });
  });

  test('lot périmé : visible, compté et ajusté', async ({ page, request }) => {
    const article = await trackedArticle(request, [['OLD', '9', 30]]);
    const old = lotName(article, 'OLD');
    ownerSql(`UPDATE stock_lots SET expiry_date = '${day(-3)}' WHERE number = '${old}'`);
    const inv = await startedInventory(request, article);
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto(`/inventories/${inv.id}`);
    const panel = page.getByRole('region', { name: `Lots de ${article.reference}` });
    await expect(panel.getByText('Périmé', { exact: true })).toBeVisible();
    await countLot(page, old, '4');
    await completeAndValidate(page);
    await expect(page.getByText(`Inventaire ${inv.number} validé : stock ajusté`)).toBeVisible();
    expect(await adjustments(request, inv.id)).toEqual([[old, '-5.000']]);
  });

  test('concurrence : validation de l’inventaire et vente simultanées, Σ lots = stock', async ({
    request,
  }) => {
    const article = await trackedArticle(request, [
      ['X', '10', 30],
      ['Y', '10', 60],
    ]);
    const [x, y] = [lotName(article, 'X'), lotName(article, 'Y')];
    const inv = await startedInventory(request, article);
    const line = (
      await api<{ items: { id: string; lots: { id: string; lot_number: string }[] }[] }>(
        request,
        'get',
        `/inventories/${inv.id}/lines`,
      )
    ).items[0];
    const rows = Object.fromEntries((line?.lots ?? []).map((l) => [l.lot_number, l.id]));
    await api(request, 'put', `/inventories/${inv.id}/lines/${line?.id}/lots`, {
      counts: [
        { lot_row_id: rows[x], quantity_physical: '8' },
        { lot_row_id: rows[y], quantity_physical: '10' },
      ],
    });
    await api(request, 'post', `/inventories/${inv.id}/complete-counting`);
    const customer = await api(request, 'post', '/customers', {
      customer_type: 'INDIVIDUAL',
      name: `Client INV ${RUN}`,
    });
    const [validated, sold] = await Promise.all([
      call(request, 'post', `/inventories/${inv.id}/validate`),
      call(request, 'post', '/pos/checkout', {
        site_id: world.site.id,
        lines: [{ article_id: article.id, quantity: '3' }],
        payments: [],
        idempotency_key: crypto.randomUUID(),
        customer_id: customer.id,
      }),
    ]);
    expect(validated.status(), await validated.text()).toBe(200);
    expect(sold.status(), await sold.text()).toBe(201);
    // Écart = physique − solde COURANT relu sous verrou : quel que soit l'ordre, le stock final
    // vaut le comptage moins la vente si elle est passée après, Σ lots = stock dans tous les cas.
    const lots = await balances(request, article);
    const total = Object.values(lots).reduce((s, q) => s + Number(q), 0);
    expect(total.toFixed(3)).toBe(await level(request, article));
    expect(['18.000', '15.000']).toContain(await level(request, article));
  });

  test('comptage par lot sur mobile : cartes, lot découvert plein écran, sans débordement @mobile', async ({
    page,
    request,
  }) => {
    const article = await trackedArticle(request, [
      ['M', '12', 30],
      ['N', '8', 60],
    ]);
    const [m, n, z] = [lotName(article, 'M'), lotName(article, 'N'), lotName(article, 'Z')];
    const inv = await startedInventory(request, article);
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto(`/inventories/${inv.id}`);
    const panel = page.getByRole('region', { name: `Lots de ${article.reference}` });
    await expect(panel).toBeVisible();
    await countLot(page, m, '12');
    await countLot(page, n, '7');
    const input = panel.getByLabel(`Quantité physique du lot ${n}`, { exact: true });
    const width = page.viewportSize()?.width ?? 0;
    const box = await input.boundingBox();
    expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(width);
    expect(await overflow(page)).toBe(false);
    await page.getByRole('button', { name: 'Ajouter un lot découvert' }).click();
    const dialog = page.getByRole('dialog');
    // Plein écran une fois l'animation d'ouverture terminée.
    await expect
      .poll(async () => Math.round((await dialog.boundingBox())?.width ?? 0))
      .toBeGreaterThanOrEqual(width - 1);
    await dialog.locator('#discover-number').fill(z);
    await dialog.locator('#discover-expiry').fill(day(120));
    await dialog.locator('#discover-quantity').fill('2');
    await dialog.getByRole('button', { name: 'Ajouter le lot' }).click();
    await expect(page.getByText(`Lot ${z} ajouté au comptage.`)).toBeVisible();
    expect(await overflow(page)).toBe(false);
    await completeAndValidate(page);
    await expect(page.getByText(`Inventaire ${inv.number} validé : stock ajusté`)).toBeVisible();
    expect(await balances(request, article)).toEqual({ [m]: '12.000', [n]: '7.000', [z]: '2.000' });
    expect(await overflow(page)).toBe(false);
  });
});
