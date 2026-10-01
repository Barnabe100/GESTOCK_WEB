import { readFileSync } from 'node:fs';

import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import {
  bearer,
  createActiveSite,
  createMember,
  loginUi,
  provisionTenant,
  tokenFor,
} from './support';

/**
 * Lot 2 — historique des ventes, sur une entreprise créée pour l'exécution (deux sites) :
 * tri chronologique par défaut, filtres (référence de paiement, référence article, « Mes
 * ventes »), UNE action « Exporter » avec choix du format (CSV, Excel, PDF) limitée au
 * périmètre de la liste et auditée, permission `sales.sale.export`, isolation site / entreprise,
 * fiche enrichie (mouvements de stock et chronologie selon les permissions).
 */

const RUN = Date.now().toString().slice(-7);
const TENANT = `Historique E2E ${RUN}`;
const EMAIL = `historique-${RUN}@example.com`;
const PASSWORD = 'E2e-Historique-2026';
const OTHER_TENANT = `Historique Autre ${RUN}`;
const OTHER_EMAIL = `historique-autre-${RUN}@example.com`;
const MEMBER_PASSWORD = 'E2e-Membre-Historique-2026';
const OM_REFERENCE = `OM-${RUN}`;

interface Site {
  id: string;
  name: string;
  code: string;
}

interface Sale {
  id: string;
  number: string;
  total: string;
}

interface World {
  token: string;
  main: Site;
  bobo: Site;
  articles: { cement: string; nails: string };
  sales: { cash: Sale; mobile: Sale; bobo: Sale };
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

async function sell(
  request: APIRequestContext,
  token: string,
  site: Site,
  article: string,
  payment: (total: string) => Record<string, unknown>,
): Promise<Sale> {
  const draft = await post<Sale>(
    request,
    '/sales',
    { site_id: site.id, lines: [{ article_id: article, quantity: '2' }] },
    token,
  );
  return post<Sale>(
    request,
    `/sales/${draft.id}/validate`,
    { payments: [payment(draft.total)] },
    token,
  );
}

test.describe.configure({ mode: 'serial' });

test.beforeAll(async ({ request }) => {
  await provisionTenant(request, {
    name: TENANT,
    profile: 'retail.quincaillerie',
    email: EMAIL,
    password: PASSWORD,
  });
  const token = await tokenFor(request, EMAIL, PASSWORD, TENANT);
  const sites = (await (
    await request.get('/api/v1/sites', { headers: bearer(token) })
  ).json()) as Site[];
  const main = sites[0] as Site;
  const bobo =
    sites.find((s) => s.code === 'BOBO') ??
    (await createActiveSite<Site>(request, token, { name: 'Bobo', code: 'BOBO' }));
  world = {
    token,
    main,
    bobo,
    articles: { cement: '', nails: '' },
    sales: {} as World['sales'],
  };
  const category = await post(request, '/catalog/categories', { name: `Historique ${RUN}` });
  for (const [key, reference, price] of [
    ['cement', `CIM-${RUN}`, '5000'],
    ['nails', `CLOU-${RUN}`, '1000'],
  ] as const) {
    const article = await post(request, '/catalog/articles', {
      reference,
      designation: `Article ${reference}`,
      category_id: category.id,
      unit: 'u',
      purchase_price: '500',
      sale_price: price,
    });
    world.articles[key] = article.id;
    for (const site of [main, bobo]) {
      const entry = await post(request, '/stock/entries', {
        site_id: site.id,
        kind: 'INITIAL_STOCK',
        lines: [{ article_id: article.id, quantity: '100', unit_cost: '500' }],
      });
      await post(request, `/stock/entries/${entry.id}/validate`, {});
    }
  }
  const orange = await post(request, '/payment-methods', {
    label: 'Orange Money',
    kind: 'MOBILE_MONEY',
    reference_required: true,
  });
  const cash = (total: string) => ({ amount: total, method: 'CASH' });
  world.sales.cash = await sell(request, token, main, world.articles.cement, cash);
  world.sales.mobile = await sell(request, token, main, world.articles.nails, (total) => ({
    amount: total,
    payment_method_id: orange.id,
    reference: OM_REFERENCE,
  }));
  world.sales.bobo = await sell(request, token, bobo, world.articles.cement, cash);
});

async function openSales(page: Page, email = EMAIL, password = PASSWORD) {
  await loginUi(page, email, password, TENANT);
  await page.goto('/sales');
  await expect(page.getByRole('heading', { name: 'Ventes', exact: true })).toBeVisible();
}

/** « Exporter » → format : fichier téléchargé (contenu lu sur le disque). */
async function exportAs(page: Page, format: 'Excel (.xlsx)' | 'CSV (.csv)' | 'PDF (.pdf)') {
  await page.getByRole('button', { name: 'Exporter' }).click();
  const download = page.waitForEvent('download');
  await page.getByRole('menuitem', { name: format }).click();
  const file = await download;
  return { name: file.suggestedFilename(), content: readFileSync((await file.path()) as string) };
}

const rowsOf = (csv: Buffer) =>
  csv
    .toString('utf8')
    .replace(/^\uFEFF/, '')
    .trim()
    .split('\r\n')
    .map((line) => line.split(';'));

test.describe('Historique des ventes (Lot 2)', () => {
  test('tri chronologique, filtres, UNE action Exporter (CSV, Excel, PDF) au périmètre de la liste', async ({
    page,
    request,
  }) => {
    await openSales(page);
    // Par défaut : la plus récente d'abord (création), quel que soit le numéro.
    const firstRow = page.locator('.sm-table tbody tr').first();
    await expect(firstRow).toContainText(world.sales.bobo.number);
    await expect(page.getByRole('button', { name: 'Exporter' })).toHaveCount(1);

    // Référence de paiement (transaction) ≠ référence article.
    await page.getByRole('button', { name: 'Plus de filtres' }).click();
    await page.getByLabel('Référence de paiement').fill(OM_REFERENCE);
    await expect(page.locator('.sm-table tbody tr')).toHaveCount(1);
    await expect(firstRow).toContainText(world.sales.mobile.number);

    const csv = await exportAs(page, 'CSV (.csv)');
    expect(csv.name).toMatch(/^ventes-\d{8}-\d{4}\.csv$/);
    expect([...csv.content.subarray(0, 3)]).toEqual([0xef, 0xbb, 0xbf]); // BOM UTF-8
    const rows = rowsOf(csv.content);
    expect(rows[0]?.slice(0, 3)).toEqual(['Numéro', 'Date de vente', 'Site']);
    expect(rows.slice(1).map((r) => r[0])).toEqual([world.sales.mobile.number]);

    await page.getByLabel('Référence de paiement').fill('');
    await page.getByLabel('Référence article').fill(`CIM-${RUN}`);
    await expect(page.locator('.sm-table tbody tr')).toHaveCount(2);
    const xlsx = await exportAs(page, 'Excel (.xlsx)');
    expect(xlsx.name).toMatch(/\.xlsx$/);
    expect(xlsx.content.subarray(0, 2).toString()).toBe('PK'); // classeur Office Open XML
    const pdf = await exportAs(page, 'PDF (.pdf)');
    expect(pdf.name).toMatch(/\.pdf$/);
    expect(pdf.content.subarray(0, 4).toString()).toBe('%PDF');

    // Chaque export est audité : format, filtres renseignés, nombre de lignes.
    const logs = (await (
      await request.get('/api/v1/audit-logs?action=export.generated', {
        headers: bearer(world.token),
      })
    ).json()) as { items: { data: Record<string, unknown> }[] };
    expect(logs.items.slice(0, 3).map((i) => [i.data.format, i.data.row_count])).toEqual([
      ['pdf', 2],
      ['xlsx', 2],
      ['csv', 1],
    ]);
    expect(logs.items[2]?.data.filters).toEqual({ payment_reference: OM_REFERENCE });
  });

  test('permissions et portée : Vendeur sans export (ses ventes), Gestionnaire avec', async ({
    page,
    request,
  }) => {
    const sellerEmail = await createMember(request, world.token, 'seller', MEMBER_PASSWORD);
    const sellerToken = await tokenFor(request, sellerEmail, MEMBER_PASSWORD, TENANT);
    const own = await sell(request, sellerToken, world.main, world.articles.nails, (total) => ({
      amount: total,
      method: 'CASH',
    }));
    const denied = await request.get('/api/v1/sales/export?format=csv', {
      headers: bearer(sellerToken),
    });
    expect(denied.status()).toBe(403);
    await openSales(page, sellerEmail, MEMBER_PASSWORD);
    await expect(page.locator('.sm-table tbody tr')).toHaveCount(1);
    await expect(page.locator('.sm-table tbody tr').first()).toContainText(own.number);
    await expect(page.getByRole('button', { name: 'Exporter' })).toHaveCount(0);

    // Gestionnaire : export au périmètre exact de sa liste.
    const managerEmail = await createMember(request, world.token, 'manager', MEMBER_PASSWORD);
    const managerToken = await tokenFor(request, managerEmail, MEMBER_PASSWORD, TENANT);
    const list = (await (
      await request.get('/api/v1/sales?limit=100', { headers: bearer(managerToken) })
    ).json()) as { items: Sale[]; total: number };
    const exported = await request.get('/api/v1/sales/export?format=csv', {
      headers: bearer(managerToken),
    });
    expect(exported.status()).toBe(200);
    const numbers = rowsOf(await exported.body())
      .slice(1)
      .map((r) => r[0]);
    expect(numbers).toEqual(list.items.map((s) => s.number));
    expect(numbers).toHaveLength(list.total);
  });

  test('isolation : site restreint et autre entreprise', async ({ request }) => {
    // Gestionnaire limité au site de Bobo : seules les ventes de Bobo sont exportées.
    const roles = (await (
      await request.get('/api/v1/roles', { headers: bearer(world.token) })
    ).json()) as { id: string; template_code: string | null }[];
    const manager = roles.find((r) => r.template_code === 'manager');
    const email = `bobo-${RUN}@example.com`;
    const created = await request.post('/api/v1/members', {
      headers: bearer(world.token),
      data: {
        email,
        full_name: `Gestion Bobo ${RUN}`,
        password: 'Provisoire-E2E-2026',
        roles: [{ role_id: manager?.id }],
        all_sites: false,
        site_ids: [world.bobo.id],
      },
    });
    expect(created.status(), await created.text()).toBe(201);
    const first = await tokenFor(request, email, 'Provisoire-E2E-2026', TENANT);
    await request.post('/api/v1/me/password', {
      headers: bearer(first),
      data: { current_password: 'Provisoire-E2E-2026', new_password: MEMBER_PASSWORD },
    });
    const boboToken = await tokenFor(request, email, MEMBER_PASSWORD, TENANT);
    const boboRows = rowsOf(
      await (
        await request.get('/api/v1/sales/export?format=csv', { headers: bearer(boboToken) })
      ).body(),
    ).slice(1);
    expect(boboRows.map((r) => r[0])).toEqual([world.sales.bobo.number]);

    // Autre entreprise : aucune vente de celle-ci, même filtrée sur l'un de ses sites.
    await provisionTenant(request, {
      name: OTHER_TENANT,
      profile: 'retail.quincaillerie',
      email: OTHER_EMAIL,
      password: PASSWORD,
    });
    const otherToken = await tokenFor(request, OTHER_EMAIL, PASSWORD, OTHER_TENANT);
    const other = await request.get(`/api/v1/sales/export?format=csv&site_id=${world.main.id}`, {
      headers: bearer(otherToken),
    });
    expect(other.status()).toBe(200);
    expect(rowsOf(await other.body()).slice(1)).toEqual([]);
  });

  test('fiche enrichie : mouvements (stock.movement.view) et chronologie (audit.log.view)', async ({
    page,
    request,
  }) => {
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto(`/sales/${world.sales.mobile.id}`);
    await expect(
      page.getByRole('heading', { name: `Vente ${world.sales.mobile.number}` }),
    ).toBeVisible();
    const movements = page.locator('section', {
      has: page.getByRole('heading', { name: 'Mouvements de stock' }),
    });
    await expect(movements).toContainText(`CLOU-${RUN}`);
    await expect(movements.getByRole('cell', { name: 'Vente', exact: true })).toBeVisible();
    const history = page.getByRole('list', { name: 'Chronologie' });
    await expect(history).toContainText('Vente créée (brouillon)');
    await expect(history).toContainText('Vente validée');
    await expect(history).toContainText('Orange Money');

    // Gestionnaire : mouvements oui (stock.movement.view), chronologie non (pas audit.log.view).
    const managerEmail = await createMember(request, world.token, 'manager', MEMBER_PASSWORD);
    await page.context().clearCookies();
    await loginUi(page, managerEmail, MEMBER_PASSWORD, TENANT);
    await page.goto(`/sales/${world.sales.mobile.id}`);
    await expect(page.getByRole('heading', { name: 'Mouvements de stock' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Chronologie' })).toHaveCount(0);
  });
});
