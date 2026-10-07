import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import {
  apiToken,
  assortArticles,
  bearer,
  createActiveSite,
  createMember,
  loginUi,
  OWNER,
  tokenFor,
} from './support';

/**
 * Recette, étape 1 — assortiment par site (ADR-0046), validation globale (palier 5) avec le vrai
 * backend. CATALOGUE TENANT ≠ ASSORTIMENT SITE ≠ STOCK SITE : un article du catalogue n'est
 * proposé par un site qu'après un ajout EXPLICITE ; toute opération sur un article hors
 * assortiment est refusée (`422 article_not_in_site_assortment`) ; le retrait est encadré
 * (stock, documents ouverts) et l'historique conservé. Chaque test crée ses propres articles
 * (suffixe unique) ; le site « Dépôt E2E » est créé au besoin.
 */

const DEPOT = { name: 'Dépôt E2E', code: 'DEPOT-E2E' };
const NOT_IN = 'article_not_in_site_assortment';

interface Site {
  id: string;
  name: string;
  code: string;
}

interface World {
  token: string;
  suffix: string;
  shop: Site;
  depot: Site;
  categoryId: string;
}

async function call(
  request: APIRequestContext,
  token: string,
  method: 'get' | 'post' | 'put' | 'patch',
  path: string,
  data?: unknown,
) {
  const response = await request[method](`/api/v1${path}`, {
    headers: bearer(token),
    ...(data === undefined ? {} : { data }),
  });
  const text = await response.text();
  return { status: response.status(), body: (text ? JSON.parse(text) : null) as never };
}

async function ok<T = { id: string }>(
  request: APIRequestContext,
  token: string,
  method: 'get' | 'post' | 'put' | 'patch',
  path: string,
  data?: unknown,
): Promise<T> {
  const response = await call(request, token, method, path, data);
  expect(response.status, JSON.stringify(response.body)).toBeLessThan(300);
  return response.body as T;
}

async function refused(
  request: APIRequestContext,
  token: string,
  method: 'post' | 'put',
  path: string,
  data: unknown,
) {
  const response = await call(request, token, method, path, data);
  expect(response.status, JSON.stringify(response.body)).toBe(422);
  expect((response.body as { code: string }).code).toBe(NOT_IN);
}

async function world(request: APIRequestContext): Promise<World> {
  const token = await apiToken(request, OWNER.email, OWNER.password);
  const sites = await ok<Site[]>(request, token, 'get', '/sites');
  const shop = sites.find((s) => s.code === 'PRINCIPAL') ?? (sites[0] as Site);
  const depot =
    sites.find((s) => s.code === DEPOT.code) ??
    (await createActiveSite<Site>(request, token, { ...DEPOT, kind: 'warehouse' }));
  const suffix = Date.now().toString().slice(-7);
  const category = await ok(request, token, 'post', '/catalog/categories', {
    name: `Assortiment E2E ${suffix}`,
  });
  return { token, suffix, shop, depot, categoryId: category.id };
}

/** Article du catalogue SEULEMENT (aucun site) : le cas que la recette a révélé. */
async function catalogOnly(
  request: APIRequestContext,
  w: World,
  reference: string,
  extra: Record<string, unknown> = {},
) {
  return ok(request, w.token, 'post', '/catalog/articles', {
    reference,
    designation: `Assortiment ${reference}`,
    category_id: w.categoryId,
    unit: 'u',
    purchase_price: '1000',
    sale_price: '1500',
    ...extra,
  });
}

async function receive(
  request: APIRequestContext,
  w: World,
  siteId: string,
  articleId: string,
  quantity: string,
) {
  const entry = await ok(request, w.token, 'post', '/stock/entries', {
    site_id: siteId,
    kind: 'INITIAL_STOCK',
    lines: [{ article_id: articleId, quantity, unit_cost: '1000' }],
  });
  await ok(request, w.token, 'post', `/stock/entries/${entry.id}/validate`);
}

async function levelOf(request: APIRequestContext, w: World, siteId: string, articleId: string) {
  const page = await ok<{ items: { quantity: string; in_assortment: boolean }[] }>(
    request,
    w.token,
    'get',
    `/stock/levels?site_id=${siteId}&article_id=${articleId}&include_inactive=true`,
  );
  return page.items[0];
}

async function sitesOf(request: APIRequestContext, w: World, articleId: string) {
  const sites = await ok<{ site_id: string; state: string }[]>(
    request,
    w.token,
    'get',
    `/catalog/articles/${articleId}/sites`,
  );
  return new Map(sites.map((s) => [s.site_id, s.state]));
}

async function openAssortment(page: Page) {
  const menu = page.getByRole('navigation', { name: 'Menu' });
  await menu.getByRole('link', { name: 'Assortiment des sites', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Assortiment des sites' })).toBeVisible();
}

/** Ligne de la page Assortiment pour une référence (recherche serveur). */
async function assortmentRow(page: Page, reference: string) {
  await page.getByRole('searchbox').first().fill(reference);
  const row = page.getByRole('row').filter({ hasText: reference });
  await expect(row).toBeVisible();
  return row;
}

async function confirmRemoval(page: Page) {
  const confirm = page.getByRole('dialog').filter({ hasText: "Retrait de l'assortiment" });
  await confirm.getByRole('button', { name: "Retirer de l'assortiment" }).click();
}

test.describe('Assortiment par site — Recette, étape 1', () => {
  test('catalogue ≠ assortiment : aucun ajout automatique, refus serveur, ajout explicite, menu et site principal', async ({
    page,
    request,
  }) => {
    const w = await world(request);
    const reference = `E2E-AS${w.suffix}`;
    const article = await catalogOnly(request, w, reference);

    // Un nouvel article n'est proposé par aucun site.
    expect([...(await sitesOf(request, w, article.id)).values()]).toEqual(
      expect.arrayContaining(['none']),
    );
    expect((await sitesOf(request, w, article.id)).get(w.shop.id)).toBe('none');
    // Toute opération est refusée, sans ajout automatique à l'assortiment.
    await refused(request, w.token, 'post', '/stock/entries', {
      site_id: w.shop.id,
      kind: 'INITIAL_STOCK',
      lines: [{ article_id: article.id, quantity: '5', unit_cost: '1000' }],
    });
    await refused(request, w.token, 'post', '/sales', {
      site_id: w.shop.id,
      lines: [{ article_id: article.id, quantity: '1' }],
    });
    await refused(request, w.token, 'put', `/stock/levels/${w.shop.id}/${article.id}/thresholds`, {
      min_stock: '2',
    });
    expect((await sitesOf(request, w, article.id)).get(w.shop.id)).toBe('none');
    // Ni niveau de stock, ni caisse : le site ne présente pas l'article.
    expect(await levelOf(request, w, w.shop.id, article.id)).toBeUndefined();
    const pos = await ok<unknown[]>(
      request,
      w.token,
      'get',
      `/pos/articles?site_id=${w.shop.id}&search=${reference}`,
    );
    expect(pos).toEqual([]);

    // Site principal calculé par le serveur : le plus ancien site actif accessible.
    const caps = await ok<{ main_site_id: string }>(request, w.token, 'get', '/me/capabilities');
    expect(caps.main_site_id).toBe(w.shop.id);

    await loginUi(page, OWNER.email, OWNER.password);
    // Menu : Articles → Assortiment des sites → Catégories.
    const menu = page.getByRole('navigation', { name: 'Menu' });
    const links = await menu.getByRole('link').allTextContents();
    const at = (label: string) => links.findIndex((l) => l.trim() === label);
    expect(at('Articles')).toBeGreaterThanOrEqual(0);
    expect(at('Assortiment des sites')).toBe(at('Articles') + 1);
    expect(at('Catégories')).toBe(at('Articles') + 2);

    // Page Assortiment : site principal par défaut, ajout explicite depuis le catalogue.
    await openAssortment(page);
    await expect(page.locator('.sm-filterbar .p-dropdown').first()).toContainText(w.shop.name);
    await page.getByRole('button', { name: 'Ajouter des articles' }).first().click();
    const dialog = page.getByRole('dialog').filter({ hasText: "Ajouter à l'assortiment" });
    await dialog.getByRole('searchbox').fill(reference);
    const candidate = dialog.getByRole('row').filter({ hasText: reference });
    await expect(candidate.getByText('Non proposé')).toBeVisible();
    await candidate.locator('input.p-checkbox-input').check();
    await dialog.getByRole('button', { name: 'Ajouter 1 article' }).click();
    await expect(dialog).toBeHidden();
    const row = await assortmentRow(page, reference);
    await expect(row.getByText('Proposé', { exact: true })).toBeVisible();

    // Désormais proposé par la boutique seulement : réception acceptée, dépôt toujours refusé.
    expect((await sitesOf(request, w, article.id)).get(w.shop.id)).toBe('active');
    expect((await sitesOf(request, w, article.id)).get(w.depot.id)).toBe('none');
    await receive(request, w, w.shop.id, article.id, '5');
    expect(await levelOf(request, w, w.shop.id, article.id)).toMatchObject({
      quantity: '5.000',
      in_assortment: true,
    });
    await refused(request, w.token, 'post', '/stock/entries', {
      site_id: w.depot.id,
      kind: 'INITIAL_STOCK',
      lines: [{ article_id: article.id, quantity: '5', unit_cost: '1000' }],
    });
  });

  test('retrait encadré (stock, document ouvert), historique conservé, stock hors assortiment, réactivation', async ({
    page,
    request,
  }) => {
    const w = await world(request);
    const reference = `E2E-AR${w.suffix}`;
    const article = await catalogOnly(request, w, reference);
    await assortArticles(request, w.token, [article.id], [w.shop.id]);
    await receive(request, w, w.shop.id, article.id, '4');

    await loginUi(page, OWNER.email, OWNER.password);
    await openAssortment(page);
    // 1. Du stock reste sur le site : retrait refusé, raison détaillée (jamais le message
    //    générique du changement de suivi).
    let row = await assortmentRow(page, reference);
    await row.getByRole('button', { name: "Retirer de l'assortiment" }).click();
    await confirmRemoval(page);
    let blocked = page.getByRole('alert').filter({ hasText: reference });
    await expect(blocked).toContainText('du stock (ou un solde de lot) reste sur ce site');
    await page.getByRole('dialog').getByRole('button', { name: 'Fermer' }).last().click();

    // 2. Stock ramené à zéro, mais un brouillon de sortie du site contient l'article.
    const reasons = await ok<{ items: { id: string; code: string | null }[] }>(
      request,
      w.token,
      'get',
      '/stock/exit-reasons?limit=50',
    );
    const reason = reasons.items[0]?.id;
    const out = await ok<{ id: string; number: string }>(request, w.token, 'post', '/stock/exits', {
      site_id: w.shop.id,
      reason_id: reason,
      lines: [{ article_id: article.id, quantity: '4' }],
    });
    await ok(request, w.token, 'post', `/stock/exits/${out.id}/validate`);
    const draft = await ok<{ id: string; number: string }>(
      request,
      w.token,
      'post',
      '/stock/exits',
      {
        site_id: w.shop.id,
        reason_id: reason,
        lines: [{ article_id: article.id, quantity: '1' }],
      },
    );
    row = await assortmentRow(page, reference);
    await row.getByRole('button', { name: "Retirer de l'assortiment" }).click();
    await confirmRemoval(page);
    blocked = page.getByRole('alert').filter({ hasText: reference });
    await expect(blocked).toContainText(draft.number);
    await page.getByRole('dialog').getByRole('button', { name: 'Fermer' }).last().click();

    // 3. Ligne retirée du brouillon (un brouillon ne s'annule pas, il se modifie) : retrait
    //    accepté ; l'article passe dans « Retirés ».
    await ok(request, w.token, 'put', `/stock/exits/${draft.id}`, { reason_id: reason, lines: [] });
    row = await assortmentRow(page, reference);
    await row.getByRole('button', { name: "Retirer de l'assortiment" }).click();
    await confirmRemoval(page);
    await expect(page.getByRole('row').filter({ hasText: reference })).toBeHidden();
    await page
      .getByRole('radio', { name: 'Retirés' })
      .or(page.getByText('Retirés', { exact: true }))
      .first()
      .click();
    row = page.getByRole('row').filter({ hasText: reference });
    await expect(row.getByText('Retiré', { exact: true })).toBeVisible();
    await expect(row.getByText(/Retiré le/)).toBeVisible();

    // Historique conservé : mouvements intacts ; plus présenté par le site (stock nul).
    const movements = await ok<{ total: number }>(
      request,
      w.token,
      'get',
      `/stock/movements?site_id=${w.shop.id}&search=${reference}`,
    );
    expect(movements.total).toBeGreaterThanOrEqual(2);
    expect(await levelOf(request, w, w.shop.id, article.id)).toBeUndefined();
    expect((await sitesOf(request, w, article.id)).get(w.shop.id)).toBe('removed');

    // Annulation de la sortie validée : permise, stock restauré « Hors assortiment ».
    await ok(request, w.token, 'post', `/stock/exits/${out.id}/cancel`, {
      reason: 'Erreur de saisie',
    });
    expect(await levelOf(request, w, w.shop.id, article.id)).toMatchObject({
      quantity: '4.000',
      in_assortment: false,
    });
    // Inutilisable sans réactivation ; lots et stock restent consultables.
    await refused(request, w.token, 'post', '/stock/exits', {
      site_id: w.shop.id,
      reason_id: reason,
      lines: [{ article_id: article.id, quantity: '1' }],
    });
    await page.goto('/stock/levels');
    await page.getByRole('searchbox').first().fill(reference);
    const level = page.getByRole('row').filter({ hasText: reference });
    await expect(level.getByText('Hors assortiment')).toBeVisible();
    await expect(level.getByRole('button', { name: 'Seuils du site' })).toHaveCount(0);

    // Réactivation : même ligne, de nouveau proposé et utilisable.
    await openAssortment(page);
    await page.getByText('Retirés', { exact: true }).first().click();
    row = await assortmentRow(page, reference);
    await row.getByRole('button', { name: 'Réactiver' }).click();
    await page.getByText('Proposés', { exact: true }).first().click();
    row = await assortmentRow(page, reference);
    await expect(row.getByText('Proposé', { exact: true })).toBeVisible();
    expect(await levelOf(request, w, w.shop.id, article.id)).toMatchObject({ in_assortment: true });
    await ok(request, w.token, 'put', `/stock/levels/${w.shop.id}/${article.id}/thresholds`, {
      min_stock: '2',
    });
  });

  test('multi-sites : transfert source ET destination, isolation, ajout explicite depuis le document', async ({
    page,
    request,
  }) => {
    const w = await world(request);
    const reference = `E2E-AT${w.suffix}`;
    const article = await catalogOnly(request, w, reference);
    await assortArticles(request, w.token, [article.id], [w.shop.id]);
    await receive(request, w, w.shop.id, article.id, '10');
    const transfer = {
      source_site_id: w.shop.id,
      destination_site_id: w.depot.id,
      lines: [{ article_id: article.id, quantity: '4' }],
    };
    // Destination hors assortiment : refus ; le dépôt ne présente pas l'article.
    await refused(request, w.token, 'post', '/stock/transfers', transfer);
    expect(await levelOf(request, w, w.depot.id, article.id)).toBeUndefined();
    const depotPos = await ok<unknown[]>(
      request,
      w.token,
      'get',
      `/pos/articles?site_id=${w.depot.id}&search=${reference}`,
    );
    expect(depotPos).toEqual([]);

    // Interface : le transfert signale le dépôt et propose l'ajout explicite.
    await loginUi(page, OWNER.email, OWNER.password);
    await page.goto('/stock/transfers/new');
    await expect(page.getByRole('heading', { name: 'Nouveau transfert' })).toBeVisible();
    await page.locator('.p-dropdown', { has: page.locator('#transfer-destination') }).click();
    await page
      .locator('.p-dropdown-panel')
      .last()
      .locator('.p-dropdown-item', { hasText: w.depot.name })
      .click();
    const input = page.locator('#line-0-article');
    if (!(await input.isVisible()))
      await page.getByRole('button', { name: 'Ajouter une ligne' }).click();
    await page.locator('#line-0-article').fill(reference);
    await page
      .getByRole('option', { name: new RegExp(reference) })
      .first()
      .click();
    const notice = page.getByText(`pas proposé par le site « ${w.depot.name} »`);
    await expect(notice).toBeVisible();
    await page.getByRole('button', { name: "Ajouter à l'assortiment du site" }).click();
    await expect(notice).toBeHidden();

    // Le dépôt propose maintenant l'article : transfert accepté, stock des deux sites.
    expect((await sitesOf(request, w, article.id)).get(w.depot.id)).toBe('active');
    const created = await ok(request, w.token, 'post', '/stock/transfers', transfer);
    await ok(request, w.token, 'post', `/stock/transfers/${created.id}/validate`);
    expect((await levelOf(request, w, w.shop.id, article.id))?.quantity).toBe('6.000');
    expect((await levelOf(request, w, w.depot.id, article.id))?.quantity).toBe('4.000');
  });

  test('ventes, POS, inventaires, article non géré en stock et droits', async ({ request }) => {
    const w = await world(request);
    const sold = await catalogOnly(request, w, `E2E-AV${w.suffix}`);
    const outside = await catalogOnly(request, w, `E2E-AH${w.suffix}`);
    const service = await catalogOnly(request, w, `E2E-AX${w.suffix}`, { stock_managed: false });
    const never = await catalogOnly(request, w, `E2E-AN${w.suffix}`);
    await assortArticles(request, w.token, [sold.id, service.id, never.id], [w.shop.id]);
    await receive(request, w, w.shop.id, sold.id, '10');

    // POS : seul l'assortiment est proposé ; hors assortiment refusé à l'encaissement.
    const pos = await ok<{ reference: string }[]>(
      request,
      w.token,
      'get',
      `/pos/articles?site_id=${w.shop.id}&search=${w.suffix}`,
    );
    const offered = pos.map((a) => a.reference);
    expect(offered).toContain(`E2E-AV${w.suffix}`);
    expect(offered).not.toContain(`E2E-AH${w.suffix}`);
    const checkout = (articleId: string) =>
      call(request, w.token, 'post', '/pos/checkout', {
        site_id: w.shop.id,
        lines: [{ article_id: articleId, quantity: '1' }],
        payments: [],
        idempotency_key: crypto.randomUUID(),
      });
    const refusedCheckout = await checkout(outside.id);
    expect(refusedCheckout.status).toBe(422);
    expect((refusedCheckout.body as { code: string }).code).toBe(NOT_IN);

    // Article non géré en stock de l'assortiment : vendable (aucun mouvement de stock).
    const draft = await ok(request, w.token, 'post', '/sales', {
      site_id: w.shop.id,
      lines: [
        { article_id: sold.id, quantity: '1' },
        { article_id: service.id, quantity: '1' },
      ],
    });
    expect(draft.id).toBeTruthy();
    await refused(request, w.token, 'post', '/sales', {
      site_id: w.shop.id,
      lines: [{ article_id: outside.id, quantity: '1' }],
    });

    // Inventaires : candidats = assortiment ; article jamais reçu proposé ; hors assortiment refusé.
    const candidates = await ok<{ items: { reference: string; quantity: string }[] }>(
      request,
      w.token,
      'get',
      `/inventories/candidates?site_id=${w.shop.id}&search=${w.suffix}&limit=100`,
    );
    const refs = new Map(candidates.items.map((c) => [c.reference, c.quantity]));
    expect(refs.get(`E2E-AN${w.suffix}`)).toBe('0.000');
    expect(refs.has(`E2E-AH${w.suffix}`)).toBe(false);
    await refused(request, w.token, 'post', '/inventories', {
      site_id: w.shop.id,
      inventory_type: 'TARGETED',
      article_ids: [outside.id],
    });

    // Droits : le Vendeur ne compose pas l'assortiment (permission contrôlée par le serveur).
    const sellerEmail = await createMember(request, w.token, 'seller', 'Vendeur-Assort-2026');
    const seller = await tokenFor(request, sellerEmail, 'Vendeur-Assort-2026', OWNER.tenant);
    const denied = await call(request, seller, 'post', `/catalog/sites/${w.shop.id}/articles`, {
      article_ids: [outside.id],
    });
    expect(denied.status).toBe(403);
    expect((await sitesOf(request, w, outside.id)).get(w.shop.id)).toBe('none');
  });

  test('@mobile page Assortiment : aucun débordement, ajout depuis le catalogue', async ({
    page,
    request,
  }) => {
    const w = await world(request);
    const reference = `E2E-AM${w.suffix}`;
    await catalogOnly(request, w, reference);
    await loginUi(page, OWNER.email, OWNER.password);
    await page.goto('/catalog/assortment');
    await expect(page.getByRole('heading', { name: 'Assortiment des sites' })).toBeVisible();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
    await page.getByRole('button', { name: 'Ajouter des articles' }).first().click();
    const dialog = page.getByRole('dialog').filter({ hasText: "Ajouter à l'assortiment" });
    await dialog.getByRole('searchbox').fill(reference);
    await dialog
      .getByRole('row')
      .filter({ hasText: reference })
      .locator('input.p-checkbox-input')
      .check();
    await dialog.getByRole('button', { name: 'Ajouter 1 article' }).click();
    await expect(dialog).toBeHidden();
    await page.getByRole('searchbox').first().fill(reference);
    await expect(page.getByRole('row').filter({ hasText: reference })).toBeVisible();
  });
});
