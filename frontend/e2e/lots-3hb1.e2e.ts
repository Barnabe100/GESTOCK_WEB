import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import { bearer, createActiveSite, loginUi, ownerSql, provisionTenant, tokenFor } from './support';

/**
 * Lot 3-H-B1 (ADR-0045) — transferts inter-sites par lot, sur une entreprise créée pour
 * l'exécution : répartition manuelle sur plusieurs lots, MÊME lot sur les deux sites, soldes et
 * mouvements, annulation exacte, lot périmé non transférable, lot insuffisant, conditionnement,
 * concurrence, écran étroit.
 *
 * P1-b reste active : les articles sont marqués « suivis par lot » par le mécanisme RÉSERVÉ AUX
 * TESTS (rôle propriétaire de la base, ``ownerSql``) — l'application refuse toujours l'activation.
 */

const RUN = Date.now().toString().slice(-7);
const TENANT = `Lots HB1 E2E ${RUN}`;
const EMAIL = `lots-hb1-${RUN}@example.com`;
const PASSWORD = 'E2e-LotsHB1-2026';
const DEPOT = { name: 'Dépôt lots', code: `DEPL-${RUN.slice(-4)}` };

const day = (offset: number) => {
  // Fuseau de l'entreprise de test : Afrique/Ouagadougou (UTC).
  const date = new Date();
  date.setUTCDate(date.getUTCDate() + offset);
  return date.toISOString().slice(0, 10);
};

interface Site {
  id: string;
  name: string;
}

interface World {
  token: string;
  site: Site;
  depot: Site;
  category: string;
}

let world: World;
let counter = 0;

async function call(
  request: APIRequestContext,
  method: 'get' | 'post' | 'put',
  path: string,
  data?: unknown,
) {
  return request[method](`/api/v1${path}`, { headers: bearer(world.token), data });
}

async function api<T = { id: string }>(
  request: APIRequestContext,
  method: 'get' | 'post' | 'put',
  path: string,
  data?: unknown,
): Promise<T> {
  const response = await call(request, method, path, data);
  expect(response.status(), await response.text()).toBeLessThan(300);
  return (await response.json()) as T;
}

interface Article {
  id: string;
  reference: string;
  designation: string;
  suffix: string;
}

/** Article suivi par lot (préparation RÉSERVÉE AUX TESTS) : une réception par lot sur le site
 *  principal, `[numéro, quantité, décalage de péremption en jours]`. */
async function trackedArticle(
  request: APIRequestContext,
  lots: [string, string, number][],
): Promise<Article> {
  counter += 1;
  const suffix = `${RUN}-${counter}-${Math.floor(Math.random() * 1000)}`;
  const reference = `EAUTR-${suffix}`;
  const designation = `Eau transfert ${suffix}`;
  const article = await api(request, 'post', '/catalog/articles', {
    reference,
    designation,
    category_id: world.category,
    unit: 'bouteille',
    sale_price: '500',
  });
  ownerSql(
    `UPDATE catalog_articles SET lot_tracked = true, expiry_tracked = true WHERE id = '${article.id}'`,
  );
  for (const [number, quantity, offset] of lots) {
    const entry = await api(request, 'post', '/stock/entries', {
      site_id: world.site.id,
      kind: 'INITIAL_STOCK',
      lines: [
        {
          article_id: article.id,
          quantity,
          unit_cost: '200',
          lot_number: `${number}-${suffix}`,
          lot_expiry_date: day(offset),
        },
      ],
    });
    await api(request, 'post', `/stock/entries/${entry.id}/validate`, {});
  }
  return { id: article.id, reference, designation, suffix };
}

const lotName = (article: Article, number: string) => `${number}-${article.suffix}`;

async function lotIds(request: APIRequestContext, article: Article) {
  const page = await api<{ items: { id: string; number: string }[] }>(
    request,
    'get',
    `/stock/lots?article_id=${article.id}&limit=50`,
  );
  return Object.fromEntries(page.items.map((l) => [l.number, l.id]));
}

/** Soldes des lots de l'article sur un site. */
async function balances(request: APIRequestContext, article: Article, site: Site) {
  const page = await api<{ items: { number: string; quantity: string }[] }>(
    request,
    'get',
    `/stock/lots?article_id=${article.id}&site_id=${site.id}&limit=50`,
  );
  return Object.fromEntries(page.items.map((l) => [l.number, l.quantity]));
}

async function level(request: APIRequestContext, article: Article, site: Site) {
  const page = await api<{ items: { site_id: string; quantity: string }[] }>(
    request,
    'get',
    `/stock/levels?article_id=${article.id}&site_id=${site.id}`,
  );
  // Quantité normalisée à 3 décimales (un niveau absent ou nul vaut 0).
  return Number(page.items[0]?.quantity ?? '0').toFixed(3);
}

async function choose(page: Page, inputId: string, label: string) {
  await page.locator('.p-dropdown', { has: page.locator(`#${inputId}`) }).click();
  await page
    .locator('.p-dropdown-panel')
    .last()
    .locator('.p-dropdown-item', { hasText: label })
    .click();
}

/** Saisie d'un transfert site principal → dépôt (une ligne), jusqu'à l'éditeur de lots. */
async function enterTransfer(page: Page, article: Article, quantity: string) {
  await page.goto('/stock/transfers/new');
  await expect(page.getByRole('heading', { name: 'Nouveau transfert' })).toBeVisible();
  await choose(page, 'transfer-source', world.site.name);
  await choose(page, 'transfer-destination', DEPOT.name);
  await page.getByRole('button', { name: 'Ajouter une ligne' }).click();
  await page.locator('#line-0-article').fill(article.reference);
  await page
    .getByRole('option', { name: new RegExp(article.reference) })
    .first()
    .click();
  await page.locator('#line-0-quantity').fill(quantity);
  return page.getByRole('group', { name: 'Lots' });
}

async function confirmValidation(page: Page) {
  await page.getByRole('button', { name: 'Valider le transfert' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Valider le transfert' }).click();
}

const overflow = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);

/** Transfert par l'API (brouillon) : lignes `[article, quantité, [[lot, quantité]], conditionnement]`. */
function transferBody(lines: [Article, string, [string, string][], string?][]) {
  return {
    source_site_id: world.site.id,
    destination_site_id: world.depot.id,
    lines: lines.map(([article, quantity, lots, packaging]) => ({
      article_id: article.id,
      quantity,
      lots: lots.map(([lot_id, q]) => ({ lot_id, quantity: q })),
      ...(packaging ? { packaging_id: packaging } : {}),
    })),
  };
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
  ).json()) as Site[];
  const depot = await createActiveSite<Site>(request, token, { ...DEPOT, kind: 'warehouse' });
  world = { token, site: sites[0] as Site, depot, category: '' };
  world.category = (
    await api(request, 'post', '/catalog/categories', { name: `Boissons HB1 ${RUN}` })
  ).id;
});

test.describe('Transferts par lot — Lot 3-H-B1', () => {
  test('transfert multi-lots : 70 = A 50 + B 20, même lot des deux côtés, puis annulation exacte', async ({
    page,
    request,
  }) => {
    expect(await api(request, 'get', '/catalog/lot-tracking')).toEqual({ available: false });
    const article = await trackedArticle(request, [
      ['A', '100', 30],
      ['B', '50', 60],
    ]);
    const [a, b] = [lotName(article, 'A'), lotName(article, 'B')];
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    const editor = await enterTransfer(page, article, '70');
    await expect(editor.getByText('Disponible : 100 bouteille')).toBeVisible();
    await editor.getByLabel(`Quantité du lot ${a}`).fill('50');
    await expect(editor.getByText('Reste : 20 bouteille')).toBeVisible();
    await expect(editor.getByText('Répartition incomplète')).toBeVisible();
    await editor.getByLabel(`Quantité du lot ${b}`).fill('20');
    await expect(editor.getByText('Répartition complète')).toBeVisible();
    await expect(editor.getByText('Reste : 0 bouteille')).toBeVisible();
    await page.getByRole('button', { name: 'Enregistrer le brouillon' }).click();
    await expect(page.getByText('Brouillon enregistré')).toBeVisible();
    await confirmValidation(page);
    await expect(page.getByText(/^Transfert TRF-\d{6} validé$/)).toBeVisible();
    // Détail : lots transférés sous l'article.
    const lots = page.getByRole('list', { name: 'Lots de la ligne' });
    await expect(lots.getByRole('listitem')).toHaveCount(2);
    await expect(lots).toContainText(`Lot ${a}`);
    await expect(lots).toContainText('50 bouteille');
    await expect(lots).toContainText(`Lot ${b}`);
    // Soldes des deux sites ; Σ lots = stock.
    expect(await balances(request, article, world.site)).toEqual({ [a]: '50.000', [b]: '30.000' });
    expect(await balances(request, article, world.depot)).toEqual({
      [a]: '50.000',
      [b]: '20.000',
    });
    expect(await level(request, article, world.site)).toBe('80.000');
    expect(await level(request, article, world.depot)).toBe('70.000');
    // Mouvements : une paire par lot, même lot sur les deux sites.
    const id = page.url().split('/').pop() ?? '';
    const movements = await api<{
      items: { movement_type: string; site_id: string; lot_id: string; quantity: string }[];
    }>(request, 'get', `/stock/movements?source_id=${id}&limit=50`);
    const ids = await lotIds(request, article);
    for (const [number, quantity] of [
      [a, '50.000'],
      [b, '20.000'],
    ] as const) {
      const ofLot = movements.items.filter((m) => m.lot_id === ids[number]);
      expect(ofLot.map((m) => [m.movement_type, m.site_id, m.quantity]).sort()).toEqual(
        [
          ['TRANSFER_IN', world.depot.id, quantity],
          ['TRANSFER_OUT', world.site.id, `-${quantity}`],
        ].sort(),
      );
    }
    // Annulation : chaque lot restauré exactement, une seule fois.
    await page.getByRole('button', { name: 'Annuler le transfert' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.locator('#cancel-reason').fill('Erreur de saisie');
    await dialog.getByRole('button', { name: 'Annuler le transfert' }).click();
    await expect(page.getByText(/^Transfert TRF-\d{6} annulé$/)).toBeVisible();
    expect(await balances(request, article, world.site)).toEqual({ [a]: '100.000', [b]: '50.000' });
    expect(await balances(request, article, world.depot)).toEqual({ [a]: '0.000', [b]: '0.000' });
    const again = await call(request, 'post', `/stock/transfers/${id}/cancel`, {
      reason: 'Seconde annulation',
    });
    expect(again.status()).toBe(409);
  });

  test('lot périmé : non sélectionnable, refusé par le serveur', async ({ page, request }) => {
    const article = await trackedArticle(request, [
      ['OLD', '10', 30],
      ['NEW', '10', 60],
    ]);
    ownerSql(
      `UPDATE stock_lots SET expiry_date = '${day(-2)}' WHERE number = '${lotName(article, 'OLD')}'`,
    );
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    const editor = await enterTransfer(page, article, '5');
    await expect(editor.getByText('Périmé — non transférable')).toBeVisible();
    await expect(editor.getByLabel(`Quantité du lot ${lotName(article, 'OLD')}`)).toBeDisabled();
    // Tentative forcée par l'API : refus du serveur, rien n'est écrit.
    const ids = await lotIds(request, article);
    const draft = await api(
      request,
      'post',
      '/stock/transfers',
      transferBody([[article, '5', [[ids[lotName(article, 'OLD')] ?? '', '5']]]]),
    );
    const refused = await call(request, 'post', `/stock/transfers/${draft.id}/validate`);
    expect(refused.status()).toBe(422);
    expect(((await refused.json()) as { code: string }).code).toBe('lot_expired_not_transferable');
    expect(await level(request, article, world.depot)).toBe('0.000');
  });

  test('lot insuffisant : validation refusée en bloc, message détaillé', async ({
    page,
    request,
  }) => {
    const article = await trackedArticle(request, [
      ['S', '5', 30],
      ['T', '20', 60],
    ]);
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    const editor = await enterTransfer(page, article, '8');
    await editor.getByLabel(`Quantité du lot ${lotName(article, 'S')}`).fill('8');
    await page.getByRole('button', { name: 'Enregistrer le brouillon' }).click();
    await expect(page.getByText('Brouillon enregistré')).toBeVisible();
    await confirmValidation(page);
    await expect(page.getByText(/Solde de lot insuffisant/)).toBeVisible();
    expect(await balances(request, article, world.site)).toEqual({
      [lotName(article, 'S')]: '5.000',
      [lotName(article, 'T')]: '20.000',
    });
  });

  test('conditionnement : 2 cartons répartis, présentation seulement si exacte', async ({
    request,
  }) => {
    const article = await trackedArticle(request, [
      ['C1', '30', 30],
      ['C2', '30', 60],
    ]);
    const carton = await api(request, 'post', `/catalog/articles/${article.id}/packagings`, {
      name: 'Carton 6',
      conversion: '6',
      sale_price: '2800',
    });
    const ids = await lotIds(request, article);
    const [c1, c2] = [ids[lotName(article, 'C1')] ?? '', ids[lotName(article, 'C2')] ?? ''];
    for (const [split, expected] of [
      [
        [
          [c1, '6'],
          [c2, '6'],
        ],
        '1.000',
      ],
      [
        [
          [c1, '5'],
          [c2, '7'],
        ],
        null,
      ],
    ] as [[string, string][], string | null][]) {
      const draft = await api(
        request,
        'post',
        '/stock/transfers',
        transferBody([[article, '2', split, carton.id]]),
      );
      await api(request, 'post', `/stock/transfers/${draft.id}/validate`);
      const movements = await api<{ items: { packaging_quantity: string | null }[] }>(
        request,
        'get',
        `/stock/movements?source_id=${draft.id}&limit=50`,
      );
      expect(movements.items).toHaveLength(4);
      expect(new Set(movements.items.map((m) => m.packaging_quantity))).toEqual(
        new Set([expected]),
      );
    }
    expect(await level(request, article, world.depot)).toBe('24.000');
  });

  test('concurrence : deux transferts simultanés du même lot sans double sortie', async ({
    request,
  }) => {
    const article = await trackedArticle(request, [['K', '10', 30]]);
    const lot = (await lotIds(request, article))[lotName(article, 'K')] ?? '';
    const drafts = await Promise.all(
      ['8', '5'].map((q) =>
        api(request, 'post', '/stock/transfers', transferBody([[article, q, [[lot, q]]]])),
      ),
    );
    const statuses = (
      await Promise.all(
        drafts.map((d) => call(request, 'post', `/stock/transfers/${d.id}/validate`)),
      )
    )
      .map((r) => r.status())
      .sort();
    expect(statuses).toEqual([200, 422]);
    const left = (await balances(request, article, world.site))[lotName(article, 'K')];
    expect(['2.000', '5.000']).toContain(left);
  });

  test('transfert multi-lots sur mobile : répartition, validation, sans débordement @mobile', async ({
    page,
    request,
  }) => {
    const article = await trackedArticle(request, [
      ['M', '6', 30],
      ['N', '6', 60],
    ]);
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    const editor = await enterTransfer(page, article, '9');
    const input = editor.getByLabel(`Quantité du lot ${lotName(article, 'M')}`);
    await input.fill('6');
    await editor.getByLabel(`Quantité du lot ${lotName(article, 'N')}`).fill('3');
    await expect(editor.getByText('Répartition complète')).toBeVisible();
    expect(await overflow(page)).toBe(false);
    const box = await input.boundingBox();
    const width = page.viewportSize()?.width ?? 0;
    expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(width);
    await page.getByRole('button', { name: 'Enregistrer le brouillon' }).click();
    await expect(page.getByText('Brouillon enregistré')).toBeVisible();
    await confirmValidation(page);
    await expect(page.getByText(/^Transfert TRF-\d{6} validé$/)).toBeVisible();
    const lots = page.getByRole('list', { name: 'Lots de la ligne' });
    await expect(lots.getByRole('listitem')).toHaveCount(2);
    expect(await overflow(page)).toBe(false);
    expect(await balances(request, article, world.depot)).toEqual({
      [lotName(article, 'M')]: '6.000',
      [lotName(article, 'N')]: '3.000',
    });
  });
});
