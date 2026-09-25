import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import { apiToken, bearer, ensureCashOpen, loginUi, OWNER } from './support';

/**
 * Phase 3.0 — Point de vente. Chaque test prépare par l'API un article vendu 10 000 (50 u en
 * boutique, suffixe unique) ; la caisse « Caisse E2E » de la boutique est ouverte au besoin.
 * Vérifications croisées par l'API : stock, vente (canal POS), paiements, caisse, créances.
 */

interface Site {
  id: string;
  code: string;
  name: string;
}

interface Setup {
  token: string;
  suffix: string;
  designation: string;
  articleId: string;
  shop: Site;
  sessionId: string;
}

async function post(request: APIRequestContext, token: string, path: string, data: unknown) {
  const response = await request.post(`/api/v1${path}`, { headers: bearer(token), data });
  expect(response.status(), await response.text()).toBeLessThan(300);
  return (await response.json()) as { id: string; code: string };
}

async function get<T>(request: APIRequestContext, token: string, path: string): Promise<T> {
  return (await (await request.get(`/api/v1${path}`, { headers: bearer(token) })).json()) as T;
}

async function setup(request: APIRequestContext): Promise<Setup> {
  const token = await apiToken(request, OWNER.email, OWNER.password);
  const sites = await get<Site[]>(request, token, '/sites');
  const shop = sites.find((s) => s.code !== 'DEPOT-E2E') as Site;
  const suffix = Date.now().toString().slice(-7);
  const designation = `Tuyau PVC ${suffix}`;
  const category = await post(request, token, '/catalog/categories', { name: `POS ${suffix}` });
  const article = await post(request, token, '/catalog/articles', {
    reference: `E2E-POS${suffix}`,
    designation,
    category_id: category.id,
    unit: 'u',
    purchase_price: '6000',
    sale_price: '10000',
  });
  const entry = await post(request, token, '/stock/entries', {
    site_id: shop.id,
    kind: 'INITIAL_STOCK',
    lines: [{ article_id: article.id, quantity: '50', unit_cost: '6000' }],
  });
  await post(request, token, `/stock/entries/${entry.id}/validate`, {});
  const session = await ensureCashOpen(request, token, shop.id);
  return { token, suffix, designation, articleId: article.id, shop, sessionId: session.id };
}

async function stockOf(request: APIRequestContext, s: Setup) {
  const page = await get<{ items: { quantity: string }[] }>(
    request,
    s.token,
    `/stock/levels?search=E2E-POS${s.suffix}&site_id=${s.shop.id}`,
  );
  return page.items[0]?.quantity;
}

interface SaleOut {
  id: string;
  number: string;
  channel: string;
  status: string;
  payment_status: string;
  remaining_amount: string;
}

async function lastPosSale(request: APIRequestContext, s: Setup) {
  const page = await get<{ items: SaleOut[] }>(
    request,
    s.token,
    `/sales?channel=POS&site_id=${s.shop.id}&sort=-number&limit=1`,
  );
  return page.items[0] as SaleOut;
}

async function cashMovementsOf(request: APIRequestContext, s: Setup, saleNumber: string) {
  const page = await get<{ items: { amount: string; movement_type: string }[] }>(
    request,
    s.token,
    `/cash/movements?search=${saleNumber}`,
  );
  return page.items;
}

/** Montant affiché (espaces insécables du format XOF). */
const amount = (text: string) =>
  new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/ /g, '\\s'));

async function openPos(page: Page, s: Setup) {
  await loginUi(page, OWNER.email, OWNER.password);
  // Menu « Ventes et clients » (lien direct sur mobile, où la barre latérale est repliée).
  const link = page.getByRole('link', { name: 'Point de vente', exact: true });
  if ((page.viewportSize()?.width ?? 0) > 800) await link.click();
  else await page.goto('/pos');
  await expect(page.getByRole('heading', { name: 'Point de vente' })).toBeVisible();
  if (await page.getByText('Choisissez le site de vente pour commencer.').isVisible()) {
    await page.locator('.sm-pos-header .p-dropdown').click();
    await page.locator('.p-dropdown-panel').last().getByText(s.shop.name, { exact: true }).click();
  }
  await page.getByLabel('Rechercher un article (F2)').fill(s.designation);
  await expect(tile(page, s)).toBeVisible();
}

const tile = (page: Page, s: Setup) =>
  page.getByRole('button', { name: `Ajouter ${s.designation} au panier` });

/** Paiements saisis dans le dialogue (F8) : [moyen, montant]. */
async function pay(page: Page, payments: [string, string][]) {
  await page.keyboard.press('F8');
  const dialog = page.getByRole('dialog', { name: 'Paiements (F8)' });
  const methods = dialog.getByRole('group', { name: 'Ajouter un moyen de paiement' });
  for (const [index, [method, value]] of payments.entries()) {
    await methods.getByRole('button', { name: method }).click();
    await dialog.getByLabel(`Montant du paiement ${index + 1}`).fill(value);
  }
  await dialog.getByRole('button', { name: 'Appliquer' }).click();
  await expect(dialog).toBeHidden();
}

async function validate(page: Page) {
  await page.keyboard.press('F10');
  const confirm = page.getByRole('dialog', { name: 'Valider la vente ?' });
  await confirm.getByRole('button', { name: 'Valider la vente (F10)' }).click();
  return confirm;
}

test.describe('Point de vente', () => {
  test('vente comptant en espèces : stock, vente, paiement, mouvement de caisse', async ({
    page,
    request,
  }) => {
    const s = await setup(request);
    await openPos(page, s);
    await tile(page, s).click();
    await tile(page, s).click();
    await expect(page.getByTestId('pos-total')).toHaveText(amount('20 000'));
    await pay(page, [['Espèces', '20000']]);
    await validate(page);
    const receipt = page.getByTestId('pos-receipt');
    await expect(receipt).toContainText('Espèces');
    await expect(page.getByRole('dialog', { name: /Vente VTE-\d{6} enregistrée/ })).toBeVisible();
    expect(await stockOf(request, s)).toBe('48.000');
    const sale = await lastPosSale(request, s);
    expect([sale.channel, sale.status, sale.payment_status]).toEqual(['POS', 'VALIDATED', 'PAID']);
    const movements = await cashMovementsOf(request, s, sale.number);
    expect(movements.map((m) => [m.movement_type, m.amount])).toEqual([
      ['SALE_CASH_IN', '20000.00'],
    ]);
  });

  test('vente Mobile Money : aucun mouvement de caisse', async ({ page, request }) => {
    const s = await setup(request);
    await openPos(page, s);
    await page.getByLabel('Rechercher un article (F2)').press('Enter');
    await pay(page, [['Mobile Money', '10000']]);
    await validate(page);
    await expect(page.getByTestId('pos-receipt')).toContainText('Mobile Money');
    const sale = await lastPosSale(request, s);
    expect(sale.payment_status).toBe('PAID');
    expect(await cashMovementsOf(request, s, sale.number)).toEqual([]);
    expect(await stockOf(request, s)).toBe('49.000');
  });

  test('vente à crédit : client, limite contrôlée, créance', async ({ page, request }) => {
    const s = await setup(request);
    const customer = await post(request, s.token, '/customers', {
      customer_type: 'INDIVIDUAL',
      name: `Client POS ${s.suffix}`,
      credit_limit: '30000',
    });
    await openPos(page, s);
    await page.keyboard.press('F4');
    const dialog = page.getByRole('dialog', { name: 'Client de la vente (F4)' });
    await dialog.getByRole('searchbox').fill(`Client POS ${s.suffix}`);
    await dialog.getByRole('button', { name: new RegExp(`Client POS ${s.suffix}`) }).click();
    await expect(page.getByText(`Client POS ${s.suffix} (${customer.code})`)).toBeVisible();
    await tile(page, s).click();
    await tile(page, s).click();
    await validate(page);
    await expect(page.getByRole('dialog', { name: /Vente VTE-\d{6} enregistrée/ })).toBeVisible();
    const sale = await lastPosSale(request, s);
    expect([sale.payment_status, sale.remaining_amount]).toEqual(['UNPAID', '20000.00']);
    expect(await stockOf(request, s)).toBe('48.000');
    const receivables = await get<{ items: { sale_number: string; remaining_amount: string }[] }>(
      request,
      s.token,
      `/customers/${customer.id}/receivables`,
    );
    expect(receivables.items.map((r) => [r.sale_number, r.remaining_amount])).toEqual([
      [sale.number, '20000.00'],
    ]);
    // Nouvelle vente à crédit de 20 000 : 40 000 > 30 000, refusée par le serveur.
    await page.getByRole('button', { name: 'Nouvelle vente' }).click();
    await page.keyboard.press('F4');
    const again = page.getByRole('dialog', { name: 'Client de la vente (F4)' });
    await again.getByRole('searchbox').fill(`Client POS ${s.suffix}`);
    await again.getByRole('button', { name: new RegExp(`Client POS ${s.suffix}`) }).click();
    await page.getByLabel('Rechercher un article (F2)').fill(s.designation);
    await tile(page, s).click();
    await tile(page, s).click();
    const refused = await validate(page);
    await expect(refused).toContainText('Limite de crédit du client dépassée');
    expect(await stockOf(request, s)).toBe('48.000');
  });

  test('paiement mixte : 40 000 espèces + 60 000 Mobile Money', async ({ page, request }) => {
    const s = await setup(request);
    await openPos(page, s);
    await tile(page, s).click();
    await page.getByLabel(`Quantité de ${s.designation}`, { exact: true }).fill('10');
    await expect(page.getByTestId('pos-total')).toHaveText(amount('100 000'));
    await pay(page, [
      ['Espèces', '40000'],
      ['Mobile Money', '60000'],
    ]);
    await validate(page);
    const receipt = page.getByTestId('pos-receipt');
    await expect(receipt.getByTestId('receipt-remaining')).toHaveText(amount('0'));
    const sale = await lastPosSale(request, s);
    expect(sale.payment_status).toBe('PAID');
    const payments = await get<{ items: { method: string; amount: string }[] }>(
      request,
      s.token,
      `/sales/${sale.id}/payments`,
    );
    expect(payments.items.map((p) => [p.method, p.amount]).sort()).toEqual([
      ['CASH', '40000.00'],
      ['MOBILE_MONEY', '60000.00'],
    ]);
    expect((await cashMovementsOf(request, s, sale.number)).map((m) => m.amount)).toEqual([
      '40000.00',
    ]);
    expect(await stockOf(request, s)).toBe('40.000');
  });

  test('point de vente sur mobile : catalogue puis panier, sans débordement @mobile', async ({
    page,
    request,
  }) => {
    const s = await setup(request);
    await openPos(page, s);
    const overflow = () =>
      page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    expect(await overflow()).toBe(false);
    await tile(page, s).click();
    await page.getByRole('tab', { name: 'Panier (1)' }).click();
    await expect(page.getByLabel(`Quantité de ${s.designation}`, { exact: true })).toBeVisible();
    await expect(page.getByTestId('pos-total')).toHaveText(amount('10 000'));
    expect(await overflow()).toBe(false);
    await page.getByRole('button', { name: 'Valider la vente (F10)' }).click();
    await page
      .getByRole('dialog', { name: 'Valider la vente ?' })
      .getByRole('button', { name: 'Valider la vente (F10)' })
      .click();
    await expect(page.getByRole('dialog', { name: /Vente VTE-\d{6} enregistrée/ })).toBeVisible();
    expect(await overflow()).toBe(false);
  });
});
