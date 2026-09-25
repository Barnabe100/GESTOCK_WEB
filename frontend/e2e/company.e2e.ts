import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import { bearer, loginUi, ownerSql, provisionTenant } from './support';

/**
 * Phase 3.2-C — Configuration de l'entreprise et identité documentaire : page Entreprise
 * (obligatoires « * », recommandées, facultatives), pays du référentiel, devise figée, aperçu
 * documentaire, étapes d'onboarding `company` et `configuration` (complétion définitive),
 * entreprise historique sans pays. STANDARD est publié le temps du test (inscription).
 */

const PASSWORD = 'E2e-Entreprise-2026';

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

async function signup(request: APIRequestContext, prefix: string) {
  const stamp = Date.now().toString();
  const email = `${prefix}-${stamp}@example.com`;
  const company = `Supérette ${prefix} ${stamp.slice(-6)}`;
  const response = await request.post('/api/v1/public/signup', {
    data: {
      account: { full_name: 'Awa Traoré', email, password: PASSWORD },
      company: { name: company, country_code: 'BF', currency: 'XOF', city: 'Ouagadougou' },
      business_profile: 'retail.alimentation',
      plan_code: 'STANDARD',
      billing_period: 'monthly',
    },
  });
  expect(response.status(), await response.text()).toBe(201);
  const token = ((await response.json()) as { access_token: string }).access_token;
  return { email, company, token };
}

async function chooseCountry(page: Page, name: string) {
  await page
    .locator('#country_code')
    .locator('xpath=ancestor::div[contains(@class,"p-dropdown")][1]')
    .click();
  const panel = page.locator('.p-dropdown-panel').last();
  await panel.locator('input.p-dropdown-filter').fill(name);
  await panel.getByRole('option', { name: new RegExp(`^${name}`) }).click();
}

const step = (page: Page, code: string) => page.getByTestId(`step-${code}`);

async function save(page: Page) {
  const saved = page.waitForResponse(
    (r) => r.url().endsWith('/api/v1/tenant') && r.request().method() === 'PATCH',
  );
  await page.getByRole('button', { name: 'Enregistrer' }).click();
  expect((await saved).status()).toBe(200);
}

test.describe('Entreprise et identité documentaire', () => {
  test('nouvelle entreprise : obligatoires, recommandations, aperçu, complétion définitive', async ({
    page,
    request,
  }) => {
    const { email, company, token } = await signup(request, 'e2e-entreprise');
    await loginUi(page, email, PASSWORD, company);
    await page.getByTestId('onboarding-banner').getByRole('button').click();
    // Nom, pays et devise demandés à l'inscription : étape « entreprise » terminée.
    await expect(step(page, 'company')).toContainText('Terminée');
    await expect(step(page, 'configuration')).toContainText('En cours'); // ville seulement
    await step(page, 'configuration')
      .getByRole('button', { name: 'Compléter les informations' })
      .click();

    await expect(page).toHaveURL(/\/organization\/company$/);
    for (const id of ['name', 'country_code', 'currency'])
      await expect(page.locator(`label[for="${id}"]`)).toContainText('*');
    for (const id of ['trade_name', 'email', 'phone', 'tax_id', 'website'])
      await expect(page.locator(`label[for="${id}"]`)).not.toContainText('*');
    await expect(page.locator('#name')).toHaveValue(company);
    await expect(page.locator('#currency')).toHaveValue('XOF');
    await expect(page.locator('#currency')).toBeDisabled();

    // Quelques informations recommandées : aperçu mis à jour, sans « N/A ».
    await page.getByLabel('Nom commercial').fill('Chez Awa');
    await page.getByLabel('Téléphone').fill('+226 70 11 22 33');
    await save(page);
    const preview = page.getByTestId('identity-preview');
    await expect(preview).toContainText('Chez Awa');
    await expect(preview).toContainText('Tél. : +22670112233');
    await expect(preview).toContainText('Ouagadougou, Burkina Faso');
    await expect(preview).not.toContainText('N/A');
    await expect(page.getByTestId('identity-completeness')).toContainText('3/9');

    // Les 9 informations recommandées : étape « configuration » terminée.
    await page.getByLabel(/^Logo/).fill('https://cdn.example.com/logo.png');
    await page.getByLabel('E-mail professionnel').fill('contact@chezawa.bf');
    await page.getByLabel('Adresse', { exact: true }).fill('Avenue Kwame Nkrumah');
    await page.getByLabel('Région').fill('Centre');
    await page.getByLabel('IFU').fill('00012345A');
    await page.getByLabel('RCCM').fill('BF-OUA-2026-B-1234');
    await save(page);
    await expect(page.getByTestId('identity-completeness')).toContainText('9/9');
    await expect(preview).toContainText('IFU : 00012345A');
    await expect(preview).toContainText('RCCM : BF-OUA-2026-B-1234');
    await expect(page.getByTestId('company-steps')).toContainText('Finaliser la configuration');

    // Effacer une information ensuite ne rouvre pas l'étape (complétion définitive).
    await page.getByLabel('Région').fill('');
    await save(page);
    await expect(preview).toContainText('Ouagadougou, Burkina Faso');
    await page.goto('/onboarding');
    await expect(step(page, 'configuration')).toContainText('Terminée');

    // La configuration de l'entreprise n'active jamais l'abonnement.
    const caps = (await (
      await request.get('/api/v1/me/capabilities', { headers: bearer(token) })
    ).json()) as { subscription: { status: string } };
    expect(caps.subscription.status).toBe('pending_activation');
  });

  test('entreprise historique : pays manquant, renseigné depuis la page Entreprise', async ({
    page,
    request,
  }) => {
    const stamp = Date.now().toString().slice(-7);
    const name = `Historique ${stamp}`;
    const email = `e2e-historique-${stamp}@example.com`;
    await provisionTenant(request, {
      name,
      profile: 'retail.alimentation',
      email,
      password: PASSWORD,
    });
    ownerSql(`UPDATE tenants SET country_code = NULL WHERE slug = 'historique-${stamp}'`);

    await loginUi(page, email, PASSWORD, name);
    await page.goto('/onboarding');
    await expect(step(page, 'company')).toContainText('En cours');
    await expect(step(page, 'configuration')).toContainText('À faire');
    await step(page, 'company').getByRole('button', { name: "Modifier l'entreprise" }).click();

    await expect(page.getByTestId('country-missing')).toBeVisible();
    // Pays exigé : rien n'est enregistré sans lui, aucun pays attribué par hypothèse.
    await page.getByRole('button', { name: 'Enregistrer' }).click();
    await expect(
      page.locator('#country_code').locator('xpath=ancestor::div[contains(@class,"sm-field")][1]'),
    ).toContainText('Champ obligatoire');
    await chooseCountry(page, 'Burkina Faso');
    await save(page);
    await expect(page.getByTestId('country-missing')).toHaveCount(0);
    await expect(page.getByTestId('identity-preview')).toContainText('Burkina Faso');

    await page.goto('/onboarding');
    await expect(step(page, 'company')).toContainText('Terminée');
  });

  test('page Entreprise sur mobile, sans débordement @mobile', async ({ page, request }) => {
    const { email, company } = await signup(request, 'e2e-entreprise-m');
    await loginUi(page, email, PASSWORD, company);
    await page.goto('/organization/company');
    await expect(page.getByTestId('identity-preview')).toBeVisible();
    expect(await overflow(page)).toBe(false);
  });
});
