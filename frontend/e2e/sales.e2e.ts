import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import {
  assortArticles,
  apiToken,
  bearer,
  createMember,
  loginUi,
  OWNER,
  SALE_NUMBER,
  unique,
} from './support';

/**
 * Phase 2.4 — Ventes simples : parcours complet avec le vrai backend (StockService, RLS,
 * RBAC, audit). Chaque test crée ses propres article, stock et client (suffixe unique).
 */

interface Stocked {
  reference: string;
  customer: string;
  token: string;
  siteId: string;
  siteName: string;
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
  // Assortiment (ADR-0046) : article ajouté EXPLICITEMENT aux sites de l'entreprise.
  await assortArticles(request, token, [article.id]);
  const sites = (await (await request.get('/api/v1/sites', { headers })).json()) as {
    id: string;
    name: string;
  }[];
  const entry = await post('/stock/entries', {
    site_id: sites[0]?.id,
    kind: 'INITIAL_STOCK',
    lines: [{ article_id: article.id, quantity: '10', unit_cost: '1000' }],
  });
  await post(`/stock/entries/${entry.id}/validate`, {}, 200);
  const customer = unique('Client Vente');
  await post('/customers', { customer_type: 'INDIVIDUAL', name: customer });
  return {
    reference,
    customer,
    token,
    siteId: sites[0]?.id ?? '',
    siteName: sites[0]?.name ?? '',
  };
}

/** Stock de l'article sur le site où il a été préparé (l'entreprise peut avoir plusieurs sites). */
async function stockOf(request: APIRequestContext, stocked: Stocked) {
  const { token, reference, siteId } = stocked;
  const response = await request.get(`/api/v1/stock/levels?search=${reference}&site_id=${siteId}`, {
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

/** Saisie d'une vente d'un article (quantité 3), client facultatif : brouillon non numéroté. */
async function enterSale(page: Page, stocked: Stocked, customer?: string) {
  const { reference } = stocked;
  await page.getByRole('link', { name: 'Ventes', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Ventes' })).toBeVisible();
  await page.getByRole('button', { name: 'Nouvelle vente' }).first().click();
  await expect(page.getByRole('heading', { name: 'Nouvelle vente' })).toBeVisible();
  // Entreprise multi-sites (site créé par les tests de transferts) : site du stock préparé.
  if (await page.locator('#sale-site').count()) {
    await page.locator('.p-dropdown', { has: page.locator('#sale-site') }).click();
    await page
      .locator('.p-dropdown-panel')
      .last()
      .locator('.p-dropdown-item', { hasText: stocked.siteName })
      .click();
  }
  if (customer) await pick(page, 'sale-customer', customer);
  await page.getByRole('button', { name: 'Ajouter une ligne' }).click();
  await pick(page, 'line-0-article', reference);
  await page.locator('#line-0-quantity').fill('3');
  // Total indicatif calculé à la saisie (le serveur recalcule).
  await expect(page.getByTestId('sale-total')).toHaveText(/4\s500/);
  await page.getByRole('button', { name: 'Enregistrer le brouillon' }).click();
  await expect(page.getByText('Brouillon enregistré')).toBeVisible();
  // Numéro attribué à la validation seulement (Lot 1).
  await expect(page.getByRole('heading', { name: 'Vente non numérotée' })).toBeVisible();
}

/**
 * Validation ; `pay` : moyen d'un encaissement immédiat du total (vente sans client : entièrement
 * payée, le crédit exigeant un client). Renvoie le numéro `VENT-…` attribué par le serveur.
 */
async function validateSale(page: Page, pay?: string) {
  await page.getByRole('button', { name: 'Valider la vente' }).click();
  const dialog = page.getByRole('dialog');
  if (pay) {
    await expect(dialog.getByLabel('Encaisser un paiement maintenant')).toBeChecked();
    await dialog.locator('.p-dropdown', { has: page.locator('#validate-method') }).click();
    await page.locator('.p-dropdown-panel').last().getByRole('option', { name: pay }).click();
  }
  await dialog.getByRole('button', { name: 'Valider la vente' }).click();
  const toast = page.getByText(new RegExp(`^Vente ${SALE_NUMBER.source} validée$`));
  await expect(toast).toBeVisible();
  await expect(page.getByText('Validée', { exact: true })).toBeVisible();
  const number = ((await toast.innerText()).match(SALE_NUMBER) ?? [''])[0];
  await expect(page.getByRole('heading', { name: `Vente ${number}` })).toBeVisible();
  return number;
}

test.describe('Ventes', () => {
  test('vendre : brouillon, validation, stock, mouvement, audit, double validation', async ({
    page,
    request,
  }) => {
    const stocked = await stockedArticle(request);
    const { reference, customer, token } = stocked;
    await loginUi(page, OWNER.email, OWNER.password);

    await enterSale(page, stocked, customer);
    expect(await stockOf(request, stocked)).toBe('10.000'); // brouillon : aucun effet
    // Vente à crédit (client identifié, permission du propriétaire).
    const number = await validateSale(page);
    await expect(page.getByText(new RegExp(customer))).toBeVisible();
    // Vente validée : plus de saisie possible.
    await expect(page.getByRole('button', { name: 'Enregistrer le brouillon' })).toHaveCount(0);

    // Stock : 10 − 3 = 7 (API et écran).
    expect(await stockOf(request, stocked)).toBe('7.000');
    await page.getByRole('link', { name: 'Stock par site', exact: true }).click();
    await page.getByRole('searchbox').fill(reference);
    await expect(
      page.getByRole('row').filter({ hasText: reference }).filter({ hasText: stocked.siteName }),
    ).toContainText('7');

    // Journal des mouvements : sortie de type Vente, rattachée au numéro de la vente.
    await page.getByRole('link', { name: 'Mouvements', exact: true }).click();
    const movement = page.getByRole('row').filter({ hasText: number });
    await expect(movement).toContainText('Vente');
    await expect(movement).toContainText('-3');

    // Audit : validation tracée avec le numéro définitif (le brouillon n'en a pas).
    await page.getByRole('link', { name: "Journal d'audit", exact: true }).click();
    await expect(
      page.getByRole('row').filter({ hasText: 'sale.validated' }).filter({ hasText: number }),
    ).toBeVisible();

    // Double validation : refusée par le backend, sans nouveau mouvement.
    const sales = (await (
      await request.get(`/api/v1/sales?search=${number}`, { headers: bearer(token) })
    ).json()) as { items: { id: string }[] };
    const again = await request.post(`/api/v1/sales/${sales.items[0]?.id}/validate`, {
      headers: bearer(token),
    });
    expect(again.status()).toBe(409);
    expect(((await again.json()) as { code: string }).code).toBe('sale_not_draft');
    expect(await stockOf(request, stocked)).toBe('7.000');
  });

  test('le Vendeur vend sans pouvoir annuler ; l’Administrateur annule', async ({
    page,
    request,
  }) => {
    const stocked = await stockedArticle(request);
    const { token } = stocked;
    const password = 'Vendeur-Ventes-E2E-2026';
    const email = await createMember(request, token, 'seller', password);

    // Vente comptant (client « Ordinaire ») saisie, payée et validée par le Vendeur.
    await loginUi(page, email, password);
    await enterSale(page, stocked);
    const number = await validateSale(page, 'Mobile Money');
    await expect(page.getByText('Ordinaire', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Annuler la vente' })).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'Rôles', exact: true })).toHaveCount(0);

    // Le backend refuse l'annulation au Vendeur, quel que soit l'écran.
    const sellerToken = await apiToken(request, email, password);
    const id = page.url().split('/').at(-1);
    const forbidden = await request.post(`/api/v1/sales/${id}/cancel`, {
      headers: bearer(sellerToken),
      data: { reason: 'Tentative non autorisée' },
    });
    expect(forbidden.status()).toBe(403);
    expect(((await forbidden.json()) as { code: string }).code).toBe('permission_denied');
    expect(await stockOf(request, stocked)).toBe('7.000');

    // L'Administrateur annule : d'abord le paiement (vente payée), puis la vente ; quantités
    // remises en stock par mouvement d'annulation.
    const payments = (await (
      await request.get(`/api/v1/sales/${id}/payments`, { headers: bearer(token) })
    ).json()) as { items: { id: string }[] };
    for (const payment of payments.items) {
      const cancelled = await request.post(`/api/v1/sales/${id}/payments/${payment.id}/cancel`, {
        headers: bearer(token),
        data: { reason: 'Remboursement E2E' },
      });
      expect(cancelled.status(), await cancelled.text()).toBe(200);
    }
    await page.context().clearCookies();
    await loginUi(page, OWNER.email, OWNER.password);
    await page.goto(`/sales/${id}`);
    await page.getByRole('button', { name: 'Annuler la vente' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel("Motif d'annulation").fill('Erreur de saisie E2E');
    await dialog.getByRole('button', { name: 'Annuler la vente' }).click();
    await expect(page.getByText(`Vente ${number} annulée`)).toBeVisible();
    await expect(page.getByText('Annulée', { exact: true })).toBeVisible();
    expect(await stockOf(request, stocked)).toBe('10.000');
  });

  /**
   * Brouillon de vente (stabilisation) : un seul brouillon créé, modification enregistrée avant
   * la validation, refus puis correction et nouvelle validation de la version corrigée.
   */
  test('brouillon : création, modification, validation refusée, correction, nouvelle validation', async ({
    page,
    request,
  }) => {
    const stocked = await stockedArticle(request);
    const { reference, customer, token } = stocked;
    await loginUi(page, OWNER.email, OWNER.password);
    await page.goto('/sales/new');
    await expect(page.getByRole('heading', { name: 'Nouvelle vente' })).toBeVisible();
    if (await page.locator('#sale-site').count()) {
      await page.locator('.p-dropdown', { has: page.locator('#sale-site') }).click();
      await page
        .locator('.p-dropdown-panel')
        .last()
        .locator('.p-dropdown-item', { hasText: stocked.siteName })
        .click();
    }
    await pick(page, 'sale-customer', customer);
    await page.getByRole('button', { name: 'Ajouter une ligne' }).click();
    await pick(page, 'line-0-article', reference);
    await page.locator('#line-0-quantity').fill('3');
    // Création : double clic, un seul brouillon.
    await page.getByRole('button', { name: 'Enregistrer le brouillon' }).dblclick();
    await expect(page.getByRole('heading', { name: 'Vente non numérotée' })).toBeVisible();

    // Modification puis validation : la quantité modifiée (12 > 10 en stock) est enregistrée
    // avant la validation, que le serveur refuse.
    await page.locator('#line-0-quantity').fill('12');
    await page.getByRole('button', { name: 'Valider la vente' }).click();
    let dialog = page.getByRole('dialog');
    await dialog.getByRole('button', { name: 'Valider la vente' }).click();
    await expect(
      dialog.getByText(`Stock insuffisant : ${reference} (disponible : 10).`),
    ).toBeVisible();
    const id = page.url().split('/').at(-1) ?? '';
    const persisted = (await (
      await request.get(`/api/v1/sales/${id}`, { headers: bearer(token) })
    ).json()) as { status: string; lines: { quantity: string }[] };
    expect(persisted.status).toBe('DRAFT');
    expect(persisted.lines.map((l) => l.quantity)).toEqual(['12.000']);

    // Correction puis nouvelle validation : la version corrigée est enregistrée et validée.
    await dialog.getByRole('button', { name: 'Annuler' }).click();
    await expect(dialog).toHaveCount(0);
    await page.locator('#line-0-quantity').fill('4');
    await page.getByRole('button', { name: 'Valider la vente' }).click();
    dialog = page.getByRole('dialog');
    await dialog.getByRole('button', { name: 'Valider la vente' }).click();
    await expect(page.getByText(new RegExp(`^Vente ${SALE_NUMBER.source} validée$`))).toBeVisible();
    await expect(page.getByText('Validée', { exact: true })).toBeVisible();

    const sales = (await (
      await request.get(`/api/v1/sales?search=${encodeURIComponent(customer)}`, {
        headers: bearer(token),
      })
    ).json()) as { total: number; items: { id: string; status: string }[] };
    expect(sales.total).toBe(1);
    expect(sales.items[0]).toMatchObject({ id, status: 'VALIDATED' });
    const sold = (await (
      await request.get(`/api/v1/sales/${id}`, { headers: bearer(token) })
    ).json()) as { lines: { quantity: string }[] };
    expect(sold.lines.map((l) => l.quantity)).toEqual(['4.000']);
    expect(await stockOf(request, stocked)).toBe('6.000');
  });

  test('nouvelle vente validée sans enregistrement préalable : refus conservé, correction, validation', async ({
    page,
    request,
  }) => {
    const stocked = await stockedArticle(request);
    const { reference, customer, token } = stocked;
    await loginUi(page, OWNER.email, OWNER.password);
    await page.goto('/sales/new');
    await expect(page.getByRole('heading', { name: 'Nouvelle vente' })).toBeVisible();
    if (await page.locator('#sale-site').count()) {
      await page.locator('.p-dropdown', { has: page.locator('#sale-site') }).click();
      await page
        .locator('.p-dropdown-panel')
        .last()
        .locator('.p-dropdown-item', { hasText: stocked.siteName })
        .click();
    }
    await pick(page, 'sale-customer', customer);
    await page.getByRole('button', { name: 'Ajouter une ligne' }).click();
    await pick(page, 'line-0-article', reference);
    await page.locator('#line-0-quantity').fill('12');
    // Validation directe : brouillon créé puis validation refusée ; le refus reste affiché.
    await page.getByRole('button', { name: 'Valider la vente' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByRole('button', { name: 'Valider la vente' }).click();
    await expect(
      dialog.getByText(`Stock insuffisant : ${reference} (disponible : 10).`),
    ).toBeVisible();
    // Dialogue fermé : fiche du brouillon créé.
    await dialog.getByRole('button', { name: 'Annuler' }).click();
    await expect(page.getByRole('heading', { name: 'Vente non numérotée' })).toBeVisible();
    await expect(page).toHaveURL(/\/sales\/[0-9a-f-]{36}$/);
    await page.locator('#line-0-quantity').fill('2');
    await page.getByRole('button', { name: 'Valider la vente' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Valider la vente' }).click();
    await expect(page.getByText(new RegExp(`^Vente ${SALE_NUMBER.source} validée$`))).toBeVisible();

    const sales = (await (
      await request.get(`/api/v1/sales?search=${encodeURIComponent(customer)}`, {
        headers: bearer(token),
      })
    ).json()) as { total: number; items: { status: string }[] };
    expect(sales.total).toBe(1);
    expect(sales.items[0]?.status).toBe('VALIDATED');
    expect(await stockOf(request, stocked)).toBe('8.000');
  });

  test('affichage mobile sans débordement @mobile', async ({ page, request }) => {
    const { reference } = await stockedArticle(request);
    await loginUi(page, OWNER.email, OWNER.password);
    await page.goto('/sales');
    await expect(page.getByRole('heading', { name: 'Ventes' })).toBeVisible();
    const overflow = () =>
      page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    expect(await overflow()).toBe(false);
    await page.getByRole('button', { name: 'Nouvelle vente' }).first().click();
    await page.getByRole('button', { name: 'Ajouter une ligne' }).click();
    await pick(page, 'line-0-article', reference);
    await expect(page.getByTestId('sale-total')).toHaveText(/1\s500/);
    expect(await overflow()).toBe(false);
  });
});
