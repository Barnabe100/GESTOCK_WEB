import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import { bearer, ensureCashOpen, loginUi, provisionTenant, tokenFor } from './support';

/**
 * Palier D — Changement du profil d'UN site (ADR-0048), sur une entreprise dédiée (créée à
 * chaque exécution : aucun autre scénario n'en dépend). Aperçu calculé par le serveur ;
 * SIMPLE confirmé normalement ; BLOCKED par une session de caisse ouverte vers un profil sans
 * caisse : aucune confirmation possible, création d'un nouveau site proposée ; mobile sans
 * débordement.
 */

const PASSWORD = 'E2e-ProfilSite-2026';

const overflow = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);

interface Site {
  id: string;
  name: string;
  business_profile_code: string;
}

async function setup(request: APIRequestContext, prefix: string) {
  const stamp = Date.now().toString().slice(-7);
  const name = `Profil Site ${prefix} ${stamp}`;
  const email = `profil-site-${prefix}-${stamp}@example.com`;
  await provisionTenant(request, {
    name,
    profile: 'retail.quincaillerie',
    email,
    password: PASSWORD,
  });
  const token = await tokenFor(request, email, PASSWORD, name);
  const sites = (await (
    await request.get('/api/v1/sites', { headers: bearer(token) })
  ).json()) as Site[];
  return { name, email, token, site: sites[0] as Site };
}

async function openProfileDialog(page: Page, siteName: string, target: string) {
  await page.goto('/organization/sites');
  const row = page.getByRole('row', { name: new RegExp(siteName) });
  await row.getByRole('button', { name: 'Changer le profil' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await dialog.locator('#site-profile-target').click();
  await page.getByRole('option', { name: target, exact: true }).click();
  await expect(dialog.getByTestId('profile-preview')).toBeVisible();
  return dialog;
}

test('changement simple du profil d’un site, puis blocage par une caisse ouverte', async ({
  page,
  request,
}) => {
  const w = await setup(request, 'd');
  await loginUi(page, w.email, PASSWORD, w.name);

  // SIMPLE : site vide → profil Restaurant / maquis, confirmation normale.
  let dialog = await openProfileDialog(page, w.site.name, 'Maquis');
  await expect(dialog.getByText('Changement simple')).toBeVisible();
  await expect(dialog.getByRole('heading', { name: 'Modules ajoutés' })).toBeVisible();
  await expect(dialog.locator('#site-profile-confirmation')).toHaveCount(0);
  await dialog.getByRole('button', { name: 'Changer de profil' }).click();
  await expect(page.getByText('Profil du site modifié')).toBeVisible();
  await expect(page.getByRole('row', { name: new RegExp(w.site.name) })).toContainText('Maquis');
  const after = (await (
    await request.get(`/api/v1/sites/${w.site.id}`, { headers: bearer(w.token) })
  ).json()) as Site;
  expect(after.business_profile_code).toBe('restaurant.maquis');

  // BLOCKED : session de caisse ouverte, vers un profil sans caisse.
  const session = await ensureCashOpen(request, w.token, w.site.id);
  dialog = await openProfileDialog(page, w.site.name, 'Entrepôt');
  await expect(dialog.getByText('Changement impossible')).toBeVisible();
  await expect(dialog.getByText(/Sessions de caisse ouvertes : 1/)).toBeVisible();
  await expect(dialog.getByRole('button', { name: 'Changer de profil' })).toHaveCount(0);
  await expect(
    dialog.getByRole('button', { name: 'Créer un nouveau site avec ce profil' }),
  ).toBeVisible();
  await dialog.getByRole('button', { name: 'Annuler' }).click();
  const closed = await request.post(`/api/v1/cash/sessions/${session.id}/close`, {
    headers: bearer(w.token),
    data: { counted_balance: '0' },
  });
  expect(closed.status(), await closed.text()).toBe(200);
});

test('changement de profil sur mobile, sans débordement @mobile', async ({ page, request }) => {
  const w = await setup(request, 'm');
  await loginUi(page, w.email, PASSWORD, w.name);
  const dialog = await openProfileDialog(page, w.site.name, 'Maquis');
  await expect(dialog.getByText('Changement simple')).toBeVisible();
  expect(await overflow(page)).toBe(false);
});
