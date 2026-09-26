import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import { adminCliWithInput, bearer, ownerSql, provisionTenant, tokenFor } from './support';

/**
 * Phase 3.2-G — Console TechNova : tenants et abonnements (ADR-0031). Chaque exécution crée
 * son administrateur TechNova (CLI) et sa propre entreprise (nom, raisons uniques) : aucune
 * dépendance aux données d'une exécution précédente.
 */

const ADMIN_PASSWORD = 'E2e-Console-Tenants-2026';
const OWNER_PASSWORD = 'E2e-Tenant-Console-2026';

function createPlatformAdmin(stamp: string): string {
  const email = `e2e-tn-tenants-${stamp}@technova.example`;
  adminCliWithInput(
    `${ADMIN_PASSWORD}\n`,
    'platform-admin',
    'create',
    '--email',
    email,
    '--name',
    'Admin Tenants E2E',
    '--password-stdin',
  );
  return email;
}

async function consoleLogin(page: Page, email: string) {
  await page.goto('/tech-admin/login');
  await page.locator('#email').fill(email);
  await page.locator('#password').fill(ADMIN_PASSWORD);
  await page.getByRole('button', { name: 'Se connecter' }).click();
  await expect(page.getByTestId('console-identity')).toBeVisible();
}

/** Action TechNova : raison, confirmation explicite, puis bouton de confirmation. */
async function runAction(page: Page, action: string, confirm: string, reason: string) {
  await page.getByTestId('tenant-actions').getByRole('button', { name: action }).click();
  const dialog = page.getByRole('dialog');
  await dialog.locator('#action-reason').fill(reason);
  await dialog.getByText('Je confirme cette action.').click();
  await dialog.getByRole('button', { name: confirm }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByText('Action enregistrée').last()).toBeVisible();
}

async function tenantAudit(request: APIRequestContext, token: string) {
  const logs = (await (
    await request.get('/api/v1/audit-logs?limit=50', { headers: bearer(token) })
  ).json()) as {
    items: { action: string; user_id: string | null; data: Record<string, unknown> }[];
  };
  return logs.items;
}

test.describe('Console TechNova : tenants et abonnements', () => {
  test('recherche, activation manuelle, changement de plan, suspension, réactivation, double audit', async ({
    page,
    request,
  }) => {
    const stamp = `${Date.now().toString().slice(-8)}${Math.floor(Math.random() * 90 + 10)}`;
    const name = `Tenant Console ${stamp}`;
    const ownerEmail = `e2e-tenant-console-${stamp}@example.com`;
    await provisionTenant(request, {
      name,
      profile: 'retail.alimentation',
      email: ownerEmail,
      password: OWNER_PASSWORD,
    });
    // État laissé par l'inscription publique sans essai (ADR-0025).
    ownerSql(
      "UPDATE subscriptions SET status = 'pending_activation', current_period_end = now() " +
        `WHERE tenant_id = (SELECT id FROM tenants WHERE name = '${name}')`,
    );
    const ownerToken = await tokenFor(request, ownerEmail, OWNER_PASSWORD, name);
    const blocked = await request.post('/api/v1/catalog/categories', {
      headers: bearer(ownerToken),
      data: { name: `Cat ${stamp}` },
    });
    expect(blocked.status()).toBe(403);

    const admin = createPlatformAdmin(stamp);
    await consoleLogin(page, admin);
    await page.getByRole('link', { name: 'Tenants' }).click();
    await page.getByPlaceholder(/Rechercher une entreprise/).fill(stamp);
    const row = page.getByRole('row').filter({ hasText: name });
    await expect(row).toHaveCount(1);
    await expect(row).toContainText("En attente d'activation");
    await row.getByRole('button', { name: new RegExp(`Ouvrir ${name}`) }).click();

    await expect(page.getByRole('heading', { name })).toBeVisible();
    await expect(page.getByTestId('subscription-plan')).toHaveText('Entreprise (ENTREPRISE)');
    await expect(page.getByTestId('tenant-badges')).toContainText("En attente d'activation");

    // Activation manuelle transitoire (aucun paiement).
    const activation = `Activation commerciale temporaire ${stamp}`;
    await page
      .getByTestId('tenant-actions')
      .getByRole('button', { name: "Activer l'abonnement" })
      .click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText('ni un paiement confirmé ni une licence');
    await expect(dialog.locator('#period-start')).not.toHaveValue('');
    await dialog.locator('#action-reason').fill(activation);
    await dialog.getByText('Je confirme cette action.').click();
    await dialog.getByRole('button', { name: "Confirmer l'activation" }).click();
    await expect(dialog).toHaveCount(0);
    await expect(page.getByTestId('tenant-badges')).toContainText('Actif');
    const history = page.getByRole('row').filter({ hasText: activation });
    await expect(history).toContainText('subscription.manually_activated');
    const created = await request.post('/api/v1/catalog/categories', {
      headers: bearer(ownerToken),
      data: { name: `Cat ${stamp}` },
    });
    expect(created.status(), await created.text()).toBe(201);

    // Changement de plan.
    await page
      .getByTestId('tenant-actions')
      .getByRole('button', { name: 'Changer de plan' })
      .click();
    await page
      .locator('#new-plan')
      .locator('xpath=ancestor::div[contains(@class,"p-dropdown")][1]')
      .click();
    await page.locator('.p-dropdown-panel').getByRole('option', { name: 'Standard' }).click();
    await page.getByRole('dialog').locator('#action-reason').fill(`Plan ${stamp}`);
    await page.getByRole('dialog').getByText('Je confirme cette action.').click();
    await page
      .getByRole('dialog')
      .getByRole('button', { name: 'Confirmer le changement de plan' })
      .click();
    await expect(page.getByTestId('subscription-plan')).toHaveText('Standard (STANDARD)');

    // Suspension puis réactivation.
    await runAction(page, 'Suspendre', 'Confirmer la suspension', `Suspension ${stamp}`);
    await expect(page.getByTestId('tenant-badges')).toContainText('Suspendue');
    const suspended = await request.get('/api/v1/sites', { headers: bearer(ownerToken) });
    expect(suspended.status()).toBe(403);
    expect(((await suspended.json()) as { code: string }).code).toBe('tenant_suspended');
    await runAction(page, 'Réactiver', 'Confirmer la réactivation', `Réactivation ${stamp}`);
    await expect(page.getByTestId('tenant-badges')).toContainText('Active');
    const back = await tokenFor(request, ownerEmail, OWNER_PASSWORD, name);
    expect((await request.get('/api/v1/sites', { headers: bearer(back) })).status()).toBe(200);

    // Journal de la plateforme.
    await page.getByRole('link', { name: 'Journal de la plateforme' }).click();
    await page.getByPlaceholder('Filtrer par action').fill('tenant.');
    const platform = page.getByRole('row').filter({ hasText: `Suspension ${stamp}` });
    await expect(platform).toContainText('tenant.suspended');
    await expect(platform).toContainText(admin);

    // Journal de l'entreprise : entrées miroir, sans identité de l'agent TechNova.
    const mirrored = await tenantAudit(request, back);
    const actions = mirrored.map((e) => e.action);
    for (const expected of [
      'subscription.manually_activated',
      'subscription.plan_changed',
      'tenant.suspended',
      'tenant.reactivated',
    ]) {
      expect(actions).toContain(expected);
    }
    const suspension = mirrored.find((e) => e.action === 'tenant.suspended');
    expect(suspension?.user_id).toBeNull();
    expect(suspension?.data.actor).toBe('technova');
    expect(suspension?.data.reason).toBe(`Suspension ${stamp}`);
    expect(JSON.stringify(mirrored)).not.toContain(admin);
  });

  test('liste des tenants sur mobile, sans débordement @mobile', async ({ page }) => {
    const stamp = `${Date.now().toString().slice(-8)}m`;
    const admin = createPlatformAdmin(stamp);
    await consoleLogin(page, admin);
    await page.goto('/tech-admin/tenants');
    await expect(page.getByRole('heading', { name: 'Tenants' })).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth),
    ).toBe(false);
  });
});
