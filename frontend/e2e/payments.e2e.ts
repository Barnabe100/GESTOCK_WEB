import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import { apiToken, bearer, ensureCashOpen, loginUi, OWNER } from './support';

/**
 * Phase 2.7 — Paiements des ventes : la validation d'une vente est indépendante de son
 * encaissement. Chaque test prépare par l'API un article vendu 10 000 (stock 50) et une vente
 * de 10 unités = 100 000, puis encaisse depuis la fiche vente.
 */

interface Setup {
  token: string;
  reference: string;
  articleId: string;
  siteId: string;
}

interface Site {
  id: string;
  code: string;
}

async function setup(request: APIRequestContext): Promise<Setup> {
  const token = await apiToken(request, OWNER.email, OWNER.password);
  const headers = bearer(token);
  const post = async (path: string, data: unknown, status = 201) => {
    const response = await request.post(`/api/v1${path}`, { headers, data });
    expect(response.status(), await response.text()).toBe(status);
    return (await response.json()) as { id: string };
  };
  const sites = (await (await request.get('/api/v1/sites', { headers })).json()) as Site[];
  const site = sites.find((s) => s.code !== 'DEPOT-E2E') ?? (sites[0] as Site);
  const suffix = Date.now().toString().slice(-7);
  const reference = `E2E-P${suffix}`;
  const category = await post('/catalog/categories', { name: `Paiements E2E ${suffix}` });
  const article = await post('/catalog/articles', {
    reference,
    designation: `Tôle ondulée E2E ${suffix}`,
    category_id: category.id,
    unit: 'u',
    purchase_price: '6000',
    sale_price: '10000',
  });
  const entry = await post('/stock/entries', {
    site_id: site.id,
    kind: 'INITIAL_STOCK',
    lines: [{ article_id: article.id, quantity: '50', unit_cost: '6000' }],
  });
  await post(`/stock/entries/${entry.id}/validate`, {}, 200);
  // Espèces : caisse ouverte sur le site (Phase 2.9).
  await ensureCashOpen(request, token, site.id);
  return { token, reference, articleId: article.id, siteId: site.id };
}

/** Vente de 10 unités (100 000), brouillon ou validée. */
async function saleByApi(request: APIRequestContext, s: Setup, validate = true) {
  const headers = bearer(s.token);
  const created = await request.post('/api/v1/sales', {
    headers,
    data: { site_id: s.siteId, lines: [{ article_id: s.articleId, quantity: '10' }] },
  });
  expect(created.status(), await created.text()).toBe(201);
  const sale = (await created.json()) as { id: string; number: string };
  if (validate) {
    expect((await request.post(`/api/v1/sales/${sale.id}/validate`, { headers })).status()).toBe(
      200,
    );
  }
  return sale;
}

async function payByApi(request: APIRequestContext, s: Setup, saleId: string, amount: string) {
  const response = await request.post(`/api/v1/sales/${saleId}/payments`, {
    headers: bearer(s.token),
    data: { amount, method: 'CASH' },
  });
  expect(response.status(), await response.text()).toBe(201);
}

async function stockOf(request: APIRequestContext, s: Setup): Promise<string | undefined> {
  const response = await request.get(
    `/api/v1/stock/levels?search=${s.reference}&site_id=${s.siteId}`,
    { headers: bearer(s.token) },
  );
  return ((await response.json()) as { items: { quantity: string }[] }).items[0]?.quantity;
}

const summary = (page: Page) => page.getByRole('group', { name: "Résumé de l'encaissement" });

/** Montant affiché (espaces insécables du format XOF). */
const amount = (text: string) =>
  new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/ /g, '\\s'));

async function recordPayment(page: Page, value?: string, method?: string) {
  await page.getByRole('button', { name: 'Enregistrer un paiement' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  if (value !== undefined) await dialog.getByLabel(/^Montant/).fill(value);
  if (method) {
    await dialog.locator('.p-dropdown', { has: page.locator('#payment-method') }).click();
    await page.locator('.p-dropdown-panel').last().getByText(method, { exact: true }).click();
  }
  await dialog.getByRole('button', { name: 'Enregistrer le paiement' }).click();
  return dialog;
}

test.describe('Paiements des ventes', () => {
  test('paiement complet : validation (stock diminué), encaissement, « Payée », solde 0', async ({
    page,
    request,
  }) => {
    const s = await setup(request);
    const sale = await saleByApi(request, s, false);
    await loginUi(page, OWNER.email, OWNER.password);
    await page.goto(`/sales/${sale.id}`);
    await page.getByRole('button', { name: 'Valider la vente' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Valider la vente' }).click();
    await expect(page.getByText(`Vente ${sale.number} validée`)).toBeVisible();
    expect(await stockOf(request, s)).toBe('40.000');
    // Validée mais non payée : statut commercial et état d'encaissement distincts.
    await expect(page.getByText('Validée', { exact: true })).toBeVisible();
    await expect(page.getByText('Non payée', { exact: true }).first()).toBeVisible();
    await expect(page.getByText('Aucun paiement enregistré.')).toBeVisible();

    // Montant proposé = solde (100 000), espèces par défaut.
    const dialog = await recordPayment(page);
    await expect(dialog).toBeHidden();
    await expect(page.getByText('Payée', { exact: true }).first()).toBeVisible();
    await expect(summary(page)).toContainText('Solde dû');
    await expect(summary(page).locator('.sm-metric').nth(2)).toContainText(amount('0'));
    await expect(page.getByRole('button', { name: 'Enregistrer un paiement' })).toHaveCount(0);
    const row = page.getByRole('row').filter({ hasText: /PAY-\d{6}/ });
    await expect(row).toContainText('Espèces');
    await expect(row).toContainText('Effectué');
    expect(await stockOf(request, s)).toBe('40.000'); // le paiement ne touche pas au stock
  });

  test('paiement partiel puis paiements successifs et mixtes jusqu’à « Payée »', async ({
    page,
    request,
  }) => {
    const s = await setup(request);
    const sale = await saleByApi(request, s);
    await loginUi(page, OWNER.email, OWNER.password);
    await page.goto(`/sales/${sale.id}`);
    await recordPayment(page, '30000');
    await expect(page.getByText('Partiellement payée', { exact: true }).first()).toBeVisible();
    await expect(summary(page).locator('.sm-metric').nth(1)).toContainText(amount('30 000'));
    await expect(summary(page).locator('.sm-metric').nth(2)).toContainText(amount('70 000'));
    // Deuxième paiement, autre moyen : le solde proposé est 70 000.
    await page.getByRole('button', { name: 'Enregistrer un paiement' }).click();
    await expect(page.getByRole('dialog')).toContainText(amount('Solde à payer : 70 000'));
    await page.getByRole('dialog').getByRole('button', { name: 'Annuler' }).click();
    await recordPayment(page, '70000', 'Mobile Money');
    await expect(page.getByText('Payée', { exact: true }).first()).toBeVisible();
    await expect(page.getByRole('row').filter({ hasText: /PAY-\d{6}/ })).toHaveCount(2);
    await expect(page.getByRole('row').filter({ hasText: 'Mobile Money' })).toHaveCount(1);
  });

  test('annulation d’un paiement : historique conservé, « Non payée », stock inchangé', async ({
    page,
    request,
  }) => {
    const s = await setup(request);
    const sale = await saleByApi(request, s);
    await payByApi(request, s, sale.id, '100000');
    await loginUi(page, OWNER.email, OWNER.password);
    await page.goto(`/sales/${sale.id}`);
    await expect(page.getByText('Payée', { exact: true }).first()).toBeVisible();
    const row = page.getByRole('row').filter({ hasText: /PAY-\d{6}/ });
    await row.getByRole('button', { name: 'Annuler le paiement' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText(amount('Annuler ce paiement de 100 000'));
    await dialog.getByLabel(/Motif d'annulation/).fill('Erreur de caisse');
    await dialog.getByRole('button', { name: 'Annuler le paiement' }).click();
    await expect(page.getByText('Non payée', { exact: true }).first()).toBeVisible();
    await expect(row).toContainText('Annulé');
    await expect(row).toContainText('Erreur de caisse');
    await expect(page.getByText('Validée', { exact: true })).toBeVisible();
    expect(await stockOf(request, s)).toBe('40.000');
  });

  test('contrainte : solde 30 000, tentative de 40 000 refusée', async ({ page, request }) => {
    const s = await setup(request);
    const sale = await saleByApi(request, s);
    await payByApi(request, s, sale.id, '70000');
    await loginUi(page, OWNER.email, OWNER.password);
    await page.goto(`/sales/${sale.id}`);
    const dialog = await recordPayment(page, '40000');
    await expect(dialog).toContainText(amount('Le montant dépasse le solde à payer (30 000'));
    await dialog.getByRole('button', { name: 'Annuler' }).click();
    await expect(page.getByRole('row').filter({ hasText: /PAY-\d{6}/ })).toHaveCount(1);
    await expect(page.getByText('Partiellement payée', { exact: true }).first()).toBeVisible();
  });

  test('fiche vente et paiements sur mobile, sans débordement @mobile', async ({
    page,
    request,
  }) => {
    const s = await setup(request);
    const sale = await saleByApi(request, s);
    await payByApi(request, s, sale.id, '25000');
    await loginUi(page, OWNER.email, OWNER.password);
    await page.goto(`/sales/${sale.id}`);
    await expect(page.getByText('Partiellement payée', { exact: true }).first()).toBeVisible();
    const overflow = () =>
      page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    expect(await overflow()).toBe(false);
    const record = page.getByRole('button', { name: 'Enregistrer un paiement' });
    await record.scrollIntoViewIfNeeded();
    await expect(record).toBeInViewport();
    await recordPayment(page, '75000');
    await expect(page.getByText('Payée', { exact: true }).first()).toBeVisible();
    expect(await overflow()).toBe(false);
  });
});
