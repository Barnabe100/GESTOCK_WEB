import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import {
  assortArticles,
  bearer,
  createMember,
  loginUi,
  enableLotTracking,
  provisionTenant,
  SALE_NUMBER,
  tokenFor,
} from './support';

/**
 * Lot 3-H-A (ADR-0045 : H-D1 à H-D18, O-1 à O-6), sur une entreprise créée pour l'exécution :
 * consommation des lots par les ventes (FEFO), le point de vente, les sorties (choix manuel) et
 * les annulations ; lots périmés jamais vendus automatiquement, dérogation explicite ;
 * conditionnements ; permissions ; concurrence ; écrans étroits (POS, sortie).
 *
 * P1-b levée (clôture du Lot 3-H) : les articles sont passés au suivi par lot par l'API, comme
 * le ferait un utilisateur (article géré en stock, stock nul).
 */

const RUN = Date.now().toString().slice(-7);
const TENANT = `Lots HA E2E ${RUN}`;
const EMAIL = `lots-ha-${RUN}@example.com`;
const PASSWORD = 'E2e-LotsHA-2026';

const day = (offset: number) => {
  // Fuseau de l'entreprise de test : Afrique/Ouagadougou (UTC).
  const date = new Date();
  date.setUTCDate(date.getUTCDate() + offset);
  return date.toISOString().slice(0, 10);
};

interface World {
  token: string;
  site: { id: string; name: string };
  category: string;
}

let world: World;
let counter = 0;

async function api<T = { id: string }>(
  request: APIRequestContext,
  method: 'get' | 'post' | 'put',
  path: string,
  data?: unknown,
  token = world.token,
): Promise<T> {
  const response = await request[method](`/api/v1${path}`, { headers: bearer(token), data });
  expect(response.status(), await response.text()).toBeLessThan(300);
  return (await response.json()) as T;
}

interface Article {
  id: string;
  reference: string;
  designation: string;
}

/**
 * Article suivi par lot (activation réelle par l'API) et stock initial par lot, une réception
 * par lot : `[numéro, quantité, décalage de péremption en jours]`.
 */
async function trackedArticle(
  request: APIRequestContext,
  lots: [string, string, number][],
  tracked = true,
): Promise<Article> {
  counter += 1;
  const suffix = `${RUN}-${counter}-${Math.floor(Math.random() * 1000)}`;
  const reference = `EAU3H-${suffix}`;
  const designation = `Eau 3H ${suffix}`;
  const article = await api(request, 'post', '/catalog/articles', {
    reference,
    designation,
    category_id: world.category,
    unit: 'bouteille',
    sale_price: '500',
  });
  // Assortiment (ADR-0046) : articles ajoutés EXPLICITEMENT aux sites qui les proposent.
  await assortArticles(request, world.token, [article.id], [world.site.id]);
  if (tracked) {
    await enableLotTracking(request, world.token, article.id);
  }
  for (const [number, quantity, offset] of lots) {
    const entry = await api(request, 'post', '/stock/entries', {
      site_id: world.site.id,
      kind: 'INITIAL_STOCK',
      lines: [
        {
          article_id: article.id,
          quantity,
          unit_cost: '200',
          ...(tracked ? { lot_number: `${number}-${suffix}`, lot_expiry_date: day(offset) } : {}),
        },
      ],
    });
    await api(request, 'post', `/stock/entries/${entry.id}/validate`, {});
  }
  return { id: article.id, reference, designation };
}

const lotName = (article: Article, number: string) =>
  `${number}-${article.reference.slice('EAU3H-'.length)}`;

async function balances(request: APIRequestContext, article: Article) {
  const page = await api<{ items: { number: string; quantity: string }[] }>(
    request,
    'get',
    `/stock/lots?article_id=${article.id}&limit=50`,
  );
  return Object.fromEntries(page.items.map((l) => [l.number, l.quantity]));
}

async function pick(page: Page, inputId: string, text: string) {
  await page.locator(`#${inputId}`).fill(text);
  await page
    .getByRole('option', { name: new RegExp(text) })
    .first()
    .click();
}

/** Brouillon de vente (sans client : payé à la validation). */
async function enterSale(page: Page, article: Article, quantity: string) {
  await page.goto('/sales/new');
  await expect(page.getByRole('heading', { name: 'Nouvelle vente' })).toBeVisible();
  await page.getByRole('button', { name: 'Ajouter une ligne' }).click();
  await pick(page, 'line-0-article', article.reference);
  await page.locator('#line-0-quantity').fill(quantity);
  await page.getByRole('button', { name: 'Enregistrer le brouillon' }).click();
  await expect(page.getByText('Brouillon enregistré')).toBeVisible();
}

async function openValidation(page: Page) {
  await page.getByRole('button', { name: 'Valider la vente' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByLabel('Encaisser un paiement maintenant')).toBeChecked();
  await dialog.locator('.p-dropdown', { has: page.locator('#validate-method') }).click();
  await page
    .locator('.p-dropdown-panel')
    .last()
    .getByRole('option', { name: 'Mobile Money' })
    .click();
  return dialog;
}

const validatedToast = (page: Page) =>
  page.getByText(new RegExp(`^Vente ${SALE_NUMBER.source} validée$`));

/** Paiement intégral au POS (F8) : une vente sans client ne peut pas rester à crédit. */
async function payPos(page: Page, amount: string) {
  await page.keyboard.press('F8');
  const dialog = page.getByRole('dialog', { name: 'Paiements (F8)' });
  await dialog
    .getByRole('group', { name: 'Ajouter un moyen de paiement' })
    .getByRole('button', { name: 'Mobile Money' })
    .click();
  await dialog.getByLabel(/^Montant (reçu )?du paiement 1$/).fill(amount);
  await dialog.getByRole('button', { name: 'Appliquer' }).click();
  await expect(dialog).toBeHidden();
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
  const sites = (await (await request.get('/api/v1/sites', { headers: bearer(token) })).json()) as {
    id: string;
    name: string;
  }[];
  world = { token, site: sites[0] as { id: string; name: string }, category: '' };
  world.category = (
    await api(request, 'post', '/catalog/categories', { name: `Boissons 3H ${RUN}-${Date.now()}` })
  ).id;
});

test.describe('Consommation des lots — Lot 3-H-A', () => {
  test('P1-b levée ; article non suivi : vente inchangée', async ({ request }) => {
    expect(await api(request, 'get', '/catalog/lot-tracking')).toEqual({ available: true });
    const plain = await trackedArticle(request, [['P', '10', 0]], false);
    const { sale } = await api<{ sale: { id: string; lines: { lots: unknown[] }[] } }>(
      request,
      'post',
      '/pos/checkout',
      {
        site_id: world.site.id,
        lines: [{ article_id: plain.id, quantity: '3' }],
        payments: [],
        idempotency_key: crypto.randomUUID(),
        ...(await creditCustomer(request)),
      },
    );
    expect(sale.lines[0]?.lots).toEqual([]);
    const movements = await api<{ items: { lot_number: string | null; quantity: string }[] }>(
      request,
      'get',
      `/stock/movements?source_id=${sale.id}`,
    );
    expect(movements.items.map((m) => [m.lot_number, m.quantity])).toEqual([[null, '-3.000']]);
  });

  test('vente back-office : FEFO multi-lots, lot périmé jamais choisi, détail par lot', async ({
    page,
    request,
  }) => {
    const article = await trackedArticle(request, [
      ['LATE', '20', 60],
      ['SOON', '10', 10],
      ['OLD', '5', -5],
    ]);
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await enterSale(page, article, '15');
    const dialog = await openValidation(page);
    await dialog.getByRole('button', { name: 'Valider la vente' }).click();
    await expect(validatedToast(page)).toBeVisible();
    const cell = page.locator('td', { hasText: `${article.reference} — ${article.designation}` });
    const lots = cell.getByRole('list', { name: 'Lots de la ligne' });
    await expect(lots.getByRole('listitem')).toHaveCount(2);
    await expect(lots.getByRole('listitem').nth(0)).toContainText(
      `Lot ${lotName(article, 'SOON')}`,
    );
    await expect(lots.getByRole('listitem').nth(0)).toContainText('10 bouteille');
    await expect(lots.getByRole('listitem').nth(1)).toContainText(
      `Lot ${lotName(article, 'LATE')}`,
    );
    await expect(lots.getByRole('listitem').nth(1)).toContainText('5 bouteille');
    expect(await balances(request, article)).toEqual({
      [lotName(article, 'SOON')]: '0.000',
      [lotName(article, 'LATE')]: '15.000',
      [lotName(article, 'OLD')]: '5.000',
    });
  });

  test('lot périmé : vente refusée par défaut, puis dérogation explicite tracée', async ({
    page,
    request,
  }) => {
    const article = await trackedArticle(request, [
      ['OLD', '5', -3],
      ['OK', '3', 40],
    ]);
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await enterSale(page, article, '5');
    const dialog = await openValidation(page);
    await dialog.getByRole('button', { name: 'Valider la vente' }).click();
    await expect(dialog.getByText(/Stock non périmé insuffisant/)).toBeVisible();
    const panel = dialog.getByRole('region', { name: 'Lot périmé' });
    await expect(panel.getByText(/Périmé depuis le/)).toBeVisible();
    const sell = panel.getByRole('button', { name: 'Vendre avec dérogation' });
    await expect(sell).toBeDisabled();
    await panel.getByLabel(/Motif de la dérogation/).fill('Déstockage autorisé par la direction');
    await panel.getByLabel('Je confirme vendre ces lots périmés').check();
    await sell.click();
    await expect(validatedToast(page)).toBeVisible();
    await expect(page.getByText('Dérogation lot périmé')).toBeVisible();
    await expect(page.getByText(/Déstockage autorisé par la direction/)).toBeVisible();
    expect(await balances(request, article)).toEqual({
      [lotName(article, 'OLD')]: '3.000',
      [lotName(article, 'OK')]: '0.000',
    });
  });

  test('permissions : dérogation refusée au Gestionnaire', async ({ request }) => {
    const article = await trackedArticle(request, [
      ['OLD', '4', -3],
      ['OK', '1', 40],
    ]);
    const email = await createMember(request, world.token, 'manager', 'Gestion-E2E-2026');
    const manager = await tokenFor(request, email, 'Gestion-E2E-2026', TENANT);
    const lots = await api<{ lots: { lot_id: string; expired: boolean }[] }>(
      request,
      'get',
      `/pos/articles/${article.id}/lots?site_id=${world.site.id}`,
    );
    const expired = lots.lots.find((l) => l.expired)?.lot_id;
    const body = {
      site_id: world.site.id,
      lines: [{ article_id: article.id, quantity: '2' }],
      payments: [],
      idempotency_key: crypto.randomUUID(),
      ...(await creditCustomer(request)),
    };
    const refused = await request.post('/api/v1/pos/checkout', {
      headers: bearer(manager),
      data: body,
    });
    expect([refused.status(), ((await refused.json()) as { code: string }).code]).toEqual([
      422,
      'insufficient_unexpired_stock',
    ]);
    const forbidden = await request.post('/api/v1/pos/checkout', {
      headers: bearer(manager),
      data: {
        ...body,
        idempotency_key: crypto.randomUUID(),
        expired_lot_override: {
          reason: 'Tentative non autorisée',
          lots: [{ article_id: article.id, lot_id: expired, quantity: '1' }],
        },
      },
    });
    expect([forbidden.status(), ((await forbidden.json()) as { code: string }).code]).toEqual([
      403,
      'expired_lot_override_not_allowed',
    ]);
  });

  test('POS : FEFO automatique sans sélecteur, lots sur le reçu', async ({ page, request }) => {
    const article = await trackedArticle(request, [
      ['B', '10', 30],
      ['A', '2', 5],
    ]);
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto('/pos');
    await page.getByLabel('Rechercher un article (F2)').fill(article.designation);
    const tile = page.getByRole('button', { name: `Ajouter ${article.designation} au panier` });
    for (let i = 0; i < 3; i += 1) await tile.click();
    await payPos(page, '1500');
    await page.keyboard.press('F10');
    const confirm = page.getByRole('dialog', { name: 'Valider la vente ?' });
    await confirm.getByRole('button', { name: 'Valider la vente (F10)' }).click();
    const receipt = page.getByTestId('pos-receipt');
    await expect(receipt).toBeVisible();
    const lots = receipt.getByRole('list', { name: 'Lots de la ligne' });
    await expect(lots.getByRole('listitem').nth(0)).toContainText(`Lot ${lotName(article, 'A')}`);
    await expect(lots.getByRole('listitem').nth(1)).toContainText(`Lot ${lotName(article, 'B')}`);
  });

  test('sortie : brouillon incomplet, validation refusée, puis multi-lots validée et annulée', async ({
    page,
    request,
  }) => {
    const article = await trackedArticle(request, [
      ['X', '6', 20],
      ['Y', '6', 50],
    ]);
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto('/stock/exits/new');
    await expect(page.getByRole('heading', { name: 'Nouvelle sortie' })).toBeVisible();
    await page.locator('.p-dropdown', { has: page.locator('#doc-reason') }).click();
    await page.locator('.p-dropdown-panel').last().getByRole('option').first().click();
    await page.getByRole('button', { name: 'Ajouter une ligne' }).click();
    await pick(page, 'line-0-article', article.reference);
    await page.locator('#line-0-quantity').fill('10');
    const editor = page.getByRole('group', { name: 'Lots' });
    await editor.getByLabel(`Quantité du lot ${lotName(article, 'X')}`).fill('6');
    await expect(editor.getByText('Reste : 4 bouteille')).toBeVisible();
    await expect(editor.getByText('Répartition incomplète')).toBeVisible();
    await page.getByRole('button', { name: 'Enregistrer le brouillon' }).click();
    await expect(page.getByText('Brouillon enregistré')).toBeVisible();
    // Validation refusée par le serveur tant que la somme n'est pas exacte.
    await page.getByRole('button', { name: 'Valider', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Valider' }).click();
    await expect(page.getByText(/La répartition par lot ne correspond pas/)).toBeVisible();
    // Confirmation refermée (animation de sortie) avant la nouvelle validation.
    await expect(page.getByRole('dialog')).toBeHidden();
    await page
      .getByRole('group', { name: 'Lots' })
      .getByLabel(`Quantité du lot ${lotName(article, 'Y')}`)
      .fill('4');
    await expect(page.getByText('Répartition complète')).toBeVisible();
    await page.getByRole('button', { name: 'Valider', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Valider' }).click();
    await expect(page.getByText(/^Document SOR-\d+ validé$/)).toBeVisible();
    expect(await balances(request, article)).toEqual({
      [lotName(article, 'X')]: '0.000',
      [lotName(article, 'Y')]: '2.000',
    });
    // Annulation : chaque lot d'origine restauré exactement.
    await page.getByRole('button', { name: 'Annuler le document' }).first().click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel(/Motif/).fill('Erreur de saisie');
    await dialog.getByRole('button', { name: 'Annuler le document' }).click();
    await expect(page.getByText(/annulé/).first()).toBeVisible();
    expect(await balances(request, article)).toEqual({
      [lotName(article, 'X')]: '6.000',
      [lotName(article, 'Y')]: '6.000',
    });
  });

  test('annulation d’une vente : restauration exacte des lots, une seule fois', async ({
    page,
    request,
  }) => {
    const article = await trackedArticle(request, [
      ['A', '4', 10],
      ['B', '8', 20],
    ]);
    const checkout = await api<{ sale: { id: string } }>(request, 'post', '/pos/checkout', {
      site_id: world.site.id,
      lines: [{ article_id: article.id, quantity: '6' }],
      payments: [],
      idempotency_key: crypto.randomUUID(),
      ...(await creditCustomer(request)),
    });
    expect(await balances(request, article)).toEqual({
      [lotName(article, 'A')]: '0.000',
      [lotName(article, 'B')]: '6.000',
    });
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto(`/sales/${checkout.sale.id}`);
    await page.getByRole('button', { name: 'Annuler la vente' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.locator('#cancel-reason').fill('Erreur de caisse');
    await dialog.getByRole('button', { name: 'Annuler la vente' }).click();
    await expect(page.getByText('Annulée', { exact: true })).toBeVisible();
    expect(await balances(request, article)).toEqual({
      [lotName(article, 'A')]: '4.000',
      [lotName(article, 'B')]: '8.000',
    });
    const again = await request.post(`/api/v1/sales/${checkout.sale.id}/cancel`, {
      headers: bearer(world.token),
      data: { reason: 'Seconde annulation' },
    });
    expect(again.status()).toBe(409);
  });

  test('conditionnement : 2 cartons répartis, présentation seulement si exacte', async ({
    request,
  }) => {
    const article = await trackedArticle(request, [
      ['A', '6', 10],
      ['B', '10', 20],
    ]);
    const carton = await api(request, 'post', `/catalog/articles/${article.id}/packagings`, {
      name: 'Carton 6',
      conversion: '6',
      sale_price: '2800',
    });
    const checkout = await api<{ sale: { id: string } }>(request, 'post', '/pos/checkout', {
      site_id: world.site.id,
      lines: [{ article_id: article.id, packaging_id: carton.id, quantity: '2' }],
      payments: [],
      idempotency_key: crypto.randomUUID(),
      ...(await creditCustomer(request)),
    });
    const movements = await api<{
      items: { lot_number: string; quantity: string; packaging_quantity: string | null }[];
    }>(request, 'get', `/stock/movements?source_id=${checkout.sale.id}&sort=occurred_at`);
    expect(
      movements.items.map((m) => [m.lot_number, m.quantity, m.packaging_quantity]).sort(),
    ).toEqual([
      [lotName(article, 'A'), '-6.000', '1.000'],
      [lotName(article, 'B'), '-6.000', '1.000'],
    ]);
  });

  test('concurrence : deux ventes simultanées sans double consommation', async ({ request }) => {
    const article = await trackedArticle(request, [['A', '10', 10]]);
    const customer = await creditCustomer(request);
    const sell = (quantity: string) =>
      request.post('/api/v1/pos/checkout', {
        headers: bearer(world.token),
        data: {
          site_id: world.site.id,
          lines: [{ article_id: article.id, quantity }],
          payments: [],
          idempotency_key: crypto.randomUUID(),
          ...customer,
        },
      });
    const statuses = (await Promise.all([sell('8'), sell('5')])).map((r) => r.status()).sort();
    expect(statuses).toEqual([201, 422]);
    const left = (await balances(request, article))[lotName(article, 'A')];
    expect(['2.000', '5.000']).toContain(left);
  });

  test('POS sur mobile : reçu avec les lots, sans débordement @mobile', async ({
    page,
    request,
  }) => {
    const article = await trackedArticle(request, [['M', '5', 30]]);
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto('/pos');
    await page.getByLabel('Rechercher un article (F2)').fill(article.designation);
    await page.getByRole('button', { name: `Ajouter ${article.designation} au panier` }).click();
    await page.getByRole('tab', { name: /Panier/ }).click();
    expect(await overflow(page)).toBe(false);
    await payPos(page, '500');
    await page.getByRole('button', { name: 'Valider la vente (F10)' }).click();
    const confirm = page.getByRole('dialog', { name: 'Valider la vente ?' });
    await confirm.getByRole('button', { name: 'Valider la vente (F10)' }).click();
    const receipt = page.getByTestId('pos-receipt');
    await expect(receipt.getByRole('list', { name: 'Lots de la ligne' })).toContainText(
      `Lot ${lotName(article, 'M')}`,
    );
    expect(await overflow(page)).toBe(false);
  });

  test('sortie sur mobile : sélecteur de lots utilisable, sans débordement @mobile', async ({
    page,
    request,
  }) => {
    const article = await trackedArticle(request, [
      ['P', '3', 20],
      ['Q', '3', 40],
    ]);
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto('/stock/exits/new');
    await expect(page.getByRole('heading', { name: 'Nouvelle sortie' })).toBeVisible();
    await page.getByRole('button', { name: 'Ajouter une ligne' }).click();
    await pick(page, 'line-0-article', article.reference);
    await page.locator('#line-0-quantity').fill('4');
    const editor = page.getByRole('group', { name: 'Lots' });
    const input = editor.getByLabel(`Quantité du lot ${lotName(article, 'P')}`);
    await input.fill('3');
    await editor.getByLabel(`Quantité du lot ${lotName(article, 'Q')}`).fill('1');
    await expect(editor.getByText('Répartition complète')).toBeVisible();
    expect(await overflow(page)).toBe(false);
    const box = await input.boundingBox();
    const width = page.viewportSize()?.width ?? 0;
    expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(width);
  });
});

/** Client du test (une vente non payée à la validation est à crédit : client obligatoire). */
let customerId: string | null = null;
async function creditCustomer(request: APIRequestContext) {
  if (customerId === null) {
    customerId = (
      await api(request, 'post', '/customers', {
        customer_type: 'INDIVIDUAL',
        name: `Client 3H ${RUN}-${Date.now()}`,
      })
    ).id;
  }
  return { customer_id: customerId };
}
