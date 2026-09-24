import { expect, test, type Page } from '@playwright/test';

import { apiToken, bearer, loginUi, OWNER } from './support';

/**
 * Phase 2.5-B — Design System : navigation groupée, tableau de bord, conventions de liste
 * (recherche, « Aucun résultat », réinitialisation), confirmation des actions sensibles et
 * saisie d'une entrée de stock par l'interface.
 */

async function choose(page: Page, inputId: string, label: string) {
  await page.locator('.p-dropdown', { has: page.locator(`#${inputId}`) }).click();
  await page
    .locator('.p-dropdown-panel')
    .last()
    .locator('.p-dropdown-item', { hasText: label })
    .click();
}

test.describe('Design System', () => {
  test('navigation groupée, tableau de bord, liste standard, confirmation, entrée de stock', async ({
    page,
    request,
  }) => {
    const token = await apiToken(request, OWNER.email, OWNER.password);
    const headers = bearer(token);
    const suffix = Date.now().toString().slice(-7);
    const post = async (path: string, data: unknown) => {
      const response = await request.post(`/api/v1${path}`, { headers, data });
      expect(response.status(), await response.text()).toBe(201);
      return (await response.json()) as { id: string };
    };
    const category = await post('/catalog/categories', { name: `UI E2E ${suffix}` });
    const reference = `E2E-UI${suffix}`;
    await post('/catalog/articles', {
      reference,
      designation: `Tournevis UI ${suffix}`,
      category_id: category.id,
      unit: 'u',
      purchase_price: '800',
      sale_price: '1200',
    });
    const spare = `UI Libre ${suffix}`;
    await post('/catalog/categories', { name: spare });
    const sites = (await (await request.get('/api/v1/sites', { headers })).json()) as {
      id: string;
      name: string;
    }[];

    await loginUi(page, OWNER.email, OWNER.password);

    // Navigation groupée par domaine.
    const menu = page.getByRole('navigation', { name: 'Menu' });
    for (const group of ['Catalogue', 'Stock', 'Ventes et clients', 'Administration']) {
      await expect(menu.getByText(group, { exact: true })).toBeVisible();
    }

    // Tableau de bord : indicateurs et actions rapides selon les droits du propriétaire.
    await expect(page.getByRole('region', { name: 'Indicateurs' })).toBeVisible();
    await expect(page.getByRole('navigation', { name: 'Actions rapides' })).toBeVisible();

    // Liste standard : recherche, aucun résultat, réinitialisation.
    await menu.getByRole('link', { name: 'Catégories', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Catégories' })).toBeVisible();
    const search = page.getByRole('searchbox').or(page.getByLabel('Rechercher…'));
    const reset = page.getByRole('button', { name: 'Réinitialiser' });
    await expect(reset).toBeDisabled();
    await search.fill(`introuvable-${suffix}`);
    await expect(page.getByText('Aucun résultat')).toBeVisible();
    await expect(reset).toBeEnabled();
    await reset.click();
    await expect(search).toHaveValue('');
    await search.fill(spare);
    const row = page.getByRole('row').filter({ hasText: spare });
    await expect(row).toBeVisible();

    // Action sensible : désactivation confirmée ; annuler ne change rien.
    await row.getByRole('button', { name: 'Désactiver' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByText(`Désactiver la catégorie « ${spare} »`)).toBeVisible();
    await dialog.getByRole('button', { name: 'Annuler' }).click();
    await expect(row.getByText('Actif', { exact: true })).toBeVisible();
    await row.getByRole('button', { name: 'Désactiver' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Désactiver' }).click();
    await expect(row.getByText('Inactif', { exact: true })).toBeVisible();

    // Entrée de stock saisie par l'interface, validée après confirmation.
    await menu.getByRole('link', { name: 'Entrées de stock', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Entrées de stock' })).toBeVisible();
    await page.getByRole('button', { name: 'Nouvelle entrée' }).first().click();
    await expect(page.getByRole('heading', { name: 'Nouvelle entrée' })).toBeVisible();
    await expect(page.getByRole('navigation', { name: "Fil d'Ariane" })).toContainText(
      'Entrées de stock',
    );
    if (await page.locator('#doc-site').isVisible()) {
      await choose(page, 'doc-site', sites[0]?.name ?? '');
    }
    await choose(page, 'doc-kind', 'Stock initial');
    await page.getByRole('button', { name: 'Ajouter une ligne' }).click();
    await page.locator('#line-0-article').fill(reference);
    await page
      .getByRole('option', { name: new RegExp(reference) })
      .first()
      .click();
    await page.locator('#line-0-quantity').fill('4');
    await page.locator('#line-0-cost').fill('800');
    await page.getByRole('button', { name: 'Valider', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Valider' }).click();
    await expect(page.getByText(/Document ENT-\d{6} validé/)).toBeVisible();
    await expect(page.getByText('Validé', { exact: true })).toBeVisible();

    // Le niveau de stock reflète l'entrée.
    await menu.getByRole('link', { name: 'Stock par site', exact: true }).click();
    await page.getByLabel('Rechercher…').fill(reference);
    await expect(page.getByRole('row').filter({ hasText: reference }).first()).toContainText('4 u');
  });
});
