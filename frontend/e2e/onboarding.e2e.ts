import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import { bearer, loginUi, ownerSql } from './support';

/**
 * Phase 3.2-B — Onboarding persistant : nouvelle inscription (STANDARD publié le temps du
 * test, sans essai → abonnement en attente d'activation, sans site), bandeau du tableau de
 * bord, page d'installation, création du premier site depuis l'étape, onboarding terminé mais
 * abonnement toujours en attente et opérations métier toujours refusées.
 */

const PASSWORD = 'E2e-Onboarding-2026';

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

/** Inscription publique par l'API ; renvoie l'e-mail, l'entreprise et le jeton. */
async function signup(request: APIRequestContext, prefix: string) {
  const stamp = Date.now().toString();
  const email = `${prefix}-${stamp}@example.com`;
  const company = `Supérette ${prefix} ${stamp.slice(-6)}`;
  const response = await request.post('/api/v1/public/signup', {
    data: {
      account: { full_name: 'Awa Traoré', email, password: PASSWORD },
      company: {
        name: company,
        country_code: 'BF',
        currency: 'XOF',
        trade_name: 'Chez Awa',
        city: 'Ouagadougou',
      },
      business_profile: 'retail.alimentation',
      plan_code: 'STANDARD',
      billing_period: 'monthly',
    },
  });
  expect(response.status(), await response.text()).toBe(201);
  const token = ((await response.json()) as { access_token: string }).access_token;
  return { email, company, token };
}

test.describe('Onboarding', () => {
  test('inscription → bandeau → installation → premier site ; activation inchangée', async ({
    page,
    request,
  }) => {
    const { email, company, token } = await signup(request, 'e2e-onboarding');
    await loginUi(page, email, PASSWORD, company);

    // Tableau de bord : progression et prochaine étape tant que l'installation n'est pas finie.
    const banner = page.getByTestId('onboarding-banner');
    await expect(banner).toContainText('Installation terminée à 50 %');
    await expect(banner).toContainText('Votre premier site');
    await banner.getByRole('button', { name: "Continuer l'installation" }).click();

    await expect(page).toHaveURL(/\/onboarding$/);
    await expect(page.getByRole('heading', { name: 'Votre installation' })).toBeVisible();
    await expect(page.getByTestId('step-company')).toContainText('Terminée');
    await expect(page.getByTestId('step-business_profile')).toContainText('Terminée');
    await expect(page.getByTestId('step-subscription')).toContainText('Terminée');
    await expect(page.getByTestId('step-first_site')).toContainText('Obligatoire');
    await expect(page.getByTestId('step-users')).toContainText('Recommandée');
    await expect(page.getByRole('button', { name: /ignorer/i })).toHaveCount(0);

    // L'action de l'étape ouvre directement le formulaire de création du site.
    await page
      .getByTestId('onboarding-next')
      .getByRole('button', { name: 'Créer mon premier site' })
      .click();
    await expect(page).toHaveURL(/\/organization\/sites\?create=1$/);
    const dialog = page.getByRole('dialog', { name: 'Nouveau site' });
    await dialog.locator('#site-name').fill('Boutique centrale');
    await dialog.locator('#site-code').fill('CENTRE');
    await dialog.getByRole('button', { name: 'Enregistrer' }).click();
    await expect(page.getByRole('cell', { name: 'Boutique centrale' })).toBeVisible();

    // Étapes obligatoires terminées : installation terminée (62 % : les recommandations
    // restantes ne comptent pas comme faites, mais ne bloquent pas).
    await page.goto('/onboarding');
    await expect(page.getByTestId('step-first_site')).toContainText('Terminée');
    await expect(page.getByText('Installation terminée à 62 %')).toBeVisible();
    await expect(page.getByTestId('onboarding-completed')).toContainText("attente d'activation");
    await expect(page.getByTestId('step-catalogue').getByTestId('blocked-catalogue')).toContainText(
      "Disponible après l'activation de votre abonnement.",
    );
    await page.goto('/');
    await expect(page.getByText('Modules de votre offre')).toBeVisible();
    await expect(page.getByTestId('onboarding-banner')).toHaveCount(0);

    // Onboarding ≠ activation : l'abonnement reste en attente, le métier reste refusé.
    const caps = (await (
      await request.get('/api/v1/me/capabilities', { headers: bearer(token) })
    ).json()) as { subscription: { status: string } };
    expect(caps.subscription.status).toBe('pending_activation');
    const refused = await request.post('/api/v1/catalog/categories', {
      headers: bearer(token),
      data: { name: 'Riz' },
    });
    expect(refused.status()).toBe(403);
    expect(((await refused.json()) as { code: string }).code).toBe('subscription_restricted');
    // Le client ne peut pas déclarer une étape terminée.
    const declared = await request.patch('/api/v1/onboarding/steps/users', {
      headers: bearer(token),
      data: { status: 'COMPLETED' },
    });
    expect(declared.status()).toBe(422);
  });

  test('installation sur mobile, sans débordement @mobile', async ({ page, request }) => {
    const { email, company } = await signup(request, 'e2e-onboarding-m');
    await loginUi(page, email, PASSWORD, company);
    await expect(page.getByTestId('onboarding-banner')).toBeVisible();
    expect(await overflow(page)).toBe(false);
    await page.getByRole('button', { name: "Continuer l'installation" }).click();
    await expect(page.getByTestId('step-configuration')).toContainText('En cours');
    expect(await overflow(page)).toBe(false);
  });
});
