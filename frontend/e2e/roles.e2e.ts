import { expect, test, type APIRequestContext } from '@playwright/test';

import { bearer, loginUi, provisionTenant, tokenFor } from './support';

/**
 * Phase 3.2-E — Rôles, permissions et délégation RBAC (ADR-0030). La délégation est calculée
 * par le serveur : un utilisateur « RH » (rôle personnalisé) ne se voit proposer que les
 * permissions et les rôles qu'il détient ; le serveur refuse de toute façon le reste.
 */

const PASSWORD = 'E2e-Roles-2026';
const TEMPORARY = 'Provisoire-Roles-2026';

async function tenantWithHr(request: APIRequestContext) {
  const stamp = `${Date.now().toString().slice(-7)}${Math.floor(Math.random() * 90 + 10)}`;
  const name = `Roles ${stamp}`;
  const ownerEmail = `e2e-roles-${stamp}@example.com`;
  await provisionTenant(request, {
    name,
    profile: 'retail.alimentation',
    email: ownerEmail,
    password: PASSWORD,
  });
  const owner = await tokenFor(request, ownerEmail, PASSWORD, name);
  const role = await request.post('/api/v1/roles', {
    headers: bearer(owner),
    data: {
      name: 'RH',
      permissions: [
        'users.role.view',
        'users.role.manage',
        'users.member.view',
        'users.member.manage',
        'catalog.article.view',
      ],
    },
  });
  expect(role.status(), await role.text()).toBe(201);
  const hrEmail = `e2e-rh-${stamp}@example.com`;
  const created = await request.post('/api/v1/members', {
    headers: bearer(owner),
    data: {
      email: hrEmail,
      full_name: 'Responsable RH',
      password: TEMPORARY,
      roles: [{ role_id: ((await role.json()) as { id: string }).id }],
      all_sites: true,
    },
  });
  expect(created.status()).toBe(201);
  const first = await request.post('/api/v1/auth/login', {
    data: { email: hrEmail, password: TEMPORARY },
  });
  await request.post('/api/v1/me/password', {
    headers: bearer(((await first.json()) as { access_token: string }).access_token),
    data: { current_password: TEMPORARY, new_password: PASSWORD },
  });
  return { name, hrEmail, hr: await tokenFor(request, hrEmail, PASSWORD, name) };
}

test.describe('Rôles et délégation', () => {
  test('le RH ne délègue que ce qu’il détient ; le serveur refuse le reste', async ({
    page,
    request,
  }) => {
    const { name, hrEmail, hr } = await tenantWithHr(request);
    await loginUi(page, hrEmail, PASSWORD, name);
    await page.goto('/users/roles');

    // Rôles de base hors de son périmètre : signalés, sans action de modification.
    const seller = page.getByRole('row').filter({ hasText: 'Vendeur' });
    await expect(seller).toContainText('Hors de votre périmètre');
    await expect(seller.getByRole('button', { name: 'Désactiver' })).toHaveCount(0);

    // Nouveau rôle : seules les permissions délégables sont cochables.
    await page.getByRole('button', { name: 'Nouveau rôle' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.locator('#role-name').fill('Lecteur catalogue');
    await expect(dialog.getByLabel('Créer un article')).toBeDisabled();
    await dialog.getByLabel('Consulter le catalogue').check();
    await dialog.getByRole('button', { name: 'Enregistrer' }).click();
    await expect(page.getByRole('row').filter({ hasText: 'Lecteur catalogue' })).toBeVisible();

    // Par l'API directement : refus serveur (anti-escalade), quelle que soit l'interface.
    const forced = await request.post('/api/v1/roles', {
      headers: bearer(hr),
      data: { name: 'Contournement', permissions: ['catalog.article.create'] },
    });
    expect(forced.status()).toBe(403);
    expect(((await forced.json()) as { code: string }).code).toBe('permission_escalation');
    const delegable = (await (
      await request.get('/api/v1/permissions/delegable', { headers: bearer(hr) })
    ).json()) as { code: string }[];
    expect(delegable.map((p) => p.code).sort()).toEqual([
      'catalog.article.view',
      'users.member.manage',
      'users.member.view',
      'users.role.manage',
      'users.role.view',
    ]);
  });
});
