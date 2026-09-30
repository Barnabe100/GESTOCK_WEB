import {
  expect,
  request as playwrightRequest,
  test,
  type APIRequestContext,
} from '@playwright/test';

import {
  adminCli,
  adminCliWithInput,
  bearer,
  declareSubscriptionPayment,
  loginUi,
  ownerSql,
  provisionTenant,
  tokenFor,
} from './support';

/**
 * Phase 3.3-B4 — Renouvellement par site et rappels d'échéance (ADR-0036) : période, postes et
 * montant calculés par le serveur ; postes reconduits sauf demande explicite, confirmée par
 * TechNova ; la licence suivante commence au lendemain de la licence en cours ; rappels créés
 * par le job `stockmanager notifications run`, lus / non lus par membre. Signing Service local
 * avec une clé ÉPHÉMÈRE (voir e2e/README.md). Chaque exécution crée ses propres données.
 */

const ADMIN_PASSWORD = 'E2e-Console-Renouvellement-2026';
const OWNER_PASSWORD = 'E2e-Tenant-Renouvellement-2026';
const CONSOLE = '/platform-api/v1';
const CONSOLE_HEADERS = { 'X-TechNova-Console': '1' };

const stampOf = () => `${Date.now().toString().slice(-8)}${Math.floor(Math.random() * 90 + 10)}`;

function createPlatformAdmin(stamp: string): string {
  const email = `e2e-tn-ren-${stamp}@technova.example`;
  adminCliWithInput(
    `${ADMIN_PASSWORD}\n`,
    'platform-admin',
    'create',
    '--email',
    email,
    '--name',
    'Admin Renouvellement E2E',
    '--password-stdin',
  );
  return email;
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

/** Confirme le paiement puis génère sa licence (postes indiqués) : parcours TechNova. */
async function license(
  api: APIRequestContext,
  paymentId: string,
  maxActivations: number,
): Promise<{ id: string; valid_from: string; valid_until: string; state: string }> {
  const confirmed = await api.post(`${CONSOLE}/payments/${paymentId}/confirm`, {
    headers: CONSOLE_HEADERS,
    data: { reason: 'Paiement reçu' },
  });
  expect(confirmed.status(), await confirmed.text()).toBe(200);
  const generated = await api.post(`${CONSOLE}/payments/${paymentId}/license`, {
    headers: CONSOLE_HEADERS,
    data: { reason: 'Émission', max_activations: maxActivations },
  });
  expect(generated.status(), await generated.text()).toBe(201);
  return (await generated.json()) as {
    id: string;
    valid_from: string;
    valid_until: string;
    state: string;
  };
}

const nextDay = (day: string) => {
  const date = new Date(`${day}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + 1);
  return date.toISOString().slice(0, 10);
};

test.describe('Renouvellement par site et rappels d’échéance', () => {
  test('renouveler sans perte de jours, postes explicites, rappels lus / non lus', async ({
    page,
    request,
    baseURL,
  }) => {
    const stamp = stampOf();
    const name = `Renouvellement ${stamp}`;
    const email = `e2e-ren-${stamp}@example.com`;
    await provisionTenant(request, {
      name,
      profile: 'retail.alimentation',
      email,
      password: OWNER_PASSWORD,
    });
    ownerSql(
      "UPDATE subscriptions SET status = 'pending_activation', current_period_end = now(), " +
        `requested_activations = 2 WHERE tenant_id = (SELECT id FROM tenants WHERE name = '${name}')`,
    );
    const token = await tokenFor(request, email, OWNER_PASSWORD, name);
    const api = await consoleApi(baseURL, createPlatformAdmin(stamp));

    // 1. Première licence (2 postes).
    const first = await license(
      api,
      (await declareSubscriptionPayment(request, token, `INIT-${stamp}`, { amount: '20000' })).id,
      2,
    );
    expect(first.state).toBe('ACTIVE');

    // 2. Page Abonnement : offre en vigueur, prochaine période au lendemain de la licence.
    await loginUi(page, email, OWNER_PASSWORD, name);
    await page.goto('/subscription');
    await expect(page.getByTestId('effective-plan').first()).toBeVisible();
    const next = page.getByTestId('next-period');
    await expect(next).toContainText('2 postes');
    const subscription = (await (
      await request.get('/api/v1/subscription', { headers: bearer(token) })
    ).json()) as { renewal: { valid_from: string; activations: number; renewal_due: boolean } };
    expect(subscription.renewal.valid_from).toBe(nextDay(first.valid_until));
    expect(subscription.renewal.activations).toBe(2);
    expect(subscription.renewal.renewal_due).toBe(true);

    // 3. « Renouveler » : période affichée (jamais saisie), 4 postes demandés explicitement.
    await next.getByRole('button', { name: 'Renouveler' }).click();
    const form = page.getByRole('dialog');
    await expect(form.getByTestId('renewal-postes')).toHaveText('2 postes');
    await expect(form.locator('#subscription-payment-start')).toHaveCount(0);
    await form.getByText('Demander un autre nombre de postes').click();
    const postes = form.locator('#subscription-payment-postes');
    await postes.fill('4');
    await postes.blur();
    await expect(form.getByTestId('renewal-postes')).toContainText('4 postes');
    if ((await form.getByTestId('renewal-amount').count()) === 0) {
      await form.locator('#subscription-payment-amount').fill('40000');
    }
    await form.locator('#subscription-payment-reference').fill(`REN-${stamp}`);
    await form.getByRole('button', { name: 'Déclarer le paiement' }).click();
    await expect(form).toHaveCount(0);
    const row = page.getByRole('row').filter({ hasText: `REN-${stamp}` });
    await expect(row).toContainText('4 postes demandés');

    // 4. Console : proposition = postes actuels 2, demande 4 ; licence suivante contiguë.
    const payments = (await (
      await request.get('/api/v1/subscription/payments?status=PENDING', { headers: bearer(token) })
    ).json()) as { items: { id: string; declared_reference: string }[] };
    const renewalPayment = payments.items.find((p) => p.declared_reference === `REN-${stamp}`)!;
    await api.post(`${CONSOLE}/payments/${renewalPayment.id}/confirm`, {
      headers: CONSOLE_HEADERS,
      data: { reason: 'Paiement reçu' },
    });
    const proposal = (await (
      await api.get(`${CONSOLE}/payments/${renewalPayment.id}/license-proposal`)
    ).json()) as {
      current_activations: number;
      requested_activations: number;
      max_activations: number;
      valid_from: string;
    };
    expect(proposal).toMatchObject({
      current_activations: 2,
      requested_activations: 4,
      max_activations: 4,
      valid_from: nextDay(first.valid_until),
    });
    const renewed = await api.post(`${CONSOLE}/payments/${renewalPayment.id}/license`, {
      headers: CONSOLE_HEADERS,
      data: { reason: 'Renouvellement', max_activations: 4 },
    });
    expect(renewed.status(), await renewed.text()).toBe(201);
    const second = (await renewed.json()) as { valid_from: string; valid_until: string };
    expect(second.valid_from).toBe(nextDay(first.valid_until));

    // Droits de la licence en vigueur inchangés jusqu'à la suivante : toujours 2 postes.
    await page.reload();
    await expect(page.getByTestId('license-activations')).toContainText('2 postes autorisés');

    // 5. Job quotidien : la veille de l'échéance (J-1), un rappel ; relancé, rien de plus.
    const now = new Date(`${second.valid_until}T10:00:00Z`);
    now.setUTCDate(now.getUTCDate() - 1);
    const firstRun = adminCli('notifications', 'run', '--now', now.toISOString());
    expect(firstRun).toContain('rappels envoyés');
    adminCli('notifications', 'run', '--now', now.toISOString());
    const listed = (await (
      await request.get('/api/v1/notifications', { headers: bearer(token) })
    ).json()) as { total: number; items: { step: number; read_at: string | null }[] };
    expect(listed.items.map((n) => n.step)).toEqual([1]);

    // 6. Indicateur, rappel sur la page Abonnement, lecture, historique conservé.
    await page.reload();
    await expect(page.getByTestId('unread-count')).toHaveText('1');
    const reminder = page.getByTestId('expiry-reminder');
    await expect(reminder).toContainText('expire dans 1 jour');
    await reminder.getByRole('button', { name: 'Marquer comme lu' }).click();
    await expect(reminder).toHaveCount(0);
    await expect(page.getByTestId('unread-count')).toHaveCount(0);
    await page.getByTestId('notifications-bell').click();
    await expect(page).toHaveURL(/\/notifications$/);
    await expect(page.getByTestId('notification')).toHaveCount(1);
    await expect(page.getByRole('row').filter({ hasText: 'expire dans 1 jour' })).toContainText(
      'Lu',
    );
    await api.dispose();
  });
});
