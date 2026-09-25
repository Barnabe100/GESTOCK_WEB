import { expect, test, type Page } from '@playwright/test';

import { apiToken, bearer, ownerSql } from './support';

/**
 * Phase 3.2-A — Inscription publique. Le plan STANDARD est publié le temps du test
 * (mensuel 5 000 XOF, sans essai) puis remis à ses valeurs neutres. Le backend doit accepter
 * plusieurs inscriptions depuis la même adresse : SM_SIGNUP_RATE_LIMIT_ATTEMPTS (README).
 */

const PASSWORD = 'E2e-Inscription-2026';

test.beforeAll(() => {
  ownerSql(
    "UPDATE plans SET listed = true, price_display_enabled = true, currency = 'XOF', " +
      'monthly_price = 5000, monthly_price_enabled = true, trial_days = 0 ' +
      "WHERE code = 'STANDARD'",
  );
});

test.afterAll(() => {
  ownerSql(
    'UPDATE plans SET listed = false, price_display_enabled = false, currency = NULL, ' +
      'monthly_price = NULL, monthly_price_enabled = false, trial_days = 0 ' +
      "WHERE code = 'STANDARD'",
  );
});

const overflow = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);

async function choose(page: Page, inputId: string, option: string) {
  await page
    .locator(`#${inputId}`)
    .locator('xpath=ancestor::div[contains(@class,"p-dropdown")][1]')
    .click();
  const panel = page.locator('.p-dropdown-panel').last();
  if (await panel.locator('input.p-dropdown-filter').count()) {
    await panel.locator('input.p-dropdown-filter').fill(option);
  }
  await panel.getByRole('option', { name: option, exact: true }).click();
}

async function fillSignup(page: Page, email: string, company: string) {
  await page.getByLabel('Nom complet').fill('Awa Traoré');
  await page.getByLabel('Adresse email').fill(email);
  await page.locator('#password').fill(PASSWORD);
  await page.locator('#confirm').fill(PASSWORD);
  await page.getByRole('button', { name: 'Suivant' }).click();

  await page.getByLabel("Nom de l'entreprise (raison sociale)").fill(company);
  await choose(page, 'country_code', 'Burkina Faso');
  await expect(
    page.locator('#currency').locator('xpath=ancestor::div[contains(@class,"p-dropdown")][1]'),
  ).toContainText('XOF');
  await page.getByLabel('Ville', { exact: true }).fill('Ouagadougou');
  await page.getByRole('button', { name: 'Suivant' }).click();

  await page
    .getByRole('radiogroup', { name: "Secteur d'activité" })
    .getByRole('radio', { name: 'Commerce de détail' })
    .click();
  await choose(page, 'business_profile', 'Alimentation / Supérette');
  await page.getByRole('button', { name: 'Suivant' }).click();

  const standard = page.getByRole('region', { name: 'Standard' });
  await expect(standard).toContainText(/5\s000\sF\sCFA \/ mois/);
  await standard.getByRole('radio', { name: 'Choisir cette offre' }).click();
  await page.getByRole('button', { name: 'Suivant' }).click();
  await expect(page.getByTestId('signup-summary')).toContainText(company);
  await expect(page.getByText(/en attente d'activation/)).toBeVisible();
}

test.describe('Inscription publique', () => {
  test('visiteur → compte + entreprise → propriétaire administrateur en attente d’activation', async ({
    page,
    request,
  }) => {
    const email = `e2e-signup-${Date.now()}@example.com`;
    const company = `Supérette Inscrite ${Date.now().toString().slice(-6)}`;
    await page.goto('/login');
    await page.getByRole('link', { name: 'Créer votre entreprise' }).click();
    await expect(
      page.locator('.p-card-title', { hasText: 'Créer votre entreprise' }),
    ).toBeVisible();
    await fillSignup(page, email, company);
    await page.getByRole('button', { name: 'Créer mon entreprise' }).click();

    // Connecté sur la nouvelle entreprise : profil, abonnement en attente d'activation.
    await expect(page.getByTestId('pending-activation')).toBeVisible();
    await expect(page.getByTestId('business-profile')).toHaveText('Alimentation / Supérette');
    await expect(page.getByText(company).first()).toBeVisible();

    // Le serveur applique la politique : administration oui, opérations métier non.
    const token = await apiToken(request, email, PASSWORD);
    const caps = (await (
      await request.get('/api/v1/me/capabilities', { headers: bearer(token) })
    ).json()) as {
      is_owner: boolean;
      sites: unknown[];
      subscription: { status: string };
    };
    expect([caps.is_owner, caps.sites.length, caps.subscription.status]).toEqual([
      true,
      0,
      'pending_activation',
    ]);
    const site = await request.post('/api/v1/sites', {
      headers: bearer(token),
      data: { name: 'Boutique centrale', code: 'CENTRE' },
    });
    expect(site.status()).toBe(201);
    const refused = await request.post('/api/v1/catalog/categories', {
      headers: bearer(token),
      data: { name: 'Riz' },
    });
    expect(refused.status()).toBe(403);
    expect(((await refused.json()) as { code: string }).code).toBe('subscription_restricted');

    // Même adresse : refus générique, aucune indication que le compte existe.
    await page.context().clearCookies();
    await page.goto('/signup');
    await page.evaluate(() => sessionStorage.clear());
    await page.reload();
    await fillSignup(page, email, `${company} bis`);
    await page.getByRole('button', { name: 'Créer mon entreprise' }).click();
    await expect(
      page.getByRole('alert').filter({ hasText: 'Inscription impossible' }),
    ).toBeVisible();
  });

  test('inscription sur mobile, sans débordement @mobile', async ({ page }) => {
    await page.goto('/signup');
    await expect(page.getByLabel('Nom complet')).toBeVisible();
    expect(await overflow(page)).toBe(false);
    const email = `e2e-signup-m-${Date.now()}@example.com`;
    await fillSignup(page, email, `Boutique Mobile ${Date.now().toString().slice(-6)}`);
    expect(await overflow(page)).toBe(false);
    await page.getByRole('button', { name: 'Créer mon entreprise' }).click();
    await expect(page.getByTestId('pending-activation')).toBeVisible();
    expect(await overflow(page)).toBe(false);
  });
});
