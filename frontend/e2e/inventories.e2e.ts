import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import { apiToken, bearer, createMember, loginUi, OWNER, STANDARD_OWNER, unique } from './support';

const PASSWORD_MEMBER = 'Membre-Inventaire-2026';

/**
 * Phase 2.6 — Inventaires : parcours complet avec le vrai backend (StockService, RLS, RBAC).
 * Chaque test crée son propre article et son stock (suffixe unique) sur le site principal.
 */

interface Site {
  id: string;
  name: string;
  code: string;
}

interface Setup {
  token: string;
  reference: string;
  articleId: string;
  site: Site;
}

/** Article stocké : 20 u sur le site principal, coût 500. */
async function setup(request: APIRequestContext, account = OWNER): Promise<Setup> {
  const token = await apiToken(request, account.email, account.password);
  const headers = bearer(token);
  const post = async (path: string, data: unknown, status = 201) => {
    const response = await request.post(`/api/v1${path}`, { headers, data });
    expect(response.status(), await response.text()).toBe(status);
    return (await response.json()) as { id: string };
  };
  const sites = (await (await request.get('/api/v1/sites', { headers })).json()) as Site[];
  const site = sites.find((s) => s.code !== 'DEPOT-E2E') ?? (sites[0] as Site);
  const suffix = Date.now().toString().slice(-7);
  const reference = `E2E-I${suffix}`;
  const category = await post('/catalog/categories', { name: `Inventaires E2E ${suffix}` });
  const article = await post('/catalog/articles', {
    reference,
    designation: `Ciment 50 kg E2E ${suffix}`,
    category_id: category.id,
    unit: 'u',
    purchase_price: '500',
    sale_price: '700',
  });
  const entry = await post('/stock/entries', {
    site_id: site.id,
    kind: 'INITIAL_STOCK',
    lines: [{ article_id: article.id, quantity: '20', unit_cost: '500' }],
  });
  await post(`/stock/entries/${entry.id}/validate`, {}, 200);
  return { token, reference, articleId: article.id, site };
}

async function stockOf(request: APIRequestContext, s: Setup): Promise<string | undefined> {
  const response = await request.get(
    `/api/v1/stock/levels?search=${s.reference}&site_id=${s.site.id}`,
    { headers: bearer(s.token) },
  );
  const page = (await response.json()) as { items: { quantity: string }[] };
  return page.items[0]?.quantity;
}

/** Inventaire ciblé créé par l'API (brouillon) ; renvoie son identifiant et son numéro. */
async function draftByApi(request: APIRequestContext, s: Setup) {
  const response = await request.post('/api/v1/inventories', {
    headers: bearer(s.token),
    data: { site_id: s.site.id, inventory_type: 'TARGETED', article_ids: [s.articleId] },
  });
  expect(response.status(), await response.text()).toBe(201);
  return (await response.json()) as { id: string; number: string };
}

async function choose(page: Page, inputId: string, label: string) {
  await page.locator('.p-dropdown', { has: page.locator(`#${inputId}`) }).click();
  await page
    .locator('.p-dropdown-panel')
    .last()
    .locator('.p-dropdown-item', { hasText: label })
    .click();
}

test.describe('Inventaires', () => {
  test('parcours complet : création, comptage, vente pendant le comptage, validation', async ({
    page,
    request,
  }) => {
    const s = await setup(request);
    await loginUi(page, OWNER.email, OWNER.password);

    // Stock ▸ Inventaires.
    const menu = page.getByRole('navigation', { name: 'Menu' });
    await menu.getByRole('link', { name: 'Inventaires', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Inventaires' })).toBeVisible();
    await page.getByRole('button', { name: 'Nouvel inventaire' }).first().click();
    await expect(page.getByRole('heading', { name: 'Nouvel inventaire' })).toBeVisible();

    // Site, type ciblé, article recherché côté serveur.
    await choose(page, 'inventory-site', s.site.name);
    await page.getByLabel(/Ciblé/).check();
    await page.locator('#inventory-article').fill(s.reference);
    await page
      .getByRole('option', { name: new RegExp(s.reference) })
      .first()
      .click();
    const selected = page.getByRole('row').filter({ hasText: s.reference });
    await expect(selected).toContainText('20 u');
    await page.getByRole('button', { name: "Créer l'inventaire" }).click();
    const heading = page.getByRole('heading', { name: /^Inventaire INV-\d{6}$/ });
    await expect(heading).toBeVisible();
    const number = ((await heading.innerText()).match(/INV-\d{6}/) ?? [''])[0];
    await expect(page.getByText('Brouillon', { exact: true })).toBeVisible();

    // Comptage : 17 constatés (stock théorique initial 20).
    await page.getByRole('button', { name: 'Démarrer le comptage' }).click();
    await expect(page.getByText('Comptage en cours', { exact: true })).toBeVisible();
    await expect(page.getByText('Articles comptés : 0 / 1')).toBeVisible();
    const input = page.getByLabel(`Quantité physique de ${s.reference}`);
    await input.fill('17');
    await input.press('Enter');
    await expect(page.getByText('Articles comptés : 1 / 1')).toBeVisible();
    const row = page.getByRole('row').filter({ hasText: s.reference });
    await expect(row).toContainText('Manquant');

    // Vente de 2 pendant le comptage : stock courant 18.
    const headers = bearer(s.token);
    const sale = await request.post('/api/v1/sales', {
      headers,
      data: { site_id: s.site.id, lines: [{ article_id: s.articleId, quantity: '2' }] },
    });
    expect(sale.status()).toBe(201);
    const saleId = ((await sale.json()) as { id: string }).id;
    expect((await request.post(`/api/v1/sales/${saleId}/validate`, { headers })).status()).toBe(
      200,
    );
    expect(await stockOf(request, s)).toBe('18.000');

    // Terminer, puis valider après confirmation (résumé affiché).
    await page.getByRole('button', { name: 'Terminer le comptage' }).click();
    await expect(page.getByText('À valider', { exact: true })).toBeVisible();
    await expect(page.getByRole('region', { name: 'Résumé des écarts' })).toBeVisible();
    await page.getByRole('button', { name: "Valider l'inventaire" }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText('applique les ajustements au stock');
    await dialog.getByRole('button', { name: "Valider l'inventaire" }).click();
    await expect(page.getByText(`Inventaire ${number} validé : stock ajusté`)).toBeVisible();

    // Validé : lecture seule ; écart calculé sur le stock courant (17 − 18 = −1).
    await expect(page.getByText('Validé', { exact: true })).toBeVisible();
    await expect(page.getByText(/lecture seule/)).toBeVisible();
    await expect(page.getByLabel(`Quantité physique de ${s.reference}`)).toHaveCount(0);
    await expect(page.getByRole('button', { name: "Annuler l'inventaire" })).toHaveCount(0);
    await expect(row).toContainText('18 u'); // stock à la validation
    await expect(row).toContainText('-1');
    expect(await stockOf(request, s)).toBe('17.000');

    // Mouvement d'ajustement dans le journal.
    await page.getByRole('link', { name: "Voir les mouvements d'ajustement" }).click();
    await expect(page.getByRole('heading', { name: 'Journal des mouvements' })).toBeVisible();
    const movement = page.getByRole('row').filter({ hasText: number });
    await expect(movement).toHaveCount(1);
    await expect(movement).toContainText('Ajustement');
  });

  test('Vendeur : consultation seule, aucune création', async ({ page, request }) => {
    const s = await setup(request);
    const draft = await draftByApi(request, s);
    const seller = await createMember(request, s.token, 'seller', PASSWORD_MEMBER);
    await loginUi(page, seller, PASSWORD_MEMBER);
    await page
      .getByRole('navigation', { name: 'Menu' })
      .getByRole('link', { name: 'Inventaires', exact: true })
      .click();
    await expect(page.getByRole('heading', { name: 'Inventaires' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Nouvel inventaire' })).toHaveCount(0);
    await page.goto(`/inventories/${draft.id}`);
    await expect(page.getByRole('heading', { name: `Inventaire ${draft.number}` })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Démarrer le comptage' })).toHaveCount(0);
    // Route de création absente pour ce rôle.
    await page.goto('/inventories/new');
    await expect(page.getByText('Page introuvable')).toBeVisible();
    // Le backend refuse, quel que soit l'écran.
    const token = await apiToken(request, seller, PASSWORD_MEMBER);
    const denied = await request.post(`/api/v1/inventories/${draft.id}/start`, {
      headers: bearer(token),
    });
    expect(denied.status()).toBe(403);
  });

  test('isolation : site non autorisé et autre entreprise', async ({ page, request }) => {
    const s = await setup(request);
    const draft = await draftByApi(request, s);
    // Autre entreprise (plan STANDARD) : l'inventaire est introuvable.
    await loginUi(page, STANDARD_OWNER.email, STANDARD_OWNER.password, STANDARD_OWNER.tenant);
    await page.goto(`/inventories/${draft.id}`);
    await expect(page.getByText('Inventaire introuvable.')).toBeVisible();
    const other = await apiToken(request, STANDARD_OWNER.email, STANDARD_OWNER.password);
    const response = await request.get(`/api/v1/inventories/${draft.id}`, {
      headers: bearer(other),
    });
    expect(response.status()).toBe(404);
    // Membre limité à un autre site du tenant : ni lecture ni création sur ce site.
    const sites = (await (
      await request.get('/api/v1/sites', { headers: bearer(s.token) })
    ).json()) as Site[];
    const elsewhere = sites.find((x) => x.id !== s.site.id);
    test.skip(!elsewhere, 'entreprise de test mono-site');
    const roles = (await (
      await request.get('/api/v1/roles', { headers: bearer(s.token) })
    ).json()) as { id: string; template_code: string | null }[];
    const manager = roles.find((r) => r.template_code === 'manager');
    const email = `site-${Date.now()}@example.com`;
    const created = await request.post('/api/v1/members', {
      headers: bearer(s.token),
      data: {
        email,
        full_name: unique('Site limité'),
        password: 'Provisoire-E2E-2026',
        roles: [{ role_id: manager?.id }],
        site_ids: [elsewhere?.id],
      },
    });
    expect(created.status(), await created.text()).toBe(201);
    const first = await apiToken(request, email, 'Provisoire-E2E-2026');
    await request.post('/api/v1/me/password', {
      headers: bearer(first),
      data: { current_password: 'Provisoire-E2E-2026', new_password: PASSWORD_MEMBER },
    });
    const limited = bearer(await apiToken(request, email, PASSWORD_MEMBER));
    expect(
      (await request.get(`/api/v1/inventories/${draft.id}`, { headers: limited })).status(),
    ).toBe(404);
    const forbidden = await request.post('/api/v1/inventories', {
      headers: limited,
      data: { site_id: s.site.id, inventory_type: 'TARGETED', article_ids: [s.articleId] },
    });
    expect(forbidden.status()).toBe(403);
  });

  test('plan STANDARD : inventaires disponibles', async ({ page, request }) => {
    await loginUi(page, STANDARD_OWNER.email, STANDARD_OWNER.password, STANDARD_OWNER.tenant);
    await page
      .getByRole('navigation', { name: 'Menu' })
      .getByRole('link', { name: 'Inventaires', exact: true })
      .click();
    await expect(page.getByRole('heading', { name: 'Inventaires' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Nouvel inventaire' }).first()).toBeVisible();
    const s = await setup(request, STANDARD_OWNER);
    const draft = await draftByApi(request, s);
    await page.goto(`/inventories/${draft.id}`);
    await expect(page.getByRole('button', { name: 'Démarrer le comptage' })).toBeVisible();
    // Nettoyage : l'article redevient disponible pour d'autres inventaires.
    await request.post(`/api/v1/inventories/${draft.id}/cancel`, {
      headers: bearer(s.token),
      data: { reason: 'Nettoyage E2E' },
    });
  });

  test('affichage mobile sans débordement, saisie utilisable @mobile', async ({
    page,
    request,
  }) => {
    const s = await setup(request);
    const draft = await draftByApi(request, s);
    expect(
      (
        await request.post(`/api/v1/inventories/${draft.id}/start`, { headers: bearer(s.token) })
      ).status(),
    ).toBe(200);
    await loginUi(page, OWNER.email, OWNER.password);
    const overflow = () =>
      page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    await page.goto('/inventories');
    await expect(page.getByRole('heading', { name: 'Inventaires' })).toBeVisible();
    expect(await overflow()).toBe(false);
    await page.goto(`/inventories/${draft.id}`);
    const input = page.getByLabel(`Quantité physique de ${s.reference}`);
    // Saisie du comptage utilisable sans défilement horizontal du tableau.
    await expect(input).toBeInViewport();
    const tableFits = await page
      .locator('.sm-count-table .p-datatable-wrapper')
      .evaluate((el) => el.scrollWidth <= el.clientWidth + 1);
    expect(tableFits).toBe(true);
    await input.fill('20');
    await input.press('Enter');
    await expect(page.getByText('Articles comptés : 1 / 1')).toBeVisible();
    expect(await overflow()).toBe(false);
    await page.goto('/inventories/new');
    await expect(page.getByRole('heading', { name: 'Nouvel inventaire' })).toBeVisible();
    expect(await overflow()).toBe(false);
    await request.post(`/api/v1/inventories/${draft.id}/cancel`, {
      headers: bearer(s.token),
      data: { reason: 'Nettoyage E2E' },
    });
  });
});
