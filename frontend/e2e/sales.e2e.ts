import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import { apiToken, bearer, createMember, loginUi, OWNER, unique } from './support';

/**
 * Phase 2.4 — Ventes simples : parcours complet avec le vrai backend (StockService, RLS,
 * RBAC, audit). Chaque test crée ses propres article, stock et client (suffixe unique).
 */

interface Stocked {
  reference: string;
  customer: string;
  token: string;
}

/** Article vendu 1 500, 10 unités en stock sur le site principal, un client actif. */
async function stockedArticle(request: APIRequestContext): Promise<Stocked> {
  const token = await apiToken(request, OWNER.email, OWNER.password);
  const headers = bearer(token);
  const suffix = Date.now().toString().slice(-7);
  const reference = `E2E-V${suffix}`;
  const post = async (path: string, data: unknown, status = 201) => {
    const response = await request.post(`/api/v1${path}`, { headers, data });
    expect(response.status(), await response.text()).toBe(status);
    return (await response.json()) as { id: string };
  };
  const category = await post('/catalog/categories', { name: `Ventes E2E ${suffix}` });
  const article = await post('/catalog/articles', {
    reference,
    designation: `Marteau E2E ${suffix}`,
    category_id: category.id,
    unit: 'u',
    purchase_price: '1000',
    sale_price: '1500',
  });
  const sites = (await (await request.get('/api/v1/sites', { headers })).json()) as {
    id: string;
  }[];
  const entry = await post('/stock/entries', {
    site_id: sites[0]?.id,
    kind: 'INITIAL_STOCK',
    lines: [{ article_id: article.id, quantity: '10', unit_cost: '1000' }],
  });
  await post(`/stock/entries/${entry.id}/validate`, {}, 200);
  const customer = unique('Client Vente');
  await post('/customers', { customer_type: 'INDIVIDUAL', name: customer });
  return { reference, customer, token };
}

async function stockOf(request: APIRequestContext, token: string, reference: string) {
  const response = await request.get(`/api/v1/stock/levels?search=${reference}`, {
    headers: bearer(token),
  });
  const page = (await response.json()) as { items: { quantity: string }[] };
  return page.items[0]?.quantity;
}

/** Choisit une suggestion de l'AutoComplete ouvert par la saisie. */
async function pick(page: Page, inputId: string, text: string) {
  await page.locator(`#${inputId}`).fill(text);
  await page
    .getByRole('option', { name: new RegExp(text) })
    .first()
    .click();
}

/** Saisie d'une vente d'un article (quantité 3), client facultatif ; renvoie son numéro. */
async function enterSale(page: Page, reference: string, customer?: string) {
  await page.getByRole('link', { name: 'Ventes' }).click();
  await expect(page.getByRole('heading', { name: 'Ventes' })).toBeVisible();
  await page.getByRole('button', { name: 'Nouvelle vente' }).click();
  await expect(page.getByRole('heading', { name: 'Nouvelle vente' })).toBeVisible();
  if (customer) await pick(page, 'sale-customer', customer);
  await page.getByRole('button', { name: 'Ajouter une ligne' }).click();
  await pick(page, 'line-0-article', reference);
  await page.locator('#line-0-quantity').fill('3');
  // Total indicatif calculé à la saisie (le serveur recalcule).
  await expect(page.getByTestId('sale-total')).toHaveText(/4\s500/);
  await page.getByRole('button', { name: 'Enregistrer le brouillon' }).click();
  await expect(page.getByText('Brouillon enregistré')).toBeVisible();
  const heading = page.getByRole('heading', { name: /^Vente VTE-\d{6}$/ });
  await expect(heading).toBeVisible();
  return ((await heading.innerText()).match(/VTE-\d{6}/) ?? [''])[0];
}

async function validateSale(page: Page, number: string) {
  await page.getByRole('button', { name: 'Valider la vente' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Valider la vente' }).click();
  await expect(page.getByText(`Vente ${number} validée`)).toBeVisible();
  await expect(page.getByText('Validée', { exact: true })).toBeVisible();
}

test.describe('Ventes', () => {
  test('vendre : brouillon, validation, stock, mouvement, audit, double validation', async ({
    page,
    request,
  }) => {
    const { reference, customer, token } = await stockedArticle(request);
    await loginUi(page, OWNER.email, OWNER.password);

    const number = await enterSale(page, reference, customer);
    expect(await stockOf(request, token, reference)).toBe('10.000'); // brouillon : aucun effet
    await validateSale(page, number);
    await expect(page.getByText(new RegExp(customer))).toBeVisible();
    // Vente validée : plus de saisie possible.
    await expect(page.getByRole('button', { name: 'Enregistrer le brouillon' })).toHaveCount(0);

    // Stock : 10 − 3 = 7 (API et écran).
    expect(await stockOf(request, token, reference)).toBe('7.000');
    await page.getByRole('link', { name: 'Stock par site' }).click();
    await page.getByRole('searchbox').fill(reference);
    await expect(page.getByRole('row').filter({ hasText: reference })).toContainText('7');

    // Journal des mouvements : sortie de type Vente, rattachée au numéro de la vente.
    await page.getByRole('link', { name: 'Mouvements' }).click();
    const movement = page.getByRole('row').filter({ hasText: number });
    await expect(movement).toContainText('Vente');
    await expect(movement).toContainText('-3');

    // Audit : création et validation tracées.
    await page.getByRole('link', { name: "Journal d'audit" }).click();
    for (const action of ['sale.validated', 'sale.created']) {
      await expect(
        page.getByRole('row').filter({ hasText: action }).filter({ hasText: number }).first(),
      ).toBeVisible();
    }

    // Double validation : refusée par le backend, sans nouveau mouvement.
    const sales = (await (
      await request.get(`/api/v1/sales?search=${number}`, { headers: bearer(token) })
    ).json()) as { items: { id: string }[] };
    const again = await request.post(`/api/v1/sales/${sales.items[0]?.id}/validate`, {
      headers: bearer(token),
    });
    expect(again.status()).toBe(409);
    expect(((await again.json()) as { code: string }).code).toBe('sale_not_draft');
    expect(await stockOf(request, token, reference)).toBe('7.000');
  });

  test('le Vendeur vend sans pouvoir annuler ; l’Administrateur annule', async ({
    page,
    request,
  }) => {
    const { reference, token } = await stockedArticle(request);
    const password = 'Vendeur-Ventes-E2E-2026';
    const email = await createMember(request, token, 'seller', password);

    // Vente comptant (sans client) saisie et validée par le Vendeur.
    await loginUi(page, email, password);
    const number = await enterSale(page, reference);
    await validateSale(page, number);
    await expect(page.getByText('Sans client')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Annuler la vente' })).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'Rôles' })).toHaveCount(0);

    // Le backend refuse l'annulation au Vendeur, quel que soit l'écran.
    const sellerToken = await apiToken(request, email, password);
    const id = page.url().split('/').at(-1);
    const forbidden = await request.post(`/api/v1/sales/${id}/cancel`, {
      headers: bearer(sellerToken),
      data: { reason: 'Tentative non autorisée' },
    });
    expect(forbidden.status()).toBe(403);
    expect(((await forbidden.json()) as { code: string }).code).toBe('permission_denied');
    expect(await stockOf(request, token, reference)).toBe('7.000');

    // L'Administrateur annule : quantités remises en stock par mouvement d'annulation.
    await page.context().clearCookies();
    await loginUi(page, OWNER.email, OWNER.password);
    await page.goto(`/sales/${id}`);
    await page.getByRole('button', { name: 'Annuler la vente' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel("Motif d'annulation").fill('Erreur de saisie E2E');
    await dialog.getByRole('button', { name: 'Annuler la vente' }).click();
    await expect(page.getByText(`Vente ${number} annulée`)).toBeVisible();
    await expect(page.getByText('Annulée', { exact: true })).toBeVisible();
    expect(await stockOf(request, token, reference)).toBe('10.000');
  });

  test('affichage mobile sans débordement @mobile', async ({ page, request }) => {
    const { reference } = await stockedArticle(request);
    await loginUi(page, OWNER.email, OWNER.password);
    await page.goto('/sales');
    await expect(page.getByRole('heading', { name: 'Ventes' })).toBeVisible();
    const overflow = () =>
      page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    expect(await overflow()).toBe(false);
    await page.getByRole('button', { name: 'Nouvelle vente' }).click();
    await page.getByRole('button', { name: 'Ajouter une ligne' }).click();
    await pick(page, 'line-0-article', reference);
    await expect(page.getByTestId('sale-total')).toHaveText(/1\s500/);
    expect(await overflow()).toBe(false);
  });
});
