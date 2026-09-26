import { readFileSync } from 'node:fs';

import {
  expect,
  request as playwrightRequest,
  test,
  type APIRequestContext,
  type Page,
} from '@playwright/test';

import { adminCliWithInput, bearer, loginUi, ownerSql, provisionTenant, tokenFor } from './support';

/**
 * Phase 3.3-B2 — Licences (ADR-0034) : paiement confirmé → licence générée par TechNova dans
 * la console, signée par le Signing Service (processus distinct, clé ÉPHÉMÈRE de
 * développement, hors dépôt : voir e2e/README.md) → site actif ; révocation définitive,
 * réémission ; l'entreprise ne fait que lire. Chaque exécution crée ses propres données.
 */

const ADMIN_PASSWORD = 'E2e-Console-Licences-2026';
const OWNER_PASSWORD = 'E2e-Tenant-Licences-2026';
const CONSOLE = '/platform-api/v1';
const CONSOLE_HEADERS = { 'X-TechNova-Console': '1' };

const stampOf = () => `${Date.now().toString().slice(-8)}${Math.floor(Math.random() * 90 + 10)}`;

function createPlatformAdmin(stamp: string): string {
  const email = `e2e-tn-lic-${stamp}@technova.example`;
  adminCliWithInput(
    `${ADMIN_PASSWORD}\n`,
    'platform-admin',
    'create',
    '--email',
    email,
    '--name',
    'Admin Licences E2E',
    '--password-stdin',
  );
  return email;
}

/** Entreprise dont l'abonnement du site attend son activation, 2 postes demandés. */
async function pendingTenant(request: APIRequestContext, label: string) {
  const name = `Licences ${label}`;
  const email = `e2e-lic-${label.replace(/\s+/g, '-').toLowerCase()}@example.com`;
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
      amount: '25000',
      period_start: '2026-10-01',
      period_end: '2026-11-01',
      payment_method: 'BANK_TRANSFER',
      declared_reference: reference,
      idempotency_key: crypto.randomUUID(),
    },
  });
  expect(response.status(), await response.text()).toBe(201);
  return (await response.json()) as { id: string };
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

async function consoleLogin(page: Page, email: string) {
  await page.goto('/tech-admin/login');
  await page.locator('#email').fill(email);
  await page.locator('#password').fill(ADMIN_PASSWORD);
  await page.getByRole('button', { name: 'Se connecter' }).click();
  await expect(page.getByTestId('console-identity')).toBeVisible();
}

async function confirmDialog(page: Page, action: string, reason: string) {
  const dialog = page.getByRole('dialog');
  await dialog.locator('#license-reason').fill(reason);
  await dialog.getByText('Je confirme cette action.').click();
  await dialog.getByRole('button', { name: action, exact: true }).click();
  await expect(dialog).toHaveCount(0);
}

test.describe('Licences : console TechNova et entreprise', () => {
  test('paiement confirmé → licence signée → site actif ; révocation ; réémission', async ({
    page,
    request,
    baseURL,
  }) => {
    const stamp = stampOf();
    const tenant = await pendingTenant(request, stamp);
    const reference = `LIC-VIR-${stamp}`;
    const payment = await declare(request, tenant.token, reference);
    const admin = createPlatformAdmin(stamp);
    const api = await consoleApi(baseURL, admin);
    const confirmed = await api.post(`${CONSOLE}/payments/${payment.id}/confirm`, {
      headers: CONSOLE_HEADERS,
      data: { reason: `Virement reçu ${stamp}` },
    });
    expect(confirmed.status()).toBe(200);

    // 1. Console : génération depuis le paiement confirmé, postes proposés (2) ajustés à 3.
    const consolePage = await page.context().newPage();
    await consoleLogin(consolePage, admin);
    await consolePage.goto(`/tech-admin/payments/${payment.id}`);
    const card = consolePage.getByTestId('payment-license');
    await card.getByRole('button', { name: 'Générer la licence' }).click();
    const dialog = consolePage.getByRole('dialog');
    const activations = dialog.locator('#license-activations');
    await expect(activations).toHaveValue('2');
    await activations.fill('3');
    await confirmDialog(consolePage, 'Générer la licence', `Licence ${stamp}`);
    await expect(consolePage).toHaveURL(/\/tech-admin\/licenses\/[0-9a-f-]+$/);
    await expect(consolePage.getByTestId('license-state')).toContainText('Active');
    await expect(consolePage.getByTestId('license-activations')).toHaveText('3');
    const number = (await consolePage.getByRole('heading', { level: 1 }).textContent())!
      .replace('Licence ', '')
      .trim();
    expect(number).toMatch(/^LIC-\d{4}-\d{5,}$/);

    // 2. Téléchargement du fichier .lic signé (document v1, aucune clé privée).
    const [download] = await Promise.all([
      consolePage.waitForEvent('download'),
      consolePage.getByRole('button', { name: 'Télécharger le fichier .lic' }).click(),
    ]);
    expect(download.suggestedFilename()).toBe(`${number}.lic`);
    const document = JSON.parse(readFileSync((await download.path())!, 'utf8')) as {
      format: string;
      version: number;
      key_id: string;
      signature: string;
      payload: { license_number: string; max_activations: number };
    };
    expect(document.format).toBe('stockmanager-license');
    expect(document.version).toBe(1);
    expect(document.payload.license_number).toBe(number);
    expect(document.payload.max_activations).toBe(3);
    expect(document.signature.length).toBeGreaterThan(80);
    expect(JSON.stringify(document)).not.toContain('PRIVATE');

    // 3. Entreprise : licence en lecture seule, site actif, postes autorisés.
    await loginUi(page, tenant.email, OWNER_PASSWORD, tenant.name);
    await page.goto('/subscription');
    const license = page.getByTestId('license');
    await expect(license).toContainText(number);
    await expect(license).toContainText('Active');
    await expect(page.getByTestId('license-activations')).toHaveText(
      '3 postes autorisés · 0 utilisé · 3 disponibles',
    );
    await expect(page.getByText('Actif').first()).toBeVisible();

    // 4. Révocation définitive : le site est suspendu, les données conservées.
    await consolePage.getByRole('button', { name: 'Révoquer' }).click();
    await confirmDialog(consolePage, 'Révoquer', `Erreur de site ${stamp}`);
    await expect(consolePage.getByTestId('license-state')).toContainText('Révoquée');
    await expect(consolePage.getByTestId('license-revocation-reason')).toHaveText(
      `Erreur de site ${stamp}`,
    );
    await expect(consolePage.getByRole('button', { name: /Télécharger/ })).toHaveCount(0);
    await page.reload();
    await expect(page.getByTestId('license')).toContainText('Révoquée');
    await expect(page.getByText('Suspendu').first()).toBeVisible();

    // 5. Réémission : nouvelle licence (nouveau numéro, même période), site de nouveau actif.
    await consolePage.getByRole('button', { name: 'Réémettre' }).click();
    await confirmDialog(consolePage, 'Réémettre', `Nouveau cycle ${stamp}`);
    await expect(consolePage.getByTestId('license-state')).toContainText('Active');
    const reissued = (await consolePage.getByRole('heading', { level: 1 }).textContent())!;
    expect(reissued).not.toContain(number);
    await expect(consolePage.getByTestId('license-activations')).toHaveText('3');
    await page.reload();
    await expect(page.getByTestId('license')).toContainText('Active');
    await expect(page.getByText('Actif').first()).toBeVisible();

    // Journal de l'entreprise : miroirs sans identité de l'agent TechNova.
    const logs = (await (
      await request.get('/api/v1/audit-logs?limit=50', { headers: bearer(tenant.token) })
    ).json()) as { items: { action: string }[] };
    const actions = logs.items.map((e) => e.action);
    expect(actions).toEqual(
      expect.arrayContaining(['license.generated', 'license.revoked', 'license.reissued']),
    );
    expect(JSON.stringify(logs.items)).not.toContain(admin);
    await api.dispose();
    await consolePage.close();
  });

  test('sans paiement confirmé : aucune licence ; l’entreprise ne peut rien générer', async ({
    page,
    request,
    baseURL,
  }) => {
    const stamp = stampOf();
    const tenant = await pendingTenant(request, `${stamp} P`);
    const payment = await declare(request, tenant.token, `LIC-PEND-${stamp}`);
    const admin = createPlatformAdmin(stamp);
    const api = await consoleApi(baseURL, admin);

    const refused = await api.post(`${CONSOLE}/payments/${payment.id}/license`, {
      headers: CONSOLE_HEADERS,
      data: { reason: 'Test', max_activations: 1 },
    });
    expect(refused.status()).toBe(409);
    expect(((await refused.json()) as { code: string }).code).toBe('payment_not_confirmed');

    await consoleLogin(page, admin);
    await page.goto(`/tech-admin/payments/${payment.id}`);
    await expect(page.getByTestId('payment-status')).toContainText('En attente');
    await expect(page.getByTestId('payment-license')).toHaveCount(0);

    // L'entreprise : aucune route de licence, abonnement toujours en attente.
    for (const path of [
      '/api/v1/licenses',
      `/api/v1/subscription/payments/${payment.id}/license`,
    ]) {
      const forged = await request.post(path, {
        headers: bearer(tenant.token),
        data: { max_activations: 99 },
      });
      expect([404, 405]).toContain(forged.status());
    }
    const subscription = (await (
      await request.get('/api/v1/subscription', { headers: bearer(tenant.token) })
    ).json()) as { status: string; license: unknown };
    expect(subscription.status).toBe('pending_activation');
    expect(subscription.license).toBeNull();
    await api.dispose();
  });

  test('postes : quota par site, message clair, libération (entreprise et TechNova)', async ({
    page,
    request,
    baseURL,
  }) => {
    const stamp = stampOf();
    const tenant = await pendingTenant(request, `${stamp} Postes`);
    const payment = await declare(request, tenant.token, `LIC-POS-${stamp}`);
    const admin = createPlatformAdmin(stamp);
    const api = await consoleApi(baseURL, admin);
    await api.post(`${CONSOLE}/payments/${payment.id}/confirm`, {
      headers: CONSOLE_HEADERS,
      data: { reason: 'Reçu' },
    });
    const generated = await api.post(`${CONSOLE}/payments/${payment.id}/license`, {
      headers: CONSOLE_HEADERS,
      data: { reason: 'Licence', max_activations: 2 },
    });
    expect(generated.status(), await generated.text()).toBe(201);
    const licence = (await generated.json()) as { id: string };
    const subscription = (await (
      await request.get('/api/v1/subscription', { headers: bearer(tenant.token) })
    ).json()) as { site: { id: string } };
    const desktop = { ...bearer(tenant.token), 'X-Site-Id': subscription.site.id };

    // Deux installations (client Desktop simulé) : quota atteint à la troisième.
    const activate = (label: string, installation = crypto.randomUUID()) =>
      request.post('/api/v1/license-activations', {
        headers: desktop,
        data: { installation_id: installation, label, client_version: '1.0.0' },
      });
    const first = crypto.randomUUID();
    expect((await activate('Caisse 1', first)).status()).toBe(201);
    expect((await activate('Caisse 1', first)).status()).toBe(200); // idempotent
    expect((await activate('Caisse 2')).status()).toBe(201);
    const refused = await activate('Caisse 3');
    expect(refused.status()).toBe(409);
    const problem = (await refused.json()) as { code: string; detail: string };
    expect(problem.code).toBe('activation_quota_reached');
    expect(problem.detail).toBe('Le nombre maximal de postes autorisés pour ce site est atteint.');
    const checkIn = await request.post('/api/v1/license-activations/check-in', {
      headers: desktop,
      data: { installation_id: first },
    });
    expect(checkIn.status()).toBe(200);

    // Entreprise : « 2 postes autorisés · 2 utilisés · 0 disponible », libération d'un poste.
    await loginUi(page, tenant.email, OWNER_PASSWORD, tenant.name);
    await page.goto('/subscription');
    const summary = page.getByTestId('license-activations');
    await expect(summary).toHaveText('2 postes autorisés · 2 utilisés · 0 disponible');
    await page.getByRole('button', { name: 'Libérer Caisse 2' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.locator('#release-reason').fill(`Remplacé ${stamp}`);
    await dialog.getByText('Je confirme la libération de ce poste.').click();
    await dialog.getByRole('button', { name: 'Libérer', exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await expect(summary).toHaveText('2 postes autorisés · 1 utilisé · 1 disponible');
    // La place libérée est réutilisable ; la licence est inchangée.
    expect((await activate('Caisse 3')).status()).toBe(201);

    // TechNova libère un poste (support) depuis la fiche de la licence.
    const consolePage = await page.context().newPage();
    await consoleLogin(consolePage, admin);
    await consolePage.goto(`/tech-admin/licenses/${licence.id}`);
    const postes = consolePage.getByTestId('license-postes');
    await expect(postes).toContainText('Postes (2 / 2)');
    await postes.getByRole('button', { name: 'Libérer Caisse 1' }).click();
    await confirmDialog(consolePage, 'Libérer', `Ordinateur volé ${stamp}`);
    await expect(postes).toContainText('Postes (1 / 2)');
    const listing = (await (
      await request.get('/api/v1/license-activations?status=RELEASED', { headers: desktop })
    ).json()) as { items: { label: string; release_source: string }[] };
    expect(listing.items.find((a) => a.label === 'Caisse 1')?.release_source).toBe('TECHNOVA');
    expect(listing.items.find((a) => a.label === 'Caisse 2')?.release_source).toBe('TENANT');
    await api.dispose();
    await consolePage.close();
  });
});
