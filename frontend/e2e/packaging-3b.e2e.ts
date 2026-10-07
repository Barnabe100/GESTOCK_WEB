import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import {
  assortArticles,
  bearer,
  createMember,
  loginUi,
  provisionTenant,
  SALE_NUMBER,
  tokenFor,
} from './support';

/**
 * Lot 3-B (ADR-0040), sur une entreprise créée pour l'exécution : article sans conditionnement
 * vendu dans son unité de base, conditionnements créés sur la fiche article, vente au point de
 * vente par présentation (unité de base / carton), stock TOUJOURS en unité de base, quantités
 * entières ou décimales, conditionnement décimal, désactivation (plus proposée, vente
 * historique fidèle), reçu, stock insuffisant après conversion, isolation des entreprises,
 * prix non configuré (conditionnement créé sans droit sur les prix : invendable tant qu'un
 * habilité ne l'a pas fixé — validation du lot).
 */

const RUN = Date.now().toString().slice(-7);
const TENANT = `Conditionnements E2E ${RUN}`;
const EMAIL = `conditionnements-${RUN}@example.com`;
const PASSWORD = 'E2e-Conditionnement-2026';
const OTHER_TENANT = `Autre entreprise E2E ${RUN}`;
const OTHER_EMAIL = `autre-cond-${RUN}@example.com`;
const MEMBER_PASSWORD = 'E2e-Membre-Conditionnement-2026';

interface Site {
  id: string;
  name: string;
}

interface World {
  token: string;
  main: Site;
  category: string;
  coca: string; // créé par l'interface (scénario 1)
  carton?: string;
  rice: string;
  bag: string;
  beer: string;
  beerCarton: string;
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

async function stockIn(request: APIRequestContext, articleId: string, quantity: string) {
  const entry = await post(request, '/stock/entries', {
    site_id: world.main.id,
    kind: 'INITIAL_STOCK',
    lines: [{ article_id: articleId, quantity, unit_cost: '100' }],
  });
  await post(request, `/stock/entries/${entry.id}/validate`, {});
}

async function level(request: APIRequestContext, reference: string): Promise<string> {
  const page = (await (
    await request.get(`/api/v1/stock/levels?search=${reference}&site_id=${world.main.id}`, {
      headers: bearer(world.token),
    })
  ).json()) as { items: { quantity: string }[] };
  return page.items[0]?.quantity ?? 'none';
}

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

async function addTile(page: Page, designation: string, search: string) {
  await page.getByLabel('Rechercher un article (F2)').fill(search);
  const tile = page.getByRole('button', { name: `Ajouter ${designation} au panier` });
  await tile.click();
  return tile;
}

async function choosePresentation(page: Page, designation: string, option: RegExp) {
  await page
    .locator('.p-dropdown', { has: page.getByLabel(`Présentation de ${designation}`) })
    .click();
  await page.locator('.p-dropdown-panel').last().getByRole('option', { name: option }).click();
}

/** Espèces pour le total affiché, puis validation ; renvoie le dialogue du reçu. */
async function payCashAndValidate(page: Page, amount: string) {
  await page.keyboard.press('F8');
  const payments = page.getByRole('dialog', { name: 'Paiements (F8)' });
  await payments
    .getByRole('group', { name: 'Ajouter un moyen de paiement' })
    .getByRole('button', { name: 'Espèces', exact: true })
    .click();
  await payments.getByLabel(/^Montant (reçu )?du paiement 1$/).fill(amount);
  await payments.getByRole('button', { name: 'Appliquer' }).click();
  await page.keyboard.press('F10');
  await page
    .getByRole('dialog', { name: 'Valider la vente ?' })
    .getByRole('button', { name: 'Valider la vente (F10)' })
    .click();
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
  world = { token, main: sites[0] as Site } as World;
  world.category = (await post(request, '/catalog/categories', { name: `Boissons ${RUN}` })).id;
  // Article au poids (quantités décimales) avec un sac de 25,5 kg.
  world.rice = (
    await post(request, '/catalog/articles', {
      reference: `RIZ-${RUN}`,
      designation: `Riz ${RUN}`,
      category_id: world.category,
      unit: 'kg',
      sale_price: '700',
      decimal_quantity_allowed: true,
    })
  ).id;
  await assortArticles(request, token, [world.rice]);
  world.bag = (
    await post(request, `/catalog/articles/${world.rice}/packagings`, {
      name: 'Sac 25,5 kg',
      conversion: '25.5',
      sale_price: '17000',
    })
  ).id;
  await stockIn(request, world.rice, '100');
  // Article à 40 en stock et carton de 24 : 2 cartons dépassent le stock.
  world.beer = (
    await post(request, '/catalog/articles', {
      reference: `BIERE-${RUN}`,
      designation: `Bière ${RUN}`,
      category_id: world.category,
      unit: 'bouteille',
      sale_price: '650',
    })
  ).id;
  await assortArticles(request, token, [world.beer]);
  world.beerCarton = (
    await post(request, `/catalog/articles/${world.beer}/packagings`, {
      name: 'Carton 24',
      conversion: '24',
      sale_price: '15000',
    })
  ).id;
  await stockIn(request, world.beer, '40');
});

test.describe('Conditionnements — Lot 3-B', () => {
  test('1, 3 — article créé sans conditionnement, puis conditionnement sur la fiche', async ({
    page,
    request,
  }) => {
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto('/catalog/articles');
    await page.getByRole('button', { name: 'Ajouter' }).first().click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel(/^Référence/).fill(`COCA-${RUN}`);
    await dialog.getByLabel(/^Désignation/).fill(`Coca ${RUN}`);
    await dialog.locator('.p-dropdown', { has: page.locator('#article-category') }).click();
    await page
      .locator('.p-dropdown-panel')
      .last()
      .getByRole('option', { name: `Boissons ${RUN}` })
      .click();
    await dialog.getByLabel(/^Unité/).fill('pièce');
    await dialog.getByLabel(/^Prix de vente/).fill('500');
    // Quantités entières par défaut.
    await expect(dialog.getByLabel('Quantités décimales autorisées')).not.toBeChecked();
    // Assortiment (ADR-0046) : site choisi EXPLICITEMENT à la création (« Proposer sur les
    // sites »), sinon l'article n'est proposé par aucun site.
    const sites = dialog.locator('.p-multiselect', { has: page.locator('#article-sites') });
    await sites.click();
    await page.locator('.p-multiselect-panel .p-multiselect-item').first().click();
    await sites.click();
    await dialog.getByRole('button', { name: 'Enregistrer' }).click();
    await expect(dialog).toBeHidden();
    const found = (await (
      await request.get(`/api/v1/catalog/articles?search=COCA-${RUN}`, {
        headers: bearer(world.token),
      })
    ).json()) as { items: { id: string; decimal_quantity_allowed: boolean }[] };
    world.coca = found.items[0]?.id ?? '';
    expect(found.items[0]?.decimal_quantity_allowed).toBe(false);
    await stockIn(request, world.coca, '50');

    await page.goto(`/catalog/articles/${world.coca}`);
    const section = page.locator('section', {
      has: page.getByRole('heading', { name: 'Conditionnements de vente' }),
    });
    await expect(section.getByText("Aucun conditionnement : l'article se vend")).toBeVisible();
    await section.getByRole('button', { name: 'Nouveau conditionnement' }).click();
    const form = page.getByRole('dialog', { name: 'Nouveau conditionnement' });
    await form.getByLabel(/^Nom/).fill('Carton 6');
    // Article entier : conversion décimale refusée par le serveur.
    await form.getByLabel(/^Contient/).fill('6,5');
    await form.getByLabel(/^Prix de vente/).fill('2800');
    await form.getByRole('button', { name: 'Enregistrer' }).click();
    await expect(
      page.getByText('la conversion doit être un nombre entier', { exact: false }),
    ).toBeVisible();
    await form.getByLabel(/^Contient/).fill('6');
    await form.getByRole('button', { name: 'Enregistrer' }).click();
    await expect(form).toBeHidden();
    const row = section.getByRole('row').filter({ hasText: 'Carton 6' });
    await expect(row).toContainText('6 pièce');
    await expect(row).toContainText(/2\s800/);
    const packagings = (await (
      await request.get(`/api/v1/catalog/articles/${world.coca}/packagings`, {
        headers: bearer(world.token),
      })
    ).json()) as { items: { id: string }[] };
    world.carton = packagings.items[0]?.id;
  });

  test('2, 4, 5, 12 — POS : unité de base et carton, stock en unité de base, reçu', async ({
    page,
    request,
  }) => {
    await openPos(page);
    const tile = await addTile(page, `Coca ${RUN}`, `COCA-${RUN}`);
    await expect(tile).toContainText('+ 1 conditionnement');
    // Ligne 1 : passage au carton, 2 cartons = 12 pièces.
    await choosePresentation(page, `Coca ${RUN}`, /Carton 6/);
    await page.getByLabel(`Quantité de Coca ${RUN} (Carton 6)`).fill('2');
    await expect(page.getByTestId('pos-base-quantity')).toHaveText('Soit 12 pièce');
    // Ligne 2 : unité de base (toujours disponible), 3 pièces.
    await tile.click();
    await page.getByLabel(`Quantité de Coca ${RUN}`, { exact: true }).fill('3');
    await expect(page.getByTestId('pos-total')).toHaveText(/7\s100/);
    await payCashAndValidate(page, '7100');
    const receipt = page.getByTestId('pos-receipt');
    await expect(page.locator('.p-dialog-title', { hasText: SALE_NUMBER })).toBeVisible();
    const lines = receipt.getByTestId('receipt-line');
    await expect(lines.nth(0)).toContainText(/2 Carton 6 × 2\s800/);
    await expect(lines.nth(0)).toContainText(/5\s600/);
    await expect(lines.nth(1)).toContainText(/3 pièce × 500/);
    await expect(lines.nth(1)).toContainText(/1\s500/);
    // Stock en unité de base : 50 − 12 − 3.
    expect(await level(request, `COCA-${RUN}`)).toBe('35.000');
  });

  test('6, 7, 8 — quantités décimales, entières, conditionnement décimal', async ({
    page,
    request,
  }) => {
    await openPos(page);
    // Article entier : 2,5 signalé, validation impossible.
    await addTile(page, `Coca ${RUN}`, `COCA-${RUN}`);
    await page.getByLabel(`Quantité de Coca ${RUN}`, { exact: true }).fill('2,5');
    await expect(page.getByText('Quantité entière uniquement pour cet article')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Valider la vente (F10)' })).toBeDisabled();
    await page.getByRole('button', { name: `Retirer Coca ${RUN} du panier` }).click();
    // Article au poids : 2,5 kg, puis 1,5 sac de 25,5 kg = 38,25 kg.
    const tile = await addTile(page, `Riz ${RUN}`, `RIZ-${RUN}`);
    await page.getByLabel(`Quantité de Riz ${RUN}`, { exact: true }).fill('2,5');
    await choosePresentation(page, `Riz ${RUN}`, /Sac 25,5 kg/);
    await page.getByLabel(`Quantité de Riz ${RUN} (Sac 25,5 kg)`).fill('1,5');
    await expect(page.getByTestId('pos-base-quantity')).toHaveText('Soit 38,25 kg');
    await tile.click();
    await page.getByLabel(`Quantité de Riz ${RUN}`, { exact: true }).fill('2,5');
    // 1,5 × 17 000 + 2,5 × 700 = 25 500 + 1 750.
    await expect(page.getByTestId('pos-total')).toHaveText(/27\s250/);
    await payCashAndValidate(page, '27250');
    await expect(page.locator('.p-dialog-title', { hasText: SALE_NUMBER })).toBeVisible();
    // 100 − 38,25 − 2,5.
    expect(await level(request, `RIZ-${RUN}`)).toBe('59.250');
  });

  test('9, 10, 11 — conditionnement désactivé : plus proposé ; vente historique fidèle', async ({
    page,
    request,
  }) => {
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto(`/catalog/articles/${world.coca}`);
    const row = page.getByRole('row').filter({ hasText: 'Carton 6' });
    await expect(row).toContainText('Utilisé en vente');
    await row.getByRole('button', { name: 'Désactiver' }).click();
    const confirm = page.getByRole('alertdialog').or(page.getByRole('dialog')).last();
    await expect(confirm).toContainText('les ventes passées restent inchangées');
    await confirm.getByRole('button', { name: 'Désactiver' }).click();
    await expect(row).toContainText('Inactif');

    await page.goto('/pos');
    const siteChoice = page.locator('.sm-pos-header .p-dropdown');
    if (await siteChoice.count()) {
      await siteChoice.click();
      await page
        .locator('.p-dropdown-panel')
        .last()
        .getByText(world.main.name, { exact: true })
        .click();
    }
    const tile = await addTile(page, `Coca ${RUN}`, `COCA-${RUN}`);
    await expect(tile).not.toContainText('conditionnement');
    await expect(page.getByLabel(`Présentation de Coca ${RUN}`)).toHaveCount(0);

    // Vente historique : présentation et prix figés.
    const sales = (await (
      await request.get(`/api/v1/sales?article_id=${world.coca}&limit=5`, {
        headers: bearer(world.token),
      })
    ).json()) as { items: { id: string }[] };
    await page.goto(`/sales/${sales.items.at(-1)?.id ?? ''}`);
    await expect(page.getByText('2 Carton 6')).toBeVisible();
    await expect(page.getByRole('cell', { name: '12 pièce' }).first()).toBeVisible();
    const refused = await request.post('/api/v1/pos/checkout', {
      headers: bearer(world.token),
      data: {
        site_id: world.main.id,
        lines: [{ article_id: world.coca, packaging_id: world.carton, quantity: '1' }],
        payments: [],
        customer_id: null,
        idempotency_key: crypto.randomUUID(),
      },
    });
    expect(refused.status()).toBe(422);
    expect(((await refused.json()) as { code: string }).code).toBe('packaging_inactive');
  });

  test('13 — stock insuffisant après conversion : refus, stock inchangé', async ({
    page,
    request,
  }) => {
    await openPos(page);
    await addTile(page, `Bière ${RUN}`, `BIERE-${RUN}`);
    await choosePresentation(page, `Bière ${RUN}`, /Carton 24/);
    await page.getByLabel(`Quantité de Bière ${RUN} (Carton 24)`).fill('2');
    await expect(page.getByText('Stock du site insuffisant (indicatif)')).toBeVisible();
    await payCashAndValidate(page, '30000');
    await expect(
      page.getByRole('dialog', { name: 'Valider la vente ?' }).getByText(/Stock insuffisant/),
    ).toBeVisible();
    expect(await level(request, `BIERE-${RUN}`)).toBe('40.000');
  });

  test('14 — isolation : conditionnements invisibles et inutilisables par une autre entreprise', async ({
    request,
  }) => {
    await provisionTenant(request, {
      name: OTHER_TENANT,
      profile: 'retail.alimentation',
      email: OTHER_EMAIL,
      password: PASSWORD,
    });
    const other = await tokenFor(request, OTHER_EMAIL, PASSWORD, OTHER_TENANT);
    const listed = await request.get(`/api/v1/catalog/articles/${world.rice}/packagings`, {
      headers: bearer(other),
    });
    expect(listed.status()).toBe(404);
    const patched = await request.patch(`/api/v1/catalog/packagings/${world.bag}`, {
      headers: bearer(other),
      data: { sale_price: '1' },
    });
    expect(patched.status()).toBe(404);
    const category = await post(request, '/catalog/categories', { name: 'Divers' }, other);
    const own = await post(
      request,
      '/catalog/articles',
      { reference: 'B-1', designation: 'B', category_id: category.id, unit: 'u' },
      other,
    );
    const sites = (await (
      await request.get('/api/v1/sites', { headers: bearer(other) })
    ).json()) as Site[];
    const sale = await request.post('/api/v1/sales', {
      headers: bearer(other),
      data: {
        site_id: sites[0]?.id,
        lines: [{ article_id: own.id, packaging_id: world.bag, quantity: '1' }],
      },
    });
    expect(sale.status()).toBe(422);
    expect(((await sale.json()) as { code: string }).code).toBe('packaging_not_found');
  });

  test("prix non configuré : invendable tant qu'un habilité ne l'a pas fixé", async ({
    page,
    request,
  }) => {
    // Gestionnaire (informations générales, sans droit sur les prix) : création par l'interface.
    const managerEmail = await createMember(request, world.token, 'manager', MEMBER_PASSWORD);
    await loginUi(page, managerEmail, MEMBER_PASSWORD, TENANT);
    await page.goto(`/catalog/articles/${world.beer}`);
    const section = page.locator('section', {
      has: page.getByRole('heading', { name: 'Conditionnements de vente' }),
    });
    await section.getByRole('button', { name: 'Nouveau conditionnement' }).click();
    const form = page.getByRole('dialog', { name: 'Nouveau conditionnement' });
    await expect(form.getByLabel(/^Prix de vente/)).toHaveAttribute('readonly', '');
    await expect(form.getByText(/restera invendable/)).toBeVisible();
    await form.getByLabel(/^Nom/).fill('Pack 6');
    await form.getByLabel(/^Contient/).fill('6');
    await form.getByRole('button', { name: 'Enregistrer' }).click();
    await expect(form).toBeHidden();
    const row = section.getByRole('row').filter({ hasText: 'Pack 6' });
    await expect(row).toContainText('Prix non configuré');
    const listed = (await (
      await request.get(`/api/v1/catalog/articles/${world.beer}/packagings?status=active`, {
        headers: bearer(world.token),
      })
    ).json()) as { items: { id: string; name: string; sale_price: string | null }[] };
    const pack = listed.items.find((p) => p.name === 'Pack 6');
    expect(pack?.sale_price).toBeNull();

    // Point de vente : non proposé ; le serveur refuse toute vente avec ce conditionnement.
    await page.context().clearCookies();
    await openPos(page);
    const tile = await addTile(page, `Bière ${RUN}`, `BIERE-${RUN}`);
    await expect(tile).toContainText('+ 1 conditionnement'); // le carton 24 seulement
    await page
      .locator('.p-dropdown', { has: page.getByLabel(`Présentation de Bière ${RUN}`) })
      .click();
    await expect(
      page
        .locator('.p-dropdown-panel')
        .last()
        .getByRole('option', { name: /Pack 6/ }),
    ).toHaveCount(0);
    await page.keyboard.press('Escape');
    const refused = await request.post('/api/v1/pos/checkout', {
      headers: bearer(world.token),
      data: {
        site_id: world.main.id,
        lines: [{ article_id: world.beer, packaging_id: pack?.id, quantity: '1' }],
        payments: [],
        customer_id: null,
        idempotency_key: crypto.randomUUID(),
      },
    });
    expect(refused.status()).toBe(422);
    expect(((await refused.json()) as { code: string }).code).toBe('packaging_price_not_set');

    // Administrateur : prix fixé sur la fiche → proposé et vendable.
    await page.goto(`/catalog/articles/${world.beer}`);
    await page
      .getByRole('row')
      .filter({ hasText: 'Pack 6' })
      .getByRole('button', { name: 'Modifier' })
      .click();
    const edit = page.getByRole('dialog', { name: /Modifier le conditionnement/ });
    await edit.getByLabel(/^Prix de vente/).fill('3800');
    await edit.getByRole('button', { name: 'Enregistrer' }).click();
    await expect(edit).toBeHidden();
    await expect(page.getByRole('row').filter({ hasText: 'Pack 6' })).toContainText(/3\s800/);
    await page.goto('/pos');
    const siteChoice = page.locator('.sm-pos-header .p-dropdown');
    if (await siteChoice.count()) {
      await siteChoice.click();
      await page
        .locator('.p-dropdown-panel')
        .last()
        .getByText(world.main.name, { exact: true })
        .click();
    }
    const priced = await addTile(page, `Bière ${RUN}`, `BIERE-${RUN}`);
    await expect(priced).toContainText('+ 2 conditionnements');
    await choosePresentation(page, `Bière ${RUN}`, /Pack 6/);
    await expect(page.getByTestId('pos-total')).toHaveText(/3\s800/);
  });
});
