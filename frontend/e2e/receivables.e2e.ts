import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import { apiToken, bearer, ensureCashOpen, loginUi, OWNER, STANDARD_OWNER } from './support';

/**
 * Phase 2.8 — Créances / comptes clients : créance = vente validée dont le reste dû (total −
 * paiements effectués) est positif, calculée par le serveur ; limite de crédit contrôlée à la
 * validation. Chaque test prépare par l'API un article vendu 10 000 (100 u en boutique, 20 u au
 * dépôt) et ses clients ; les données portent un suffixe unique (suite rejouable).
 */

interface Site {
  id: string;
  code: string;
}

interface Setup {
  token: string;
  suffix: string;
  articleId: string;
  shop: string;
  depot: string;
}

const DEPOT = { name: 'Dépôt E2E', code: 'DEPOT-E2E' };

async function post(request: APIRequestContext, token: string, path: string, data: unknown) {
  const response = await request.post(`/api/v1${path}`, { headers: bearer(token), data });
  expect(response.status(), await response.text()).toBeLessThan(300);
  if (response.status() === 204) return { id: '', number: '', code: '' };
  return (await response.json()) as { id: string; number: string; code: string };
}

/** Module Créances activé (une entreprise créée avant la Phase 2.8 l'active elle-même). */
async function enableReceivables(request: APIRequestContext, token: string) {
  const response = await request.put('/api/v1/modules/receivables', {
    headers: bearer(token),
    data: { enabled: true },
  });
  expect(response.status(), await response.text()).toBe(204);
}

async function setup(request: APIRequestContext): Promise<Setup> {
  const token = await apiToken(request, OWNER.email, OWNER.password);
  await enableReceivables(request, token);
  const headers = bearer(token);
  const sites = (await (await request.get('/api/v1/sites', { headers })).json()) as Site[];
  const shop = sites.find((s) => s.code !== DEPOT.code) as Site;
  const depot =
    sites.find((s) => s.code === DEPOT.code) ??
    ((await post(request, token, '/sites', { ...DEPOT, kind: 'warehouse' })) as Site);
  const suffix = Date.now().toString().slice(-7);
  const category = await post(request, token, '/catalog/categories', {
    name: `Créances E2E ${suffix}`,
  });
  const article = await post(request, token, '/catalog/articles', {
    reference: `E2E-R${suffix}`,
    designation: `Ciment E2E ${suffix}`,
    category_id: category.id,
    unit: 'sac',
    purchase_price: '6000',
    sale_price: '10000',
  });
  for (const [siteId, quantity] of [
    [shop.id, '100'],
    [depot.id, '20'],
  ] as const) {
    const entry = await post(request, token, '/stock/entries', {
      site_id: siteId,
      kind: 'INITIAL_STOCK',
      lines: [{ article_id: article.id, quantity, unit_cost: '6000' }],
    });
    await post(request, token, `/stock/entries/${entry.id}/validate`, {});
  }
  // Espèces : caisse ouverte en boutique (Phase 2.9).
  await ensureCashOpen(request, token, shop.id);
  return { token, suffix, articleId: article.id, shop: shop.id, depot: depot.id };
}

async function customer(request: APIRequestContext, s: Setup, name: string, limit?: string) {
  return post(request, s.token, '/customers', {
    customer_type: 'INDIVIDUAL',
    name: `${name} ${s.suffix}`,
    ...(limit ? { credit_limit: limit } : {}),
  });
}

/** Vente de `units` × 10 000 pour un client, brouillon ou validée sans paiement. */
async function sale(
  request: APIRequestContext,
  s: Setup,
  customerId: string,
  units: number,
  { validate = true, siteId = s.shop } = {},
) {
  const created = await post(request, s.token, '/sales', {
    site_id: siteId,
    customer_id: customerId,
    lines: [{ article_id: s.articleId, quantity: String(units) }],
  });
  if (validate) await post(request, s.token, `/sales/${created.id}/validate`, {});
  return created;
}

/** Montant affiché (espaces insécables du format XOF). */
const amount = (text: string) =>
  new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/ /g, '\\s'));

/** Créances filtrées par la recherche (numéro de vente ou client). */
async function openReceivables(page: Page, search: string) {
  await page.getByRole('link', { name: 'Créances', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Créances' })).toBeVisible();
  await page.getByRole('searchbox').fill(search);
}

const row = (page: Page, number: string) => page.getByRole('row').filter({ hasText: number });

async function recordPayment(page: Page, value: string) {
  await page.getByRole('button', { name: 'Enregistrer un paiement' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel(/^Montant/).fill(value);
  await dialog.getByRole('button', { name: 'Enregistrer le paiement' }).click();
  await expect(dialog).toBeHidden();
}

test.describe('Créances / comptes clients', () => {
  test('cycle : vente à crédit, créance, paiement partiel puis final, annulation', async ({
    page,
    request,
  }) => {
    const s = await setup(request);
    const client = await customer(request, s, 'Client Crédit', '500000');
    const draft = await sale(request, s, client.id, 10, { validate: false });
    await loginUi(page, OWNER.email, OWNER.password);
    // 1. Validation d'une vente à crédit (sans paiement).
    await page.goto(`/sales/${draft.id}`);
    await page.getByRole('button', { name: 'Valider la vente' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Valider la vente' }).click();
    await expect(page.getByText(`Vente ${draft.number} validée`)).toBeVisible();
    // 2. Elle apparaît dans Créances (menu « Ventes et clients »).
    await openReceivables(page, draft.number);
    await expect(row(page, draft.number)).toContainText(`Client Crédit ${s.suffix}`);
    await expect(row(page, draft.number)).toContainText(amount('100 000'));
    await expect(row(page, draft.number)).toContainText('Non payée');
    // 3. Paiement partiel depuis la fiche de la vente, ouverte depuis la créance.
    await row(page, draft.number).getByRole('button', { name: 'Ouvrir la vente' }).click();
    await expect(page).toHaveURL(new RegExp(`/sales/${draft.id}$`));
    await recordPayment(page, '40000');
    // 4. La créance diminue.
    await openReceivables(page, draft.number);
    await expect(row(page, draft.number)).toContainText(amount('60 000'));
    await expect(row(page, draft.number)).toContainText('Partiellement payée');
    // 5-6. Paiement final : la créance disparaît des créances ouvertes.
    await page.goto(`/sales/${draft.id}`);
    await recordPayment(page, '60000');
    await expect(page.getByText('Payée', { exact: true }).first()).toBeVisible();
    await openReceivables(page, draft.number);
    await expect(page.getByText('Aucun résultat')).toBeVisible();
    // 7-8. Annulation d'un paiement : la créance réapparaît.
    await page.goto(`/sales/${draft.id}`);
    const last = page
      .getByRole('row')
      .filter({ hasText: amount('60 000') })
      .filter({
        hasText: 'Effectué',
      });
    await last.getByRole('button', { name: 'Annuler le paiement' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel(/Motif d'annulation/).fill('Chèque impayé');
    await dialog.getByRole('button', { name: 'Annuler le paiement' }).click();
    await expect(page.getByText('Partiellement payée', { exact: true }).first()).toBeVisible();
    await openReceivables(page, draft.number);
    await expect(row(page, draft.number)).toContainText(amount('60 000'));
  });

  test('limite de crédit : refus au-delà, encaissement immédiat accepté, compte client', async ({
    page,
    request,
  }) => {
    const s = await setup(request);
    const client = await customer(request, s, 'Client Limité', '150000');
    await sale(request, s, client.id, 10); // exposition 100 000
    const draft = await sale(request, s, client.id, 10, { validate: false });
    await loginUi(page, OWNER.email, OWNER.password);
    await page.goto(`/sales/${draft.id}`);
    await page.getByRole('button', { name: 'Valider la vente' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByRole('button', { name: 'Valider la vente' }).click();
    // 100 000 + 100 000 > 150 000 : refusée par le serveur, dialogue conservé.
    await expect(page.getByText(/Limite de crédit du client dépassée/)).toBeVisible();
    await expect(page.getByText(amount('50 000 F CFA de crédit disponible'))).toBeVisible();
    await expect(dialog).toBeVisible();
    // Encaissement de 50 000 à la validation : exposition 150 000 = limite, acceptée.
    await dialog.getByLabel('Encaisser un paiement maintenant').check();
    await dialog.getByLabel(/^Montant/).fill('50000');
    await dialog.getByRole('button', { name: 'Valider la vente' }).click();
    await expect(page.getByText(`Vente ${draft.number} validée`)).toBeVisible();
    await expect(page.getByText('Partiellement payée', { exact: true }).first()).toBeVisible();
    // Compte client : limite, exposition, crédit disponible, créances.
    await page.goto(`/customers/${client.id}`);
    const credit = page.getByRole('group', { name: 'Crédit du client' });
    await expect(credit).toContainText(amount('150 000'));
    await expect(credit).toContainText('Exposition actuelle');
    await expect(credit).toContainText('2 créances ouvertes');
    await expect(credit.locator('.sm-metric').nth(2)).toContainText(amount('0'));
    await expect(page.getByRole('row').filter({ hasText: draft.number })).toContainText(
      amount('50 000'),
    );
  });

  test('client sans limite : « Non configurée », aucun crédit disponible affiché', async ({
    page,
    request,
  }) => {
    const s = await setup(request);
    const client = await customer(request, s, 'Client Libre');
    const validated = await sale(request, s, client.id, 30); // 300 000, aucune limite
    await loginUi(page, OWNER.email, OWNER.password);
    await page.goto(`/customers/${client.id}`);
    const credit = page.getByRole('group', { name: 'Crédit du client' });
    await expect(credit).toContainText('Non configurée');
    await expect(credit).toContainText(amount('300 000'));
    await expect(credit).not.toContainText('Crédit disponible');
    await expect(page.getByRole('row').filter({ hasText: validated.number })).toBeVisible();
  });

  test('isolation : site du vendeur, autre entreprise', async ({ page, request }) => {
    const s = await setup(request);
    const client = await customer(request, s, 'Client Multisite');
    const shopSale = await sale(request, s, client.id, 2);
    const depotSale = await sale(request, s, client.id, 3, { siteId: s.depot });
    // Vendeur limité à la boutique.
    const roles = (await (
      await request.get('/api/v1/roles', { headers: bearer(s.token) })
    ).json()) as { id: string; template_code: string | null }[];
    const email = `vendeur-boutique-${Date.now()}@example.com`;
    await post(request, s.token, '/members', {
      email,
      full_name: `Vendeur boutique ${s.suffix}`,
      password: 'Provisoire-E2E-2026',
      roles: [{ role_id: roles.find((r) => r.template_code === 'seller')?.id }],
      site_ids: [s.shop],
    });
    const first = await apiToken(request, email, 'Provisoire-E2E-2026');
    await post(request, first, '/me/password', {
      current_password: 'Provisoire-E2E-2026',
      new_password: 'E2e-Vendeur-2026',
    });
    await loginUi(page, email, 'E2e-Vendeur-2026');
    await openReceivables(page, `Client Multisite ${s.suffix}`);
    await expect(row(page, shopSale.number)).toBeVisible();
    await expect(page.getByRole('row').filter({ hasText: depotSale.number })).toHaveCount(0);
    await page.goto(`/customers/${client.id}`);
    await expect(page.getByText(/Exposition limitée à vos sites/)).toBeVisible();
    await expect(page.getByRole('group', { name: 'Crédit du client' })).toContainText(
      amount('20 000'),
    );
    // Autre entreprise : aucune créance, vente et client introuvables.
    const other = await apiToken(request, STANDARD_OWNER.email, STANDARD_OWNER.password);
    await enableReceivables(request, other);
    const headers = bearer(other);
    const list = await request.get(`/api/v1/receivables?search=${shopSale.number}`, { headers });
    expect(((await list.json()) as { total: number }).total).toBe(0);
    expect((await request.get(`/api/v1/receivables/${shopSale.id}`, { headers })).status()).toBe(
      404,
    );
    const exposure = await request.get(`/api/v1/customers/${client.id}/credit-exposure`, {
      headers,
    });
    expect(exposure.status()).toBe(404);
  });

  test('liste des créances sur mobile, sans débordement @mobile', async ({ page, request }) => {
    const s = await setup(request);
    const client = await customer(request, s, 'Client Mobile');
    const validated = await sale(request, s, client.id, 4);
    await loginUi(page, OWNER.email, OWNER.password);
    await page.goto('/receivables');
    await page.getByRole('searchbox').fill(validated.number);
    await expect(row(page, validated.number)).toBeVisible();
    const overflow = () =>
      page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    expect(await overflow()).toBe(false);
    await page.goto(`/customers/${client.id}`);
    await expect(page.getByRole('group', { name: 'Crédit du client' })).toBeVisible();
    expect(await overflow()).toBe(false);
  });
});
