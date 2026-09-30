import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import {
  bearer,
  createActiveSite,
  loginUi,
  OWNER,
  apiToken,
  provisionTenant,
  SALE_NUMBER,
  tokenFor,
} from './support';

/**
 * Lot 1 — Encaissement (ADR-0037), sur une entreprise créée pour l'exécution (deux sites sans
 * caisse au départ : la caisse est optionnelle par site). Scénarios : vente en espèces sans
 * caisse (montant reçu, monnaie du serveur, numéro VENT-…), moyen configuré (Orange Money,
 * référence obligatoire) sur un site sans caisse, paiement mixte, caisse activée (session
 * site + poste + utilisateur, désactivation refusée tant qu'une session est ouverte, puis
 * possible après clôture), crédit avec / sans client, isolation entre sites et entreprises.
 */

const RUN = Date.now().toString().slice(-7);
const TENANT = `Encaissement E2E ${RUN}`;
const EMAIL = `encaissement-${RUN}@example.com`;
const PASSWORD = 'E2e-Encaissement-2026';
const YEAR = new Date().getFullYear();

interface Site {
  id: string;
  name: string;
  code: string;
}

interface World {
  token: string;
  main: Site;
  bobo: Site;
  customer: { id: string; name: string };
}

let world: World;

async function post<T = { id: string }>(
  request: APIRequestContext,
  path: string,
  data: unknown,
  token = world.token,
): Promise<T> {
  const response = await request.post(`/api/v1${path}`, { headers: bearer(token), data });
  expect(response.status(), await response.text()).toBeLessThan(300);
  return (await response.json()) as T;
}

async function get<T>(request: APIRequestContext, path: string, token = world.token): Promise<T> {
  const response = await request.get(`/api/v1${path}`, { headers: bearer(token) });
  expect(response.status(), await response.text()).toBe(200);
  return (await response.json()) as T;
}

test.describe.configure({ mode: 'serial' });

test.beforeAll(async ({ request }) => {
  await provisionTenant(request, {
    name: TENANT,
    profile: 'retail.quincaillerie',
    email: EMAIL,
    password: PASSWORD,
  });
  const token = await tokenFor(request, EMAIL, PASSWORD, TENANT);
  const sites = (await (
    await request.get('/api/v1/sites', { headers: bearer(token) })
  ).json()) as Site[];
  const main = sites[0] as Site;
  const bobo =
    sites.find((s) => s.code === 'BOBO') ??
    (await createActiveSite<Site>(request, token, { name: 'Bobo', code: 'BOBO' }));
  world = { token, main, bobo, customer: { id: '', name: '' } };
  // Deux articles : 7 500 (exemple de la monnaie) et 10 000 ; stock sur les deux sites.
  const category = await post(request, '/catalog/categories', { name: `Encaissement ${RUN}` });
  for (const [reference, price] of [
    ['ENC-7500', '7500'],
    ['ENC-10000', '10000'],
  ] as const) {
    const article = await post(request, '/catalog/articles', {
      reference,
      designation: `Article ${price} ${RUN}`,
      category_id: category.id,
      unit: 'u',
      purchase_price: '1000',
      sale_price: price,
    });
    for (const site of [main, bobo]) {
      const entry = await post(request, '/stock/entries', {
        site_id: site.id,
        kind: 'INITIAL_STOCK',
        lines: [{ article_id: article.id, quantity: '100', unit_cost: '1000' }],
      });
      await post(request, `/stock/entries/${entry.id}/validate`, {});
    }
  }
  const name = `Client Crédit ${RUN}`;
  const customer = await post(request, '/customers', { customer_type: 'INDIVIDUAL', name });
  world.customer = { id: customer.id, name };
});

/** Montant affiché (espaces insécables du format XOF). */
const amount = (text: string) =>
  new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/ /g, '\\s'));

const RECORDED = new RegExp(`^Vente ${SALE_NUMBER.source} enregistrée$`);

/** Point de vente sur un site ; connexion d'abord, sauf si la page est déjà connectée. */
async function openPos(page: Page, site: Site, login = true) {
  if (login) await loginUi(page, EMAIL, PASSWORD, TENANT);
  await page.goto('/pos');
  await expect(page.getByRole('heading', { name: 'Point de vente' })).toBeVisible();
  await page.locator('.sm-pos-header .p-dropdown').click();
  await page.locator('.p-dropdown-panel').last().getByText(site.name, { exact: true }).click();
}

async function addToCart(page: Page, price: string, times = 1) {
  await page.getByLabel('Rechercher un article (F2)').fill(`Article ${price} ${RUN}`);
  const tile = page.getByRole('button', { name: `Ajouter Article ${price} ${RUN} au panier` });
  for (let i = 0; i < times; i += 1) await tile.click();
}

/** Paiements (F8) : [moyen configuré, montant (espèces : reçu), référence facultative]. */
async function pay(page: Page, payments: [string, string, string?][]) {
  await page.keyboard.press('F8');
  const dialog = page.getByRole('dialog', { name: 'Paiements (F8)' });
  const methods = dialog.getByRole('group', { name: 'Ajouter un moyen de paiement' });
  for (const [index, [method, value, reference]] of payments.entries()) {
    await methods.getByRole('button', { name: method, exact: true }).click();
    await dialog.getByLabel(new RegExp(`^Montant (reçu )?du paiement ${index + 1}$`)).fill(value);
    if (reference) await dialog.getByLabel(`Référence du paiement ${index + 1}`).fill(reference);
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

async function receiptNumber(page: Page): Promise<string> {
  const title = page.locator('.p-dialog-title', { hasText: RECORDED });
  await expect(title).toBeVisible();
  return ((await title.innerText()).match(SALE_NUMBER) ?? [''])[0];
}

interface SaleOut {
  id: string;
  number: string;
  payment_status: string;
  is_credit: boolean;
  credit_status: string | null;
  customer_id: string | null;
}

interface PaymentOut {
  method: string;
  method_label: string;
  amount: string;
  amount_received: string | null;
  change_given: string | null;
  reference: string | null;
}

async function saleByNumber(request: APIRequestContext, number: string) {
  const page = await get<{ items: SaleOut[] }>(request, `/sales?search=${number}`);
  expect(page.items).toHaveLength(1);
  const sale = page.items[0] as SaleOut;
  const payments = await get<{ items: PaymentOut[] }>(request, `/sales/${sale.id}/payments`);
  return { sale, payments: payments.items };
}

async function cashMovements(request: APIRequestContext, number: string) {
  return (await get<{ items: { amount: string }[] }>(request, `/cash/movements?search=${number}`))
    .items;
}

test.describe('Encaissement (Lot 1)', () => {
  test('1, 9, 10 — site sans caisse : espèces sans session, monnaie du serveur, numéro VENT', async ({
    page,
    request,
  }) => {
    await openPos(page, world.main);
    // Caisse optionnelle : aucun état de caisse affiché pour un site sans caisse.
    await expect(page.getByText(/Aucune session de caisse ouverte/)).toHaveCount(0);
    await addToCart(page, '7500');
    await pay(page, [['Espèces', '10000']]);
    await validate(page);
    const number = await receiptNumber(page);
    // Premier numéro du site pour l'année : VENT-{CODE_SITE}-{ANNÉE}-000001.
    expect(number).toBe(`VENT-${world.main.code.toUpperCase()}-${YEAR}-000001`);
    const receipt = page.getByTestId('pos-receipt');
    await expect(receipt).toContainText(amount('Espèces reçues 10 000'));
    await expect(receipt.getByTestId('receipt-change')).toHaveText(amount('2 500'));
    const { sale, payments } = await saleByNumber(request, number);
    expect([sale.payment_status, sale.is_credit, sale.customer_id]).toEqual(['PAID', false, null]);
    expect(payments.map((p) => [p.method, p.amount, p.amount_received, p.change_given])).toEqual([
      ['CASH', '7500.00', '10000.00', '2500.00'],
    ]);
    // Aucune session, aucun mouvement de caisse.
    expect(await cashMovements(request, number)).toEqual([]);
    expect((await get<{ total: number }>(request, '/cash/sessions?status=OPEN')).total).toBe(0);
    // Vente ordinaire : client « Ordinaire » sur la fiche.
    await page.goto(`/sales/${sale.id}`);
    await expect(page.getByRole('heading', { name: `Vente ${number}` })).toBeVisible();
    await expect(page.getByText('Ordinaire', { exact: true })).toBeVisible();
  });

  test('5 — moyen configuré (Orange Money, référence obligatoire) sur un site sans caisse', async ({
    page,
    request,
  }) => {
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    // Configuration des moyens de paiement (libellé libre, type = comportement).
    await page.goto('/sales/payment-methods');
    await expect(page.getByRole('heading', { name: 'Moyens de paiement' })).toBeVisible();
    await page.getByRole('button', { name: 'Nouveau moyen' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel(/^Libellé/).fill('Orange Money');
    await dialog.getByLabel('Référence obligatoire').check();
    await dialog.getByRole('button', { name: 'Enregistrer' }).click();
    await expect(page.getByText('Moyen « Orange Money » créé')).toBeVisible();
    const row = page.getByRole('row').filter({ hasText: 'Orange Money' });
    await expect(row).toContainText('Mobile Money');
    await expect(row).toContainText('Manuelle');

    await openPos(page, world.bobo, false);
    await addToCart(page, '7500');
    // Référence exigée par le moyen : refus avant envoi, puis saisie.
    await page.keyboard.press('F8');
    const payments = page.getByRole('dialog', { name: 'Paiements (F8)' });
    await payments
      .getByRole('group', { name: 'Ajouter un moyen de paiement' })
      .getByRole('button', { name: 'Orange Money', exact: true })
      .click();
    await payments.getByRole('button', { name: 'Appliquer' }).click();
    await expect(payments.getByText('Référence de la transaction obligatoire')).toBeVisible();
    await payments.getByLabel('Référence du paiement 1').fill('OM-778899');
    await payments.getByRole('button', { name: 'Appliquer' }).click();
    await validate(page);
    const number = await receiptNumber(page);
    // Séquence propre au site : premier numéro de Bobo.
    expect(number).toBe(`VENT-BOBO-${YEAR}-000001`);
    await expect(page.getByTestId('pos-receipt')).toContainText('Orange Money');
    await expect(page.getByTestId('receipt-change')).toHaveCount(0);
    const { payments: recorded } = await saleByNumber(request, number);
    expect(recorded.map((p) => [p.method, p.method_label, p.reference, p.change_given])).toEqual([
      ['MOBILE_MONEY', 'Orange Money', 'OM-778899', null],
    ]);
    expect(await cashMovements(request, number)).toEqual([]);
  });

  test('8, 9 — paiement mixte : monnaie sur la seule partie espèces', async ({ page, request }) => {
    await openPos(page, world.main);
    await addToCart(page, '10000', 5); // 50 000
    await expect(page.getByTestId('pos-total')).toHaveText(amount('50 000'));
    // Espèces 20 000 + Orange Money 10 000, puis 30 000 en espèces pour solder 20 000.
    await pay(page, [
      ['Espèces', '20000'],
      ['Orange Money', '10000', 'OM-MIX-1'],
      ['Espèces', '30000'],
    ]);
    await validate(page);
    const number = await receiptNumber(page);
    expect(number).toBe(`VENT-${world.main.code.toUpperCase()}-${YEAR}-000002`);
    await expect(page.getByTestId('receipt-remaining')).toHaveText(amount('0'));
    const { sale, payments } = await saleByNumber(request, number);
    expect(sale.payment_status).toBe('PAID');
    expect(
      payments
        .map((p) => [p.method_label, p.amount, p.amount_received, p.change_given])
        .sort((a, b) => String(a).localeCompare(String(b))),
    ).toEqual(
      [
        ['Espèces', '20000.00', '20000.00', '0.00'],
        ['Espèces', '20000.00', '30000.00', '10000.00'],
        ['Orange Money', '10000.00', null, null],
      ].sort((a, b) => String(a).localeCompare(String(b))),
    );
  });

  test('2, 3, 4 — caisse activée : session, vente espèces, désactivation refusée puis permise', async ({
    page,
    request,
  }) => {
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto('/cash/registers');
    await expect(page.getByRole('heading', { name: 'Postes de caisse' })).toBeVisible();
    // Activation de la caisse de Bobo (configuration du site).
    const bobo = page.getByTestId(`cash-site-${world.bobo.id}`);
    await expect(bobo).toContainText('Sans caisse');
    await bobo.getByRole('button', { name: 'Activer la caisse — Bobo' }).click();
    await expect(bobo).toContainText('Caisse activée');
    // Poste = ordinateur de caisse ; ouverture de la session de l'utilisateur.
    await post(request, '/cash/registers', { site_id: world.bobo.id, name: `Poste 1 ${RUN}` });
    await page.reload();
    const row = page.getByRole('row').filter({ hasText: `Poste 1 ${RUN}` });
    await row.getByRole('button', { name: 'Ouvrir la caisse' }).click();
    const opening = page.getByRole('dialog');
    await opening.getByLabel(/Fond initial/).fill('5000');
    await opening.getByRole('button', { name: 'Ouvrir la caisse' }).click();
    await expect(page.getByText(/Session SES-\d{6} ouverte/)).toBeVisible();
    const sessionUrl = page.url();

    // Vente en espèces sur Bobo : encaissée dans la session de l'utilisateur.
    await openPos(page, world.bobo, false);
    await expect(page.getByText(`Caisse ouverte : Poste 1 ${RUN}`)).toBeVisible();
    await addToCart(page, '7500');
    await pay(page, [['Espèces', '10000']]);
    await validate(page);
    const number = await receiptNumber(page);
    expect((await cashMovements(request, number)).map((m) => m.amount)).toEqual(['7500.00']);

    // Désactivation demandée avec une session ouverte : refusée, message explicite.
    await page.goto('/cash/registers');
    await page
      .getByTestId(`cash-site-${world.bobo.id}`)
      .getByRole('button', { name: 'Désactiver la caisse — Bobo' })
      .click();
    await page.getByRole('button', { name: 'Désactiver la caisse', exact: true }).last().click();
    await expect(page.getByText(/clôturez-la avant de désactiver la caisse/)).toBeVisible();
    await expect(page.getByTestId(`cash-site-${world.bobo.id}`)).toContainText('Caisse activée');

    // Clôture (théorique 5 000 + 7 500 = 12 500), puis désactivation possible.
    await page.goto(sessionUrl);
    await page.getByRole('button', { name: 'Clôturer la caisse' }).click();
    const closing = page.getByRole('dialog');
    await expect(closing.getByTestId('close-theoretical')).toHaveText(amount('12 500'));
    await closing.getByLabel(/Montant compté/).fill('12500');
    await closing.getByLabel(/Je confirme/).check();
    await closing.getByRole('button', { name: 'Clôturer la caisse' }).click();
    await expect(page.getByText(/Session SES-\d{6} clôturée/)).toBeVisible();
    await page.goto('/cash/registers');
    const site = page.getByTestId(`cash-site-${world.bobo.id}`);
    await site.getByRole('button', { name: 'Désactiver la caisse — Bobo' }).click();
    await page.getByRole('button', { name: 'Désactiver la caisse', exact: true }).last().click();
    await expect(page.getByText('Caisse désactivée pour Bobo')).toBeVisible();
    await expect(site).toContainText('Sans caisse');

    // Sans caisse : vente en espèces de nouveau possible sans session ; historique conservé.
    await openPos(page, world.bobo, false);
    await addToCart(page, '7500');
    await pay(page, [['Espèces', '7500']]);
    await validate(page);
    const after = await receiptNumber(page);
    expect(await cashMovements(request, after)).toEqual([]);
    expect((await cashMovements(request, number)).map((m) => m.amount)).toEqual(['7500.00']);
  });

  test('6, 7 — crédit : refusé sans client, accepté avec un client identifié', async ({
    page,
    request,
  }) => {
    await openPos(page, world.main);
    await addToCart(page, '10000', 2);
    // Aucun paiement, aucun client : refus du serveur, panier conservé.
    const refused = await validate(page);
    await expect(refused).toContainText('une vente à crédit exige un client identifié');
    await expect(refused.getByText(/Une vente à crédit exige un client identifié/)).toBeVisible();
    await refused.getByRole('button', { name: 'Annuler' }).click();
    // Client choisi (F4) : vente à crédit acceptée.
    await page.keyboard.press('F4');
    const chooser = page.getByRole('dialog', { name: 'Client de la vente (F4)' });
    await chooser.getByRole('searchbox').fill(world.customer.name);
    await chooser.getByRole('button', { name: new RegExp(world.customer.name) }).click();
    await validate(page);
    const number = await receiptNumber(page);
    const { sale } = await saleByNumber(request, number);
    expect([sale.customer_id, sale.is_credit, sale.credit_status, sale.payment_status]).toEqual([
      world.customer.id,
      true,
      'OPEN',
      'UNPAID',
    ]);
    await page.goto(`/sales/${sale.id}`);
    await expect(page.getByText('Crédit ouvert')).toBeVisible();
  });

  test('11 — isolation : site A ≠ site B de la même entreprise ≠ autre entreprise', async ({
    request,
  }) => {
    const sales = await get<{ items: SaleOut[] }>(request, '/sales?limit=100');
    const onBobo = sales.items.find((s) => s.number.startsWith('VENT-BOBO-')) as SaleOut;
    const onMain = sales.items.find((s) =>
      s.number.startsWith(`VENT-${world.main.code.toUpperCase()}-`),
    ) as SaleOut;
    // Gestionnaire limité au site principal.
    const roles = await get<{ id: string; template_code: string | null }[]>(request, '/roles');
    const email = `gestion-principal-${Date.now()}@example.com`;
    await post(request, '/members', {
      email,
      full_name: `Gestion principal ${RUN}`,
      password: 'Provisoire-E2E-2026',
      roles: [{ role_id: roles.find((r) => r.template_code === 'manager')?.id }],
      site_ids: [world.main.id],
    });
    const first = await tokenFor(request, email, 'Provisoire-E2E-2026', TENANT);
    const changed = await request.post('/api/v1/me/password', {
      headers: bearer(first),
      data: { current_password: 'Provisoire-E2E-2026', new_password: 'E2e-Gestion-2026' },
    });
    expect(changed.status()).toBe(204);
    const manager = await tokenFor(request, email, 'E2e-Gestion-2026', TENANT);
    const managerSales = await get<{ items: SaleOut[] }>(request, '/sales?limit=100', manager);
    expect(managerSales.items.some((s) => s.id === onMain.id)).toBe(true);
    expect(managerSales.items.some((s) => s.id === onBobo.id)).toBe(false);
    const hidden = await request.get(`/api/v1/sales/${onBobo.id}`, { headers: bearer(manager) });
    expect(hidden.status()).toBe(404);
    const boboMethods = await request.get(`/api/v1/payment-methods?site_id=${world.bobo.id}`, {
      headers: bearer(manager),
    });
    expect(boboMethods.status()).toBe(404);

    // Autre entreprise : ni vente, ni moyens de paiement, ni caisse de ce tenant.
    const other = await apiToken(request, OWNER.email, OWNER.password);
    for (const path of [`/sales/${onMain.id}`, `/sales/${onBobo.id}/payments`]) {
      expect((await request.get(`/api/v1${path}`, { headers: bearer(other) })).status()).toBe(404);
    }
    const ours = await get<{ id: string }[]>(request, '/payment-methods');
    const theirs = await get<{ id: string }[]>(request, '/payment-methods', other);
    expect(ours.some((m) => theirs.some((t) => t.id === m.id))).toBe(false);
    const patched = await request.patch(`/api/v1/payment-methods/${ours[0]?.id}`, {
      headers: bearer(other),
      data: { label: 'Piraté' },
    });
    expect(patched.status()).toBe(404);
    const cash = await request.put(`/api/v1/cash/sites/${world.bobo.id}`, {
      headers: bearer(other),
      data: { enabled: true },
    });
    expect(cash.status()).toBe(404);
  });
});
