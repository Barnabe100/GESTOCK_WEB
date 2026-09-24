import { expect, test } from '@playwright/test';

import { apiToken, bearer, loginUi, OWNER, unique } from './support';

/**
 * Phase 2.3 — Clients : parcours complet avec le vrai backend (RLS, RBAC, audit).
 * Les données créées portent un suffixe unique : la suite peut être rejouée.
 */
test.describe('Clients', () => {
  test('créer, rechercher, modifier, désactiver, réactiver, auditer', async ({ page }) => {
    const name = unique('Awa Traoré');
    const phone = `7${Date.now().toString().slice(-7)}`;
    await loginUi(page, OWNER.email, OWNER.password);

    await page.getByRole('link', { name: 'Clients' }).click();
    await expect(page.getByRole('heading', { name: 'Clients' })).toBeVisible();

    // Création : validation côté client, puis enregistrement.
    await page.getByRole('button', { name: 'Nouveau client' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByRole('button', { name: 'Enregistrer' }).click();
    await expect(dialog.getByText('Champ obligatoire')).toBeVisible();
    await dialog.getByLabel('Nom et prénom').fill(name);
    await dialog.getByLabel('Téléphone', { exact: true }).fill(phone.replace(/(\d{2})/g, '$1 '));
    await dialog.getByLabel('Email').fill('awa.e2e@example.com');
    await dialog.getByRole('button', { name: 'Enregistrer' }).click();
    await expect(page.getByText(/Client CLI-\d{6} créé/)).toBeVisible();

    // Recherche par téléphone (saisi avec espaces, stocké normalisé).
    await page.getByRole('searchbox').fill(phone.slice(0, 4) + ' ' + phone.slice(4));
    const row = page.getByRole('row').filter({ hasText: name });
    await expect(row).toBeVisible();
    await expect(page.getByRole('row')).toHaveCount(2); // en-tête + résultat
    const code = (await row.getByRole('cell').first().innerText()).trim();
    expect(code).toMatch(/^CLI-\d{6}$/);

    // Modification.
    await row.getByRole('button', { name: 'Modifier' }).click();
    await dialog.getByLabel('Ville').fill('Ouagadougou');
    await dialog.getByRole('button', { name: 'Enregistrer' }).click();
    await expect(page.getByText('Client modifié')).toBeVisible();

    // Désactivation puis réactivation depuis la fiche.
    await row.getByRole('button', { name: 'Voir la fiche' }).click();
    await expect(page.getByRole('heading', { name })).toBeVisible();
    await expect(page.getByText('Ouagadougou')).toBeVisible();
    await page.getByRole('button', { name: 'Désactiver' }).click();
    await expect(page.getByText('Client désactivé', { exact: true })).toBeVisible();
    await expect(page.getByText(/Client désactivé : consultable/)).toBeVisible();
    await page.getByRole('button', { name: 'Activer' }).click();
    await expect(page.getByText('Client réactivé')).toBeVisible();

    // Audit : les quatre opérations sont tracées.
    await page.getByRole('link', { name: "Journal d'audit" }).click();
    for (const action of [
      'customer.activated',
      'customer.deactivated',
      'customer.updated',
      'customer.created',
    ]) {
      await expect(
        page.getByRole('row').filter({ hasText: action }).filter({ hasText: code }).first(),
      ).toBeVisible();
    }
  });

  test('le Vendeur consulte sans pouvoir créer ni modifier', async ({ page, request }) => {
    const token = await apiToken(request, OWNER.email, OWNER.password);
    const roles = (await (
      await request.get('/api/v1/roles', { headers: bearer(token) })
    ).json()) as {
      id: string;
      template_code: string | null;
    }[];
    const seller = roles.find((r) => r.template_code === 'seller');
    const email = `vendeur-${Date.now()}@example.com`;
    const temporary = 'Provisoire-E2E-2026';
    const created = await request.post('/api/v1/members', {
      headers: bearer(token),
      data: {
        email,
        full_name: unique('Vendeur'),
        password: temporary,
        roles: [{ role_id: seller?.id }],
        all_sites: true,
      },
    });
    expect(created.status(), await created.text()).toBe(201);
    const firstToken = await apiToken(request, email, temporary);
    const definitive = 'Vendeur-E2E-Definitif-2026';
    const changed = await request.post('/api/v1/me/password', {
      headers: bearer(firstToken),
      data: { current_password: temporary, new_password: definitive },
    });
    expect(changed.status()).toBe(204);

    await loginUi(page, email, definitive);
    await page.getByRole('link', { name: 'Clients' }).click();
    await expect(page.getByRole('heading', { name: 'Clients' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Nouveau client' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Modifier' })).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'Rôles' })).toHaveCount(0);

    // Le backend refuse, quel que soit l'écran.
    const sellerToken = await apiToken(request, email, definitive);
    const forbidden = await request.post('/api/v1/customers', {
      headers: bearer(sellerToken),
      data: { customer_type: 'INDIVIDUAL', name: 'Interdit' },
    });
    expect(forbidden.status()).toBe(403);
    expect(((await forbidden.json()) as { code: string }).code).toBe('permission_denied');
  });

  test('affichage mobile sans débordement @mobile', async ({ page }) => {
    await loginUi(page, OWNER.email, OWNER.password);
    await page.goto('/customers');
    await expect(page.getByRole('heading', { name: 'Clients' })).toBeVisible();
    const overflow = () =>
      page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    expect(await overflow()).toBe(false);
    await page.getByRole('button', { name: 'Nouveau client' }).click();
    await expect(page.getByRole('dialog').getByLabel('Nom et prénom')).toBeVisible();
    expect(await overflow()).toBe(false);
  });
});
