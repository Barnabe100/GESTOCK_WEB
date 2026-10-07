import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import {
  assortArticles,
  bearer,
  createActiveSite,
  loginUi,
  provisionTenant,
  tokenFor,
} from './support';

/**
 * Lot 3-C (ADR-0041), sur une entreprise créée pour l'exécution : opérations de stock dans
 * une présentation (unité de base ou conditionnement actif de l'article) — entrée, sortie,
 * transfert et inventaire saisis par l'interface —, équivalences affichées (« 48 bouteille =
 * 8 Pack 6 = 2 Carton 24 », « 10 Carton 24 = 240 bouteille »), stock TOUJOURS en unité de
 * base, présentation conservée dans le journal (« -3 Carton 24 → -72 bouteille »),
 * comptage 8 cartons + 5 bouteilles = 197, conditionnement désactivé (plus proposé, refusé
 * à la validation), quantité décimale refusée, isolation des entreprises.
 */

const RUN = Date.now().toString().slice(-7);
const TENANT = `Stock conditionné E2E ${RUN}`;
const EMAIL = `stock-cond-${RUN}@example.com`;
const PASSWORD = 'E2e-Stock-Conditionne-2026';
const OTHER_TENANT = `Autre stock E2E ${RUN}`;
const OTHER_EMAIL = `autre-stock-cond-${RUN}@example.com`;
const REFERENCE = `COCA3C-${RUN}`;
const DEPOT = { name: `Dépôt 3C ${RUN}`, code: `D3C${RUN.slice(-4)}` };

interface Site {
  id: string;
  name: string;
}

interface World {
  token: string;
  main: Site;
  depot: Site;
  coca: string;
  pack: string;
  carton: string;
}

let world: World;

async function post<T = { id: string }>(
  request: APIRequestContext,
  path: string,
  data: unknown,
  token = world.token,
): Promise<T> {
  const response = await request.post(`/api/v1${path}`, { headers: bearer(token), data });
  expect(response.status(), await response.text()).toBeLessThan(300);
  return (await response.json()) as T;
}

async function level(request: APIRequestContext, site: Site): Promise<string> {
  const page = (await (
    await request.get(`/api/v1/stock/levels?search=${REFERENCE}&site_id=${site.id}`, {
      headers: bearer(world.token),
    })
  ).json()) as { items: { quantity: string }[] };
  return page.items[0]?.quantity ?? 'none';
}

async function choose(page: Page, inputId: string, label: string | RegExp) {
  await page.locator('.p-dropdown', { has: page.locator(`#${inputId}`) }).click();
  await page.locator('.p-dropdown-panel').last().getByRole('option', { name: label }).click();
}

async function pickArticle(page: Page) {
  await page.getByRole('button', { name: 'Ajouter une ligne' }).click();
  await page.locator('#line-0-article').fill(REFERENCE);
  await page
    .getByRole('option', { name: new RegExp(REFERENCE) })
    .first()
    .click();
}

async function selectMainSite(page: Page, inputId: string) {
  if (await page.locator(`#${inputId}`).isVisible()) await choose(page, inputId, world.main.name);
}

test.describe.configure({ mode: 'serial' });

test.beforeAll(async ({ request }) => {
  await provisionTenant(request, {
    name: TENANT,
    profile: 'retail.alimentation',
    email: EMAIL,
    password: PASSWORD,
  });
  const token = await tokenFor(request, EMAIL, PASSWORD, TENANT);
  const sites = (await (
    await request.get('/api/v1/sites', { headers: bearer(token) })
  ).json()) as (Site & { code: string })[];
  world = { token, main: sites[0] as Site } as World;
  world.depot =
    sites.find((s) => s.code === DEPOT.code) ??
    (await createActiveSite<Site>(request, token, { ...DEPOT, kind: 'warehouse' }));
  const category = (await post(request, '/catalog/categories', { name: `Boissons 3C ${RUN}` })).id;
  world.coca = (
    await post(request, '/catalog/articles', {
      reference: REFERENCE,
      designation: `Coca 3C ${RUN}`,
      category_id: category,
      unit: 'bouteille',
      sale_price: '500',
    })
  ).id;
  // Assortiment (ADR-0046) : articles ajoutés EXPLICITEMENT aux sites qui les proposent.
  await assortArticles(request, token, [world.coca]);
  // Conditionnements sans prix : la présentation des opérations de stock ne dépend pas du prix.
  world.pack = (
    await post(request, `/catalog/articles/${world.coca}/packagings`, {
      name: 'Pack 6',
      conversion: '6',
    })
  ).id;
  world.carton = (
    await post(request, `/catalog/articles/${world.coca}/packagings`, {
      name: 'Carton 24',
      conversion: '24',
    })
  ).id;
});

test.describe('Stock conditionné — Lot 3-C', () => {
  test('entrée : équivalences affichées, 10 cartons = 240 bouteilles en stock', async ({
    page,
    request,
  }) => {
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto('/stock/entries/new');
    await expect(page.getByRole('heading', { name: 'Nouvelle entrée' })).toBeVisible();
    await selectMainSite(page, 'doc-site');
    await choose(page, 'doc-kind', 'Stock initial');
    await pickArticle(page);

    // Unité de base : équivalences dans les conditionnements configurés.
    await page.locator('#line-0-quantity').fill('48');
    await expect(page.getByTestId('presentation-equivalence')).toHaveText(
      '48 bouteille = 8 Pack 6 = 2 Carton 24',
    );
    await page.locator('#line-0-quantity').fill('50');
    await expect(page.getByTestId('presentation-equivalence')).toHaveText(
      '50 bouteille = 8 Pack 6 + 2 bouteille = 2 Carton 24 + 2 bouteille',
    );

    // Conditionnement : quantité et coût saisis par carton.
    await choose(page, 'line-0-packaging', /^Carton 24/);
    await page.locator('#line-0-quantity').fill('10');
    await expect(page.getByTestId('presentation-equivalence')).toHaveText(
      '10 Carton 24 = 240 bouteille',
    );
    await expect(page.getByText('Coût unitaire (par Carton 24)')).toBeVisible();
    await page.locator('#line-0-cost').fill('12000');
    await page.getByRole('button', { name: 'Valider', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Valider' }).click();
    await expect(page.getByText(/Document ENT-\d{6} validé/)).toBeVisible();
    await expect(page.getByText('10 Carton 24 = 240 bouteille')).toBeVisible();

    // Stock en unité de base, CMUP par bouteille (12 000 / 24 = 500).
    const levels = (await (
      await request.get(`/api/v1/stock/levels?search=${REFERENCE}&site_id=${world.main.id}`, {
        headers: bearer(world.token),
      })
    ).json()) as { items: { quantity: string; average_cost: string }[] };
    expect([levels.items[0]?.quantity, levels.items[0]?.average_cost]).toEqual([
      '240.000',
      '500.0000',
    ]);
  });

  test('sortie en cartons : 3 cartons → 72 bouteilles, présentation dans le journal', async ({
    page,
    request,
  }) => {
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto('/stock/exits/new');
    await expect(page.getByRole('heading', { name: 'Nouvelle sortie' })).toBeVisible();
    await selectMainSite(page, 'doc-site');
    await choose(page, 'doc-reason', 'Casse');
    await pickArticle(page);
    await choose(page, 'line-0-packaging', /^Carton 24/);
    await page.locator('#line-0-quantity').fill('3');
    await expect(page.getByTestId('presentation-equivalence')).toHaveText(
      '3 Carton 24 = 72 bouteille',
    );
    await page.getByRole('button', { name: 'Valider', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Valider' }).click();
    const validated = page.getByText(/Document SOR-\d{6} validé/);
    await expect(validated).toBeVisible();
    const number = ((await validated.innerText()).match(/SOR-\d{6}/) ?? [''])[0];
    expect(await level(request, world.main)).toBe('168.000');

    // Journal des mouvements : présentation saisie et quantité de base.
    await page.goto(`/stock/movements?search=${number}`);
    const row = page.getByRole('row').filter({ hasText: number });
    await expect(row).toContainText('-3 Carton 24');
    await expect(row).toContainText('-72 bouteille');
  });

  test('transfert en cartons : 2 cartons quittent le site, 48 bouteilles arrivent', async ({
    page,
    request,
  }) => {
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto('/stock/transfers/new');
    await expect(page.getByRole('heading', { name: 'Nouveau transfert' })).toBeVisible();
    await choose(page, 'transfer-source', world.main.name);
    await choose(page, 'transfer-destination', world.depot.name);
    await pickArticle(page);
    await choose(page, 'line-0-packaging', /^Carton 24/);
    await page.locator('#line-0-quantity').fill('2');
    await expect(page.getByTestId('presentation-equivalence')).toHaveText(
      '2 Carton 24 = 48 bouteille',
    );
    await expect(page.getByTestId('available-0')).toHaveText(/Stock disponible : 168 bouteille/);
    await page.getByRole('button', { name: 'Enregistrer le brouillon' }).click();
    await expect(page.getByText('Brouillon enregistré')).toBeVisible();
    await page.getByRole('button', { name: 'Valider le transfert' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Valider le transfert' }).click();
    await expect(page.getByText(/Transfert TRF-\d{6} validé/)).toBeVisible();
    expect(await level(request, world.main)).toBe('120.000');
    expect(await level(request, world.depot)).toBe('48.000');
  });

  test('inventaire : 8 cartons + 5 bouteilles = 197 comptés', async ({ page, request }) => {
    const draft = await post(request, '/inventories', {
      site_id: world.main.id,
      inventory_type: 'TARGETED',
      article_ids: [world.coca],
    });
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto(`/inventories/${draft.id}`);
    await page.getByRole('button', { name: 'Démarrer le comptage' }).click();
    await expect(page.getByText('Comptage en cours', { exact: true })).toBeVisible();
    await page
      .locator('.p-dropdown', { has: page.getByLabel(`Présentation du comptage de ${REFERENCE}`) })
      .click();
    await page
      .locator('.p-dropdown-panel')
      .last()
      .getByRole('option', { name: /^Carton 24/ })
      .click();
    await page.getByLabel(`Nombre de Carton 24 comptés pour ${REFERENCE}`).fill('8');
    const loose = page.getByLabel(`bouteille en vrac pour ${REFERENCE}`);
    await loose.fill('5');
    await expect(page.getByTestId(/^count-equivalence-/)).toHaveText('= 197 bouteille');
    await loose.blur();
    await expect(page.getByText('Articles comptés : 1 / 1')).toBeVisible();

    const saved = (await (
      await request.get(`/api/v1/inventories/${draft.id}/lines`, { headers: bearer(world.token) })
    ).json()) as { items: { quantity_physical: string; count_packaging_name: string }[] };
    expect([saved.items[0]?.quantity_physical, saved.items[0]?.count_packaging_name]).toEqual([
      '197.000',
      'Carton 24',
    ]);

    await page.getByRole('button', { name: 'Terminer le comptage' }).click();
    await page.getByRole('button', { name: "Valider l'inventaire" }).click();
    await page.getByRole('dialog').getByRole('button', { name: "Valider l'inventaire" }).click();
    await expect(page.getByText(/Inventaire INV-\d{6} validé/)).toBeVisible();
    // Comptage affiché dans sa présentation ; stock ajusté en unité de base (120 → 197).
    await expect(page.getByText('8 Carton 24 + 5 bouteille = 197 bouteille')).toBeVisible();
    expect(await level(request, world.main)).toBe('197.000');
  });

  test('conditionnement désactivé : plus proposé, brouillon refusé à la validation', async ({
    page,
    request,
  }) => {
    const draft = await post(request, '/stock/entries', {
      site_id: world.main.id,
      kind: 'INITIAL_STOCK',
      lines: [
        { article_id: world.coca, packaging_id: world.pack, quantity: '2', unit_cost: '3000' },
      ],
    });
    await post(request, `/catalog/packagings/${world.pack}/deactivate`, {});

    await loginUi(page, EMAIL, PASSWORD, TENANT);
    // Le brouillon garde sa présentation, mais la validation est refusée par le serveur.
    await page.goto(`/stock/entries/${draft.id}`);
    await page.getByRole('button', { name: 'Valider', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Valider' }).click();
    await expect(page.getByText(/Ce conditionnement est désactivé/)).toBeVisible();
    expect(await level(request, world.main)).toBe('197.000');

    // Nouvelle saisie : le conditionnement désactivé n'est plus proposé ni dans l'équivalence.
    await page.goto('/stock/entries/new');
    await selectMainSite(page, 'doc-site');
    await pickArticle(page);
    await page.locator('.p-dropdown', { has: page.locator('#line-0-packaging') }).click();
    const panel = page.locator('.p-dropdown-panel').last();
    await expect(panel.getByRole('option', { name: /^Carton 24/ })).toBeVisible();
    await expect(panel.getByRole('option', { name: /^Pack 6/ })).toHaveCount(0);
    await page.keyboard.press('Escape');
    await page.locator('#line-0-quantity').fill('48');
    await expect(page.getByTestId('presentation-equivalence')).toHaveText(
      '48 bouteille = 2 Carton 24',
    );

    // L'API refuse aussi une nouvelle opération dans ce conditionnement.
    const refused = await request.post('/api/v1/stock/entries', {
      headers: bearer(world.token),
      data: {
        site_id: world.main.id,
        kind: 'INITIAL_STOCK',
        lines: [
          { article_id: world.coca, packaging_id: world.pack, quantity: '1', unit_cost: '500' },
        ],
      },
    });
    expect(refused.status()).toBe(422);
    expect(((await refused.json()) as { code: string }).code).toBe('packaging_inactive');
  });

  test('quantité décimale refusée pour un article géré en entiers', async ({ request }) => {
    for (const line of [
      { article_id: world.coca, quantity: '1.5', unit_cost: '500' },
      { article_id: world.coca, packaging_id: world.carton, quantity: '0.5', unit_cost: '500' },
    ]) {
      const response = await request.post('/api/v1/stock/entries', {
        headers: bearer(world.token),
        data: { site_id: world.main.id, kind: 'INITIAL_STOCK', lines: [line] },
      });
      expect(response.status()).toBe(422);
      expect(((await response.json()) as { code: string }).code).toBe('quantity_not_whole');
    }
  });

  test("isolation : le conditionnement d'une autre entreprise est introuvable", async ({
    request,
  }) => {
    await provisionTenant(request, {
      name: OTHER_TENANT,
      profile: 'retail.alimentation',
      email: OTHER_EMAIL,
      password: PASSWORD,
    });
    const other = await tokenFor(request, OTHER_EMAIL, PASSWORD, OTHER_TENANT);
    const sites = (await (
      await request.get('/api/v1/sites', { headers: bearer(other) })
    ).json()) as Site[];
    const category = (
      await post(request, '/catalog/categories', { name: `Autre 3C ${RUN}` }, other)
    ).id;
    const article = (
      await post(
        request,
        '/catalog/articles',
        {
          reference: `AUTRE3C-${RUN}`,
          designation: `Autre 3C ${RUN}`,
          category_id: category,
          unit: 'pièce',
          sale_price: '100',
        },
        other,
      )
    ).id;
    // Son propre article est proposé par son site (ADR-0046) : seul le conditionnement d'une
    // autre entreprise est en cause.
    await assortArticles(request, other, [article], [sites[0]?.id ?? '']);
    const response = await request.post('/api/v1/stock/entries', {
      headers: bearer(other),
      data: {
        site_id: sites[0]?.id,
        kind: 'INITIAL_STOCK',
        lines: [
          { article_id: article, packaging_id: world.carton, quantity: '1', unit_cost: '100' },
        ],
      },
    });
    expect(response.status()).toBe(422);
    expect(((await response.json()) as { code: string }).code).toBe('packaging_not_found');
    const packagings = await request.get(`/api/v1/catalog/articles/${world.coca}/packagings`, {
      headers: bearer(other),
    });
    expect(packagings.status()).toBe(404);
  });
});
