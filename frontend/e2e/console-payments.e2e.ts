import {
  expect,
  request as playwrightRequest,
  test,
  type APIRequestContext,
  type Page,
} from '@playwright/test';

import { adminCliWithInput, bearer, loginUi, ownerSql, provisionTenant, tokenFor } from './support';

/**
 * Phase 3.3-A — Paiements d'abonnement (ADR-0032) : l'entreprise déclare, TechNova confirme
 * ou rejette (décision définitive), un paiement confirmé n'active rien. Chaque exécution crée
 * ses administrateurs TechNova et ses entreprises (noms, références et raisons uniques).
 */

const ADMIN_PASSWORD = 'E2e-Console-Payments-2026';
const OWNER_PASSWORD = 'E2e-Tenant-Payments-2026';
const CONSOLE = '/platform-api/v1';
const CONSOLE_HEADERS = { 'X-TechNova-Console': '1' };

const stampOf = () => `${Date.now().toString().slice(-8)}${Math.floor(Math.random() * 90 + 10)}`;

function createPlatformAdmin(stamp: string, suffix = ''): string {
  const email = `e2e-tn-pay${suffix}-${stamp}@technova.example`;
  adminCliWithInput(
    `${ADMIN_PASSWORD}\n`,
    'platform-admin',
    'create',
    '--email',
    email,
    '--name',
    'Admin Paiements E2E',
    '--password-stdin',
  );
  return email;
}

/** Entreprise dont l'abonnement attend son activation (inscription sans essai, ADR-0025). */
async function pendingTenant(request: APIRequestContext, label: string) {
  const name = `Paiements ${label}`;
  const email = `e2e-pay-${label.replace(/\s+/g, '-').toLowerCase()}@example.com`;
  await provisionTenant(request, {
    name,
    profile: 'retail.alimentation',
    email,
    password: OWNER_PASSWORD,
  });
  ownerSql(
    "UPDATE subscriptions SET status = 'pending_activation', current_period_end = now() " +
      `WHERE tenant_id = (SELECT id FROM tenants WHERE name = '${name}')`,
  );
  const token = await tokenFor(request, email, OWNER_PASSWORD, name);
  return { name, email, token };
}

async function declare(request: APIRequestContext, token: string, reference: string) {
  const subscription = (await (
    await request.get('/api/v1/subscription', { headers: bearer(token) })
  ).json()) as { id: string };
  const response = await request.post('/api/v1/subscription/payments', {
    headers: bearer(token),
    data: {
      subscription_id: subscription.id,
      amount: '15000',
      period_start: '2026-10-01',
      period_end: '2026-11-01',
      payment_method: 'MOBILE_MONEY',
      declared_reference: reference,
      idempotency_key: crypto.randomUUID(),
    },
  });
  expect(response.status(), await response.text()).toBe(201);
  return (await response.json()) as { id: string; status: string };
}

async function consoleLogin(page: Page, email: string) {
  await page.goto('/tech-admin/login');
  await page.locator('#email').fill(email);
  await page.locator('#password').fill(ADMIN_PASSWORD);
  await page.getByRole('button', { name: 'Se connecter' }).click();
  await expect(page.getByTestId('console-identity')).toBeVisible();
}

async function consoleApi(baseURL: string | undefined, email: string) {
  const context = await playwrightRequest.newContext({ baseURL });
  const login = await context.post(`${CONSOLE}/auth/login`, {
    headers: CONSOLE_HEADERS,
    data: { email, password: ADMIN_PASSWORD },
  });
  expect(login.status(), await login.text()).toBe(200);
  return context;
}

/** Ouvre la fiche d'un paiement depuis la liste de la console (recherche par référence). */
async function openPayment(page: Page, reference: string) {
  await page.locator('#console-sidebar').getByRole('link', { name: 'Paiements' }).click();
  await page.getByPlaceholder('Rechercher une référence').fill(reference);
  const row = page.getByRole('row').filter({ hasText: reference });
  await expect(row).toHaveCount(1);
  await expect(row).toContainText('En attente');
  await row.getByRole('button', { name: `Ouvrir le paiement ${reference}` }).click();
  await expect(page.getByTestId('payment-status')).toContainText('En attente');
}

async function decide(page: Page, action: string, reason: string) {
  await page.getByTestId('payment-actions').getByRole('button', { name: action }).click();
  const dialog = page.getByRole('dialog');
  await dialog.locator('#decision-reason').fill(reason);
  await dialog.getByText('Je confirme cette décision définitive.').click();
  await dialog.getByRole('button', { name: action }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByTestId('payment-actions')).toHaveCount(0);
}

test.describe("Paiements d'abonnement : entreprise et console TechNova", () => {
  test('déclaration, confirmation, rejet motivé, aucune activation', async ({ page, request }) => {
    const stamp = stampOf();
    const tenant = await pendingTenant(request, stamp);
    const confirmedRef = `VIR-${stamp}`;
    const rejectedRef = `OM-${stamp}`;

    // 1. L'entreprise déclare un paiement : « En attente ».
    await loginUi(page, tenant.email, OWNER_PASSWORD, tenant.name);
    await page.goto('/subscription');
    await page.getByRole('button', { name: 'Déclarer un paiement' }).click();
    const form = page.getByRole('dialog');
    await form.locator('#subscription-payment-amount').fill('10000');
    await form.locator('#subscription-payment-start').fill('2026-10-01');
    await form.locator('#subscription-payment-end').fill('2026-11-01');
    await form.locator('#subscription-payment-reference').fill(confirmedRef);
    await form.getByRole('button', { name: 'Déclarer le paiement' }).click();
    await expect(form).toHaveCount(0);
    const declared = page.getByRole('row').filter({ hasText: confirmedRef });
    await expect(declared).toContainText('En attente');
    await expect(declared).toContainText('Virement bancaire');
    await declare(request, tenant.token, rejectedRef);

    // 2. TechNova confirme.
    const admin = createPlatformAdmin(stamp);
    const consolePage = await page.context().newPage();
    await consoleLogin(consolePage, admin);
    await openPayment(consolePage, confirmedRef);
    await expect(consolePage.getByText(/n'active pas l'abonnement/).first()).toBeVisible();
    await decide(consolePage, 'Confirmer le paiement', `Virement reçu ${stamp}`);
    await expect(consolePage.getByTestId('payment-status')).toContainText('Confirmé');
    await expect(
      consolePage.getByRole('row').filter({ hasText: 'subscription_payment.confirmed' }),
    ).toContainText(`Virement reçu ${stamp}`);

    // 3. TechNova rejette avec un motif, visible par l'entreprise.
    const motive = `Référence introuvable ${stamp}`;
    await openPayment(consolePage, rejectedRef);
    await decide(consolePage, 'Rejeter le paiement', motive);
    await expect(consolePage.getByTestId('payment-rejection-reason')).toHaveText(motive);

    await page.reload();
    await expect(page.getByRole('row').filter({ hasText: confirmedRef })).toContainText('Confirmé');
    const rejected = page.getByRole('row').filter({ hasText: rejectedRef });
    await expect(rejected).toContainText('Rejeté');
    await expect(rejected).toContainText(motive);

    // 6. Un paiement confirmé n'active pas l'abonnement.
    const subscription = (await (
      await request.get('/api/v1/subscription', { headers: bearer(tenant.token) })
    ).json()) as { status: string; effective_status: string };
    expect(subscription.status).toBe('pending_activation');
    expect(subscription.effective_status).toBe('pending_activation');
    const blocked = await request.post('/api/v1/catalog/categories', {
      headers: bearer(tenant.token),
      data: { name: `Cat ${stamp}` },
    });
    expect(blocked.status()).toBe(403);

    // Journal de l'entreprise : déclaration + décisions (miroir sans identité TechNova).
    const logs = (await (
      await request.get('/api/v1/audit-logs?limit=50', { headers: bearer(tenant.token) })
    ).json()) as { items: { action: string; user_id: string | null; data: object }[] };
    const actions = logs.items.map((e) => e.action);
    expect(actions).toContain('subscription_payment.declared');
    expect(actions).toContain('subscription_payment.confirmed');
    expect(actions).toContain('subscription_payment.rejected');
    expect(JSON.stringify(logs.items)).not.toContain(admin);
    await consolePage.close();
  });

  test("4. une entreprise ne voit jamais les paiements d'une autre", async ({ request }) => {
    const stamp = stampOf();
    const a = await pendingTenant(request, `${stamp} A`);
    const b = await pendingTenant(request, `${stamp} B`);
    const payment = await declare(request, a.token, `ISO-${stamp}`);

    const detail = await request.get(`/api/v1/subscription/payments/${payment.id}`, {
      headers: bearer(b.token),
    });
    expect(detail.status()).toBe(404);
    const listing = (await (
      await request.get('/api/v1/subscription/payments', { headers: bearer(b.token) })
    ).json()) as { total: number; items: { id: string }[] };
    expect(listing.items.map((p) => p.id)).not.toContain(payment.id);
    // Champs de décision refusés au client.
    const forged = await request.post('/api/v1/subscription/payments', {
      headers: bearer(b.token),
      data: { status: 'CONFIRMED' },
    });
    expect(forged.status()).toBe(422);
  });

  test('5. décisions simultanées : une seule réussit', async ({ request, baseURL }) => {
    const stamp = stampOf();
    const tenant = await pendingTenant(request, `${stamp} C`);
    const payment = await declare(request, tenant.token, `CONC-${stamp}`);
    const first = await consoleApi(baseURL, createPlatformAdmin(stamp, '1'));
    const second = await consoleApi(baseURL, createPlatformAdmin(stamp, '2'));

    const [confirm, reject] = await Promise.all([
      first.post(`${CONSOLE}/payments/${payment.id}/confirm`, {
        headers: CONSOLE_HEADERS,
        data: { reason: `Confirmation ${stamp}` },
      }),
      second.post(`${CONSOLE}/payments/${payment.id}/reject`, {
        headers: CONSOLE_HEADERS,
        data: { reason: `Rejet ${stamp}` },
      }),
    ]);
    const statuses = [confirm.status(), reject.status()].sort();
    expect(statuses).toEqual([200, 409]);
    const loser = confirm.status() === 409 ? confirm : reject;
    expect(((await loser.json()) as { code: string }).code).toBe('payment_already_decided');

    const final = (await (await first.get(`${CONSOLE}/payments/${payment.id}`)).json()) as {
      status: string;
    };
    expect(final.status).toBe(confirm.status() === 200 ? 'CONFIRMED' : 'REJECTED');
    const audit = (await (await first.get(`${CONSOLE}/audit?target_id=${payment.id}`)).json()) as {
      items: { action: string }[];
    };
    expect(audit.items.filter((e) => e.action.startsWith('subscription_payment.'))).toHaveLength(1);
    await first.dispose();
    await second.dispose();
  });
});
