import { expect, test, type Page } from '@playwright/test';

import { adminCliWithInput, OWNER, ownerSql } from './support';

/**
 * Phase 3.2-F — Console TechNova (ADR-0031). Processus distinct (`app.console.main`, port
 * 8001, relayé par Vite sous /platform-api). Un administrateur TechNova est créé par la CLI à
 * chaque exécution ; le plan STANDARD est remis à ses valeurs neutres à la fin.
 */

const PASSWORD = 'E2e-Console-TechNova-2026';

const NEUTRAL =
  'UPDATE plans SET listed = false, price_display_enabled = false, currency = NULL, ' +
  'monthly_price = NULL, monthly_price_enabled = false, annual_price = NULL, ' +
  'annual_price_enabled = false, contact_required = false, commercial_description = NULL, ' +
  "display_order = 0, trial_days = 0 WHERE code = 'STANDARD'";

test.beforeAll(() => ownerSql(NEUTRAL));
test.afterAll(() => ownerSql(NEUTRAL));

function createPlatformAdmin(): string {
  const email = `e2e-technova-${Date.now()}@technova.example`;
  adminCliWithInput(
    `${PASSWORD}\n`,
    'platform-admin',
    'create',
    '--email',
    email,
    '--name',
    'Admin TechNova E2E',
    '--password-stdin',
  );
  return email;
}

async function consoleLogin(page: Page, email: string, password: string) {
  await page.goto('/tech-admin');
  await expect(page).toHaveURL(/\/tech-admin\/login$/);
  await page.locator('#email').fill(email);
  await page.locator('#password').fill(password);
  await page.getByRole('button', { name: 'Se connecter' }).click();
}

const toggle = (page: Page, id: string) =>
  page.locator(`#${id}`).locator('xpath=ancestor::div[contains(@class,"p-inputswitch")][1]');

test.describe('Console TechNova', () => {
  test('publier une offre : raison, confirmation, nouvelle valeur, audit, /public/plans', async ({
    page,
    request,
  }) => {
    const email = createPlatformAdmin();
    // Raison unique : la suite peut être rejouée sur la même base (journal append-only).
    const reason = `Lancement commercial E2E ${Date.now()}`;
    await consoleLogin(page, email, PASSWORD);
    await expect(page.getByTestId('console-badge')).toContainText('Console TechNova');
    await expect(page.getByTestId('console-identity')).toContainText(email);

    await page.getByRole('link', { name: 'Offres & tarifs' }).click();
    const row = page.getByRole('row').filter({ hasText: 'STANDARD' });
    await expect(row).toContainText('Non publié');
    await row.getByRole('button', { name: /Ouvrir/ }).click();
    await expect(page.getByRole('heading', { name: 'Standard (STANDARD)' })).toBeVisible();

    // Structure technique : lecture seule.
    const structure = page.getByTestId('plan-structure');
    await expect(structure.getByTestId('limit-max_users')).toHaveText('5');
    await expect(structure.locator('input, textarea')).toHaveCount(0);

    // Publication mensuelle à 7 500 XOF, prix affiché.
    await toggle(page, 'listed').click();
    await toggle(page, 'monthly_price_enabled').click();
    await page.locator('#monthly_price').fill('7500');
    await page
      .locator('#currency')
      .locator('xpath=ancestor::div[contains(@class,"p-dropdown")][1]')
      .click();
    await page.locator('.p-dropdown-panel input.p-dropdown-filter').fill('XOF');
    await page
      .locator('.p-dropdown-panel')
      .getByRole('option', { name: 'XOF', exact: true })
      .click();
    await toggle(page, 'price_display_enabled').click();

    // Raison obligatoire.
    await page.getByRole('button', { name: 'Enregistrer les modifications' }).click();
    await expect(page.getByText('La raison de la modification est obligatoire.')).toBeVisible();
    await page.locator('#reason').fill(reason);
    await page.getByRole('button', { name: 'Enregistrer les modifications' }).click();

    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText('Modifier le plan Standard ?');
    await expect(dialog).toContainText(/Prix mensuel : — → 7.500/);
    await dialog.getByRole('button', { name: 'Confirmer' }).click();
    await expect(page.getByText('Paramètres commerciaux enregistrés')).toBeVisible();
    await expect(page.getByTestId('plan-badges')).toContainText('Publié');
    await expect(page.locator('#monthly_price')).toHaveValue('7500.00');

    // Historique du plan et journal de la plateforme.
    const history = page.getByRole('row').filter({ hasText: reason });
    await expect(history).toContainText('plan.published');
    await expect(history).toContainText(email);
    await page.getByRole('link', { name: 'Journal de la plateforme' }).click();
    const entry = page.getByRole('row').filter({ hasText: reason });
    await expect(entry).toContainText('plan · STANDARD');
    await expect(entry).toContainText(/Prix mensuel.*→.*7500\.00/);

    // Effet immédiat sur les offres publiques.
    const publicPlans = (await (await request.get('/api/v1/public/plans')).json()) as {
      plans: { code: string; periods: { billing_period: string; price: string }[] }[];
    };
    const standard = publicPlans.plans.find((p) => p.code === 'STANDARD');
    expect(standard?.periods).toEqual([{ billing_period: 'monthly', price: '7500.00' }]);

    await page.getByRole('button', { name: 'Se déconnecter' }).click();
    await expect(page).toHaveURL(/\/tech-admin\/login$/);
  });

  test('un compte d’entreprise ne peut pas se connecter à la console', async ({
    page,
    request,
  }) => {
    await consoleLogin(page, OWNER.email, OWNER.password);
    await expect(page.getByText('Email ou mot de passe incorrect.')).toBeVisible();
    await expect(page).toHaveURL(/\/tech-admin\/login$/);
    const plans = await request.get('/platform-api/v1/plans');
    expect(plans.status()).toBe(401);
  });

  test('console lisible sur mobile, sans débordement @mobile', async ({ page }) => {
    const email = createPlatformAdmin();
    await consoleLogin(page, email, PASSWORD);
    await expect(page.getByTestId('console-identity')).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth),
    ).toBe(false);
  });
});
