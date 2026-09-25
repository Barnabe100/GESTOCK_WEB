import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import { bearer, loginUi, provisionTenant, tokenFor } from './support';

/**
 * Phase 3.2-D — Administration des utilisateurs (ADR-0029) : l'administrateur gère
 * l'appartenance à SON entreprise (rôles, sites, statut), jamais l'identité globale. Deux
 * entreprises créées par la CLI à chaque exécution ; un même compte « Jean » appartient aux deux.
 */

const PASSWORD = 'E2e-Utilisateurs-2026';
const TEMPORARY = 'Provisoire-Users-2026';

interface Member {
  id: string;
  user_id: string;
  email: string;
  full_name: string;
  status: string;
}

async function tenant(request: APIRequestContext, prefix: string) {
  const stamp = `${Date.now().toString().slice(-7)}${Math.floor(Math.random() * 90 + 10)}`;
  const name = `${prefix} ${stamp}`;
  const email = `e2e-${prefix.toLowerCase()}-${stamp}@example.com`;
  await provisionTenant(request, {
    name,
    profile: 'retail.alimentation',
    email,
    password: PASSWORD,
  });
  return { name, email, token: await tokenFor(request, email, PASSWORD, name) };
}

async function roleId(request: APIRequestContext, token: string, template: string) {
  const roles = (await (await request.get('/api/v1/roles', { headers: bearer(token) })).json()) as {
    id: string;
    template_code: string | null;
  }[];
  return roles.find((r) => r.template_code === template)?.id as string;
}

/** Membre créé par l'API puis connecté une première fois (mot de passe définitif). */
async function addMember(
  request: APIRequestContext,
  ownerToken: string,
  tenantName: string,
  email: string,
  data: Record<string, unknown>,
) {
  const created = await request.post('/api/v1/members', {
    headers: bearer(ownerToken),
    data: { email, full_name: email, password: TEMPORARY, ...data },
  });
  expect(created.status(), await created.text()).toBe(201);
  const first = await request.post('/api/v1/auth/login', { data: { email, password: TEMPORARY } });
  if (first.ok()) {
    const token = ((await first.json()) as { access_token: string }).access_token;
    await request.post('/api/v1/me/password', {
      headers: bearer(token),
      data: { current_password: TEMPORARY, new_password: PASSWORD },
    });
  }
  return tokenFor(request, email, PASSWORD, tenantName);
}

async function members(request: APIRequestContext, token: string, search: string) {
  const page = (await (
    await request.get(`/api/v1/members?search=${encodeURIComponent(search)}`, {
      headers: bearer(token),
    })
  ).json()) as { items: Member[] };
  return page.items;
}

async function pick(page: Page, inputId: string, option: string) {
  await page
    .locator(`#${inputId}`)
    .locator('xpath=ancestor::div[contains(@class,"p-multiselect")][1]')
    .click();
  await page.locator('.p-multiselect-panel').getByRole('option', { name: option }).click();
  // Fermer le panneau sans quitter le dialogue (Échap fermerait aussi le dialogue).
  await page.getByRole('dialog').locator('.p-dialog-title').click();
  await expect(page.locator('.p-multiselect-panel')).toHaveCount(0);
}

const row = (page: Page, text: string) => page.getByRole('row').filter({ hasText: text });

test.describe('Administration des utilisateurs', () => {
  test('ajout, compte global réutilisé, identité en lecture seule, désactivation limitée au tenant', async ({
    page,
    request,
  }) => {
    const a = await tenant(request, 'UtilA');
    const b = await tenant(request, 'UtilB');
    const jean = `e2e-jean-${Date.now()}@example.com`;
    await addMember(request, b.token, b.name, jean, {
      full_name: 'Jean Dupont',
      roles: [{ role_id: await roleId(request, b.token, 'manager') }],
      all_sites: true,
    });

    await loginUi(page, a.email, PASSWORD, a.name);
    await page.goto('/users/members');
    await expect(page.getByTestId('members-alone')).toBeVisible();

    // Scénario 1 : nouvel utilisateur (rôle Vendeur, site principal).
    const newcomer = `e2e-vendeur-${Date.now()}@example.com`;
    await page.getByRole('button', { name: 'Nouvel utilisateur' }).first().click();
    let dialog = page.getByRole('dialog');
    await dialog.locator('#member-email').fill(newcomer);
    await dialog.locator('#member-name').fill('Paul Kaboré');
    await dialog.locator('#member-password').fill(TEMPORARY);
    await pick(page, 'member-roles', 'Vendeur');
    await pick(page, 'member-sites', 'Site principal');
    await dialog.getByRole('button', { name: 'Enregistrer' }).click();
    await expect(row(page, 'Paul Kaboré')).toContainText('Vendeur');
    await expect(row(page, 'Paul Kaboré')).toContainText('Site principal');

    // Scénario 2 : compte existant (entreprise B) — réutilisé tel quel, aucun second compte.
    await page.getByRole('button', { name: 'Nouvel utilisateur' }).first().click();
    dialog = page.getByRole('dialog');
    await dialog.locator('#member-email').fill(jean);
    await dialog.locator('#member-name').fill('Nom saisi par A');
    await pick(page, 'member-roles', 'Vendeur');
    await dialog.getByRole('button', { name: 'Enregistrer' }).click();
    await expect(row(page, jean)).toContainText('Jean Dupont');
    const [inA] = await members(request, a.token, jean);
    const [inB] = await members(request, b.token, jean);
    expect(inA?.user_id).toBe(inB?.user_id);
    expect(inA?.full_name).toBe('Jean Dupont');

    // Scénario 5 : modifier l'accès — identité affichée en lecture seule, aucun mot de passe.
    await row(page, jean).getByRole('button', { name: "Modifier l'accès" }).click();
    dialog = page.getByRole('dialog');
    await expect(dialog.getByTestId('member-identity-name')).toHaveText('Jean Dupont');
    await expect(dialog.getByTestId('member-identity-email')).toHaveText(jean);
    await expect(dialog.getByText(/compte global de l'utilisateur/)).toBeVisible();
    await expect(dialog.locator('input[type="password"], #member-name, #member-email')).toHaveCount(
      0,
    );
    await dialog.getByRole('button', { name: 'Annuler' }).click();

    // Scénario 3 : désactivé dans A, toujours actif dans B.
    await row(page, jean).getByRole('button', { name: 'Désactiver' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Désactiver' }).click();
    await expect(row(page, jean)).toContainText('Inactif');
    const login = await request.post('/api/v1/auth/login', {
      data: { email: jean, password: PASSWORD },
    });
    const session = (await login.json()) as { memberships: { tenant_name: string }[] };
    expect(session.memberships.map((m) => m.tenant_name)).toEqual([b.name]);
    const inBToken = await tokenFor(request, jean, PASSWORD, b.name);
    const caps = await request.get('/api/v1/me/capabilities', { headers: bearer(inBToken) });
    expect(caps.status()).toBe(200);
    expect((await members(request, b.token, jean))[0]?.status).toBe('active');

    // Réactivation.
    await row(page, jean).getByRole('button', { name: 'Activer' }).click();
    await expect(row(page, jean)).toContainText('Actif');
  });

  test('anti-escalade : rôle non délégable refusé par le serveur', async ({ page, request }) => {
    const a = await tenant(request, 'UtilC');
    const hrRole = await request.post('/api/v1/roles', {
      headers: bearer(a.token),
      data: {
        name: 'RH',
        permissions: ['users.member.view', 'users.member.manage', 'users.role.view'],
      },
    });
    expect(hrRole.status()).toBe(201);
    const hr = `e2e-rh-${Date.now()}@example.com`;
    await addMember(request, a.token, a.name, hr, {
      roles: [{ role_id: ((await hrRole.json()) as { id: string }).id }],
      all_sites: true,
    });

    await loginUi(page, hr, PASSWORD, a.name);
    await page.goto('/users/members');
    await page.getByRole('button', { name: 'Nouvel utilisateur' }).first().click();
    const dialog = page.getByRole('dialog');
    await dialog.locator('#member-email').fill(`e2e-cible-${Date.now()}@example.com`);
    await dialog.locator('#member-name').fill('Cible');
    await dialog.locator('#member-password').fill(TEMPORARY);
    await pick(page, 'member-roles', 'Administrateur');
    await dialog.getByRole('button', { name: 'Enregistrer' }).click();
    await expect(
      page.getByText('Vous ne pouvez pas accorder des permissions que vous ne détenez pas.'),
    ).toBeVisible();
    await expect(dialog).toBeVisible();
  });

  test('liste des utilisateurs sur mobile, sans débordement @mobile', async ({ page, request }) => {
    const a = await tenant(request, 'UtilM');
    await loginUi(page, a.email, PASSWORD, a.name);
    await page.goto('/users/members');
    await expect(page.getByText(a.email)).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth),
    ).toBe(false);
  });
});
