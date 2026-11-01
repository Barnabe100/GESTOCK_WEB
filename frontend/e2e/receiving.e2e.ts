import { expect, test, type Page } from '@playwright/test';

import { call, choose, raw, setupRestaurant, type Resto } from './restaurant-support';
import { confirmFundsReceived, createActiveSite, loginUi } from './support';

/**
 * R6.5 (ADR-0051) — moyens de réception internationaux, parcours réel : opérateur personnalisé
 * (catalogue vide, jamais requis), identifiants communs et propres à un site, caisse (choix de
 * l'identifiant, confirmation MANUELLE explicite), reçu construit depuis l'instantané du
 * paiement, règle de devise (aucune conversion), isolation entre sites, mobile.
 */

const PASSWORD = 'E2e-Receiving-2026';
let r: Resto;
let method = '';

test.describe.configure({ mode: 'serial' });

test.beforeAll(async ({ request }) => {
  r = await setupRestaurant(request, 'Maquis Reception', PASSWORD);
});

type Account = { id: string; value: string; shared: boolean; site_id: string | null };
type PaymentOut = {
  method_label: string;
  provider_label: string | null;
  account_value: string | null;
  account_holder: string | null;
  confirmed_by_name: string | null;
  confirmed_at: string | null;
  currency: string;
};

async function openPos(page: Page) {
  await page.goto('/pos');
  await expect(page.getByRole('heading', { name: 'Point de vente' })).toBeVisible();
  await page.getByLabel('Rechercher un article (F2)').fill(r.itemLabel);
  await page.getByRole('button', { name: `Ajouter ${r.itemLabel} au panier` }).click();
}

const overflow = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);

test('administration : opérateur personnalisé, identifiants communs et du site', async ({
  page,
  request,
}) => {
  // Catalogue initial VIDE : jamais une condition pour enregistrer un moyen.
  expect(await call<unknown[]>(request, r.token, 'get', '/payment-providers')).toEqual([]);
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginUi(page, r.email, PASSWORD, r.name);
  await page.goto('/sales/payment-methods');
  await page.getByRole('button', { name: 'Nouveau moyen' }).first().click();
  let dialog = page.getByRole('dialog', { name: 'Nouveau moyen' });
  await dialog.getByLabel(/^Libellé/).fill('Paiement mobile');
  await choose(page, dialog, 'pm-provider', 'Opérateur personnalisé…');
  await dialog.getByLabel(/^Nom de l'opérateur/).fill('Opérateur Local');
  await dialog.getByRole('button', { name: 'Enregistrer' }).click();
  await expect(dialog).toBeHidden();
  const row = page.getByRole('row', { name: /Paiement mobile/ });
  await expect(row).toContainText('Opérateur Local');
  await expect(row).toContainText('BF · XOF');
  const methods = await call<{ id: string; label: string }[]>(
    request,
    r.token,
    'get',
    '/payment-methods',
  );
  method = methods.find((m) => m.label === 'Paiement mobile')?.id ?? '';

  // Identifiants : un commun à tous les sites, un propre au site (portées distinguées).
  await row.getByRole('button', { name: 'Identifiants de réception' }).click();
  const accounts = page.getByRole('dialog', { name: /Identifiants de réception/ });
  for (const [scope, type, value] of [
    ['Tous les sites', 'Code marchand', '445566'],
    [null, 'Téléphone', '70 11 22 33'],
  ] as const) {
    await accounts.getByRole('button', { name: 'Nouvel identifiant' }).click();
    dialog = page.getByRole('dialog', { name: 'Nouvel identifiant' });
    if (scope === null) {
      await dialog.locator('.p-dropdown', { has: page.locator('#acc-scope') }).click();
      await page.locator('.p-dropdown-panel').last().getByRole('option').nth(1).click();
    }
    await choose(page, dialog, 'acc-type', type);
    await dialog.getByLabel(/^Numéro ou code/).fill(value);
    await dialog.getByLabel(/^Titulaire/).fill('Maquis Centre');
    await dialog.getByRole('button', { name: 'Enregistrer' }).click();
    await expect(dialog).toBeHidden();
  }
  await expect(accounts.getByRole('row', { name: /445566/ })).toContainText('Tous les sites');
  // Téléphone : séparateurs retirés, aucun indicatif deviné.
  await expect(accounts.getByRole('row', { name: /70112233/ })).not.toContainText('Tous les sites');
  const saved = await call<Account[]>(
    request,
    r.token,
    'get',
    `/payment-methods/${method}/accounts`,
  );
  expect(saved.map((a) => [a.value, a.shared]).sort()).toEqual([
    ['445566', true],
    ['70112233', false],
  ]);
});

test('caisse : identifiant choisi, confirmation explicite, reçu depuis l’instantané', async ({
  page,
  request,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginUi(page, r.email, PASSWORD, r.name);
  await openPos(page);
  await page.keyboard.press('F8');
  const dialog = page.getByRole('dialog', { name: 'Paiements (F8)' });
  await dialog
    .getByRole('group', { name: 'Ajouter un moyen de paiement' })
    .getByRole('button', { name: 'Paiement mobile' })
    .click();
  await expect(dialog.getByTestId('pos-pay-1-provider')).toContainText('Opérateur Local');
  // Deux identifiants actifs sur le site : aucun présélectionné, choix du caissier.
  await choose(page, dialog, 'pos-pay-1-account', /445566/);
  await expect(dialog.getByTestId('pos-pay-1-account-details')).toContainText(
    'Code marchand 445566 (Maquis Centre)',
  );
  // Choisir le moyen ne suffit jamais : confirmation explicite.
  await dialog.getByRole('button', { name: 'Appliquer' }).click();
  await expect(dialog.getByText('Confirmez la réception effective des fonds.')).toBeVisible();
  await confirmFundsReceived(dialog);
  await dialog.getByRole('button', { name: 'Appliquer' }).click();
  await expect(dialog).toBeHidden();
  await page.keyboard.press('F10');
  await page
    .getByRole('dialog', { name: 'Valider la vente ?' })
    .getByRole('button', { name: 'Valider la vente (F10)' })
    .click();
  const receipt = page.getByTestId('pos-receipt');
  await expect(receipt.getByTestId('receipt-provider')).toHaveText('Opérateur Local');
  await expect(receipt.getByTestId('receipt-account')).toHaveText(
    'Code marchand 445566 (Maquis Centre)',
  );

  // La configuration change ensuite : le paiement et le reçu gardent l'instantané.
  const sales = await call<{ items: { id: string }[] }>(
    request,
    r.token,
    'get',
    `/sales?channel=POS&sort=-created_at&limit=1`,
  );
  const saleId = sales.items[0]?.id ?? '';
  const shared = (
    await call<Account[]>(request, r.token, 'get', `/payment-methods/${method}/accounts`)
  ).find((a) => a.value === '445566');
  await call(request, r.token, 'patch', `/payment-methods/accounts/${shared?.id}`, {
    value: '999999',
  });
  const history = await call<{ items: PaymentOut[] }>(
    request,
    r.token,
    'get',
    `/sales/${saleId}/payments`,
  );
  const [payment] = history.items;
  expect([payment?.account_value, payment?.account_holder, payment?.currency]).toEqual([
    '445566',
    'Maquis Centre',
    'XOF',
  ]);
  expect(payment?.confirmed_at).toBeTruthy();
  expect(payment?.confirmed_by_name).toBeTruthy();
  const printed = await call<{ payments: PaymentOut[] }>(
    request,
    r.token,
    'get',
    `/sales/${saleId}/receipt`,
  );
  expect(printed.payments[0]?.account_value).toBe('445566');
});

test('devise : un moyen dans une autre devise n’encaisse jamais la vente', async ({ request }) => {
  const euro = await call(request, r.token, 'post', '/payment-methods', {
    label: 'Carte EUR',
    kind: 'CARD',
    country_code: null,
    currency: 'EUR',
  });
  const onSite = await call<{ id: string; available: boolean; unavailable_reason: string }[]>(
    request,
    r.token,
    'get',
    `/payment-methods?site_id=${r.site}`,
  );
  const listed = onSite.find((m) => m.id === euro.id);
  expect([listed?.available, listed?.unavailable_reason]).toEqual([false, 'currency_mismatch']);
  const refused = await raw(request, r.token, 'post', '/pos/checkout', {
    site_id: r.site,
    lines: [{ article_id: r.article, quantity: '1' }],
    payments: [{ payment_method_id: euro.id, amount: '2500', received_confirmed: true }],
    idempotency_key: crypto.randomUUID(),
  });
  expect([refused.status, refused.code]).toEqual([422, 'payment_method_currency_mismatch']);
  const unconfirmed = await raw(request, r.token, 'post', '/pos/checkout', {
    site_id: r.site,
    lines: [{ article_id: r.article, quantity: '1' }],
    payments: [{ payment_method_id: method, amount: '2500' }],
    idempotency_key: crypto.randomUUID(),
  });
  expect([unconfirmed.status, unconfirmed.code]).toEqual([422, 'payment_confirmation_required']);
});

test('isolation : les identifiants d’un site restent invisibles depuis un autre site', async ({
  request,
}) => {
  const annex = await createActiveSite<{ id: string }>(request, r.token, {
    name: `Annexe ${r.suffix}`,
    code: `RCV${r.suffix.slice(-4)}`,
    business_profile_code: 'restaurant.maquis',
  });
  await call(request, r.token, 'post', `/payment-methods/${method}/accounts`, {
    site_id: annex.id,
    identifier_type: 'AGENT_CODE',
    value: 'AG-7',
  });
  const onMain = await call<{ id: string; accounts: Account[] }[]>(
    request,
    r.token,
    'get',
    `/payment-methods?site_id=${r.site}`,
  );
  const values = onMain.find((m) => m.id === method)?.accounts.map((a) => a.value) ?? [];
  expect(values).not.toContain('AG-7');
  expect(values).toContain('70112233');
  const onAnnex = await call<{ id: string; accounts: Account[] }[]>(
    request,
    r.token,
    'get',
    `/payment-methods?site_id=${annex.id}`,
  );
  const annexValues = onAnnex.find((m) => m.id === method)?.accounts.map((a) => a.value) ?? [];
  expect(annexValues).toContain('AG-7');
  expect(annexValues).not.toContain('70112233');
});

test('@mobile caisse : coordonnées et confirmation lisibles, sans débordement', async ({
  page,
  request,
}) => {
  const m = await setupRestaurant(request, 'Maquis Reception Mobile', PASSWORD);
  const created = await call(request, m.token, 'post', '/payment-methods', {
    label: 'Paiement mobile',
    kind: 'MOBILE_MONEY',
    provider_name: 'Opérateur Local',
  });
  await call(request, m.token, 'post', `/payment-methods/${created.id}/accounts`, {
    site_id: null,
    identifier_type: 'MERCHANT_CODE',
    value: '445566',
    holder_name: 'Maquis Centre',
  });
  r = m;
  await loginUi(page, m.email, PASSWORD, m.name);
  await openPos(page);
  await page.keyboard.press('F8');
  const dialog = page.getByRole('dialog', { name: 'Paiements (F8)' });
  await dialog
    .getByRole('group', { name: 'Ajouter un moyen de paiement' })
    .getByRole('button', { name: 'Paiement mobile' })
    .click();
  // Un seul identifiant actif : présélectionné.
  await expect(dialog.getByTestId('pos-pay-1-account-details')).toContainText('445566');
  await expect(dialog.locator('label', { hasText: "J'ai vérifié" })).toBeVisible();
  expect(await overflow(page)).toBe(false);
  await confirmFundsReceived(dialog);
  await dialog.getByRole('button', { name: 'Appliquer' }).click();
  await expect(dialog).toBeHidden();
  await page.goto('/sales/payment-methods');
  await expect(page.getByRole('heading', { name: /Moyens de paiement/ })).toBeVisible();
  expect(await overflow(page)).toBe(false);
});
