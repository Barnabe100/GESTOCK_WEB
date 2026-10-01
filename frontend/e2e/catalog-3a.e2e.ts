import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import {
  bearer,
  createActiveSite,
  createMember,
  loginUi,
  provisionTenant,
  SALE_NUMBER,
  tokenFor,
} from './support';

/**
 * Lot 3-A (ADR-0039), sur une entreprise créée pour l'exécution : scan EXACT d'un code-barres
 * au point de vente (code inconnu signalé, panier inchangé), article non géré en stock vendu
 * sans mouvement, passage « géré » → « non géré » refusé tant qu'il reste du stock, droits
 * distincts sur les prix (`catalog.article.price_update`) et les coûts
 * (`catalog.article.cost_view`), historique des prix sur la fiche article.
 */

const RUN = Date.now().toString().slice(-7);
const TENANT = `Catalogue E2E ${RUN}`;
const EMAIL = `catalogue-${RUN}@example.com`;
const PASSWORD = 'E2e-Catalogue-2026';
const MEMBER_PASSWORD = 'E2e-Membre-Catalogue-2026';
const BARCODE = `20${RUN}0001`;

interface Site {
  id: string;
  name: string;
  code: string;
}

interface Article {
  id: string;
  reference: string;
  designation: string;
  purchase_price?: string;
  sale_price: string;
}

interface World {
  token: string;
  main: Site;
  category: string;
  stocked: Article;
  service: Article;
  empty: Article;
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

async function article(
  request: APIRequestContext,
  token: string,
  category: string,
  reference: string,
  extra: Record<string, unknown> = {},
): Promise<Article> {
  return post<Article>(
    request,
    '/catalog/articles',
    {
      reference,
      designation: `Article ${reference}`,
      category_id: category,
      unit: 'u',
      purchase_price: '600',
      sale_price: '1000',
      ...extra,
    },
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
  if (!sites.some((s) => s.code === 'DEPOT')) {
    await createActiveSite<Site>(request, token, { name: 'Dépôt', code: 'DEPOT' });
  }
  const category = (await post(request, '/catalog/categories', { name: `Cat ${RUN}` }, token)).id;
  const stocked = await article(request, token, category, `SCAN-${RUN}`, { barcode: BARCODE });
  const service = await article(request, token, category, `SRV-${RUN}`, {
    stock_managed: false,
    sale_price: '5000',
    purchase_price: '0',
  });
  const empty = await article(request, token, category, `VIDE-${RUN}`);
  const entry = await post<{ id: string }>(
    request,
    '/stock/entries',
    {
      site_id: main.id,
      kind: 'INITIAL_STOCK',
      lines: [{ article_id: stocked.id, quantity: '30', unit_cost: '600' }],
    },
    token,
  );
  await post(request, `/stock/entries/${entry.id}/validate`, {}, token);
  world = { token, main, category, stocked, service, empty };
});

async function openPos(page: Page) {
  await loginUi(page, EMAIL, PASSWORD, TENANT);
  await page.goto('/pos');
  await expect(page.getByRole('heading', { name: 'Point de vente' })).toBeVisible();
  const siteChoice = page.locator('.sm-pos-header .p-dropdown');
  if (await siteChoice.count()) {
    await siteChoice.click();
    await page
      .locator('.p-dropdown-panel')
      .last()
      .getByText(world.main.name, { exact: true })
      .click();
  }
}

async function scan(page: Page, code: string) {
  const search = page.getByLabel('Rechercher un article (F2)');
  // Douchette : saisie immédiate puis Entrée, sans attendre la recherche différée.
  await search.fill(code);
  await search.press('Enter');
}

test.describe('Catalogue — Lot 3-A', () => {
  test('1, 2 — scan exact : article ajouté ; code inconnu : message, panier inchangé', async ({
    page,
  }) => {
    await openPos(page);
    // Une recherche affiche des résultats : Entrée sur un code inconnu ne doit RIEN ajouter.
    await page.getByLabel('Rechercher un article (F2)').fill(`SCAN-${RUN}`);
    await expect(
      page.getByRole('button', { name: `Ajouter Article SCAN-${RUN} au panier` }),
    ).toBeVisible();
    await scan(page, '0000000000000');
    await expect(page.getByRole('alert').filter({ hasText: 'Code-barres inconnu' })).toBeVisible();
    await expect(page.getByText('Panier vide : ajoutez des articles.')).toBeVisible();
    // Préfixe d'un code existant : jamais de correspondance partielle.
    await scan(page, BARCODE.slice(0, -1));
    await expect(page.getByRole('alert').filter({ hasText: 'Code-barres inconnu' })).toBeVisible();
    await expect(page.getByText('Panier vide : ajoutez des articles.')).toBeVisible();

    await scan(page, BARCODE);
    await expect(page.getByLabel(`Quantité de Article SCAN-${RUN}`, { exact: true })).toHaveValue(
      '1',
    );
    await expect(page.getByRole('alert')).toHaveCount(0);
  });

  test('3 — article non géré en stock : vendu, aucun mouvement de stock', async ({
    page,
    request,
  }) => {
    await openPos(page);
    await page.getByLabel('Rechercher un article (F2)').fill(`SRV-${RUN}`);
    const tile = page.getByRole('button', { name: `Ajouter Article SRV-${RUN} au panier` });
    await expect(tile).toContainText('Non géré en stock');
    await tile.click();
    await page.getByLabel(`Quantité de Article SRV-${RUN}`, { exact: true }).fill('3');
    await page.keyboard.press('F8');
    const payments = page.getByRole('dialog', { name: 'Paiements (F8)' });
    await payments
      .getByRole('group', { name: 'Ajouter un moyen de paiement' })
      .getByRole('button', { name: 'Espèces', exact: true })
      .click();
    await payments.getByLabel(/^Montant (reçu )?du paiement 1$/).fill('15000');
    await payments.getByRole('button', { name: 'Appliquer' }).click();
    await page.keyboard.press('F10');
    await page
      .getByRole('dialog', { name: 'Valider la vente ?' })
      .getByRole('button', { name: 'Valider la vente (F10)' })
      .click();
    await expect(page.locator('.p-dialog-title', { hasText: SALE_NUMBER })).toBeVisible();
    const movements = (await (
      await request.get(`/api/v1/stock/movements?article_id=${world.service.id}`, {
        headers: bearer(world.token),
      })
    ).json()) as { total: number };
    expect(movements.total).toBe(0);
  });

  test('4, 5 — géré → non géré : refusé avec du stock, accepté à stock nul', async ({ page }) => {
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    for (const [item, expected] of [
      [world.stocked, 'refused'],
      [world.empty, 'ok'],
    ] as const) {
      await page.goto(`/catalog/articles/${item.id}`);
      await expect(page.getByRole('heading', { name: item.designation })).toBeVisible();
      await page.getByRole('button', { name: 'Modifier' }).click();
      const dialog = page.getByRole('dialog');
      await dialog.getByLabel('Géré en stock').uncheck();
      await dialog.getByRole('button', { name: 'Enregistrer' }).click();
      if (expected === 'refused') {
        await expect(page.getByText(/encore du stock sur au moins un site/)).toBeVisible();
        await dialog.getByRole('button', { name: 'Annuler' }).click();
      } else {
        await expect(dialog).toBeHidden();
        await expect(page.getByText('Non géré en stock').first()).toBeVisible();
      }
    }
  });

  test('6, 7 — Gestionnaire : prix refusés, coûts visibles ; Consultant : coûts non exposés', async ({
    page,
    request,
  }) => {
    // Gestionnaire (rôle de base) : cost_view, mais pas price_update.
    const email = await createMember(request, world.token, 'manager', MEMBER_PASSWORD);
    const token = await tokenFor(request, email, MEMBER_PASSWORD, TENANT);
    const refused = await request.patch(`/api/v1/catalog/articles/${world.stocked.id}`, {
      headers: bearer(token),
      data: { sale_price: '1' },
    });
    expect(refused.status()).toBe(403);
    expect(((await refused.json()) as { code: string }).code).toBe('price_update_not_allowed');
    const detail = (await (
      await request.get(`/api/v1/catalog/articles/${world.stocked.id}`, { headers: bearer(token) })
    ).json()) as Record<string, unknown>;
    expect(detail.purchase_price).toBe('600.00');

    await loginUi(page, email, MEMBER_PASSWORD, TENANT);
    await page.goto(`/catalog/articles/${world.stocked.id}`);
    await expect(page.getByRole('heading', { name: world.stocked.designation })).toBeVisible();
    await expect(page.getByText("Prix d'achat").first()).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Historique des prix' })).toHaveCount(0);
    await page.getByRole('button', { name: 'Modifier' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByLabel(/^Prix de vente/)).toHaveAttribute('readonly', '');
    await expect(dialog.getByLabel(/^Prix d'achat/)).toHaveAttribute('readonly', '');
    await dialog.getByRole('button', { name: 'Annuler' }).click();
    await page.goto('/stock/levels');
    await expect(page.getByText(`SCAN-${RUN}`, { exact: true }).first()).toBeVisible();
    await expect(page.getByRole('columnheader', { name: 'CMUP' })).toBeVisible();

    // Consultant (sans cost_view) : aucun coût dans les réponses ni à l'écran.
    const viewerEmail = await createMember(request, world.token, 'viewer', MEMBER_PASSWORD);
    const viewerToken = await tokenFor(request, viewerEmail, MEMBER_PASSWORD, TENANT);
    const viewed = (await (
      await request.get(`/api/v1/catalog/articles/${world.stocked.id}`, {
        headers: bearer(viewerToken),
      })
    ).json()) as Record<string, unknown>;
    expect('purchase_price' in viewed).toBe(false);
    const levels = (await (
      await request.get('/api/v1/stock/levels', { headers: bearer(viewerToken) })
    ).json()) as { items: Record<string, unknown>[] };
    expect(levels.items.length).toBeGreaterThan(0);
    expect(levels.items.some((l) => 'average_cost' in l || 'stock_value' in l)).toBe(false);
    await page.context().clearCookies();
    await loginUi(page, viewerEmail, MEMBER_PASSWORD, TENANT);
    await page.goto(`/catalog/articles/${world.stocked.id}`);
    await expect(page.getByRole('heading', { name: world.stocked.designation })).toBeVisible();
    await expect(page.getByText("Prix d'achat")).toHaveCount(0);
    await page.goto('/stock/levels');
    await expect(page.getByText(`SCAN-${RUN}`, { exact: true }).first()).toBeVisible();
    await expect(page.getByRole('columnheader', { name: 'CMUP' })).toHaveCount(0);
  });

  test('8 — Administrateur : modification de prix et historique sur la fiche', async ({ page }) => {
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto(`/catalog/articles/${world.stocked.id}`);
    await page.getByRole('button', { name: 'Modifier' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel(/^Prix de vente/).fill('1250');
    await dialog.getByRole('button', { name: 'Enregistrer' }).click();
    await expect(dialog).toBeHidden();
    const history = page.locator('section', {
      has: page.getByRole('heading', { name: 'Historique des prix' }),
    });
    await expect(history).toBeVisible();
    await expect(history.getByRole('row')).toHaveCount(3); // en-tête + modification + création
    await expect(history.getByRole('row').nth(1)).toContainText(/1\s000\s*F\s*CFA → 1\s250/);
    await expect(history.getByRole('columnheader', { name: "Prix d'achat" })).toBeVisible();
  });
});
