import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import { bearer, createActiveSite, loginUi, provisionTenant, tokenFor } from './support';

/**
 * Lot 3-D (ADR-0042), sur une entreprise créée pour l'exécution : codes-barres multiples d'un
 * article et codes de ses conditionnements (fiche article), unicité commune refusée, scan EXACT
 * au point de vente (code de l'article → unité de base, code du carton → 1 Carton 24, code
 * inconnu refusé, conditionnement sans prix refusé), scan dans un inventaire (article et
 * présentation présélectionnés, quantité saisie), dans une entrée et un transfert (présentation
 * présélectionnée, stock en unité de base), isolation entre entreprises.
 */

const RUN = Date.now().toString().slice(-7);
const TENANT = `Codes-barres E2E ${RUN}`;
const EMAIL = `codes-barres-${RUN}@example.com`;
const PASSWORD = 'E2e-Codes-Barres-2026';
const OTHER_TENANT = `Autre codes E2E ${RUN}`;
const OTHER_EMAIL = `autre-codes-${RUN}@example.com`;
const REFERENCE = `COCA3D-${RUN}`;
const DESIGNATION = `Coca 3D ${RUN}`;
const PRIMARY = `PRI${RUN}`;
const BOTTLE_2 = `BTL2${RUN}`;
const BOTTLE_3 = `BTL3${RUN}`;
const CARTON_1 = `CTN1${RUN}`;
const CARTON_2 = `CTN2${RUN}`;
const BUNDLE = `FAR${RUN}`;
const DEPOT = { name: `Dépôt 3D ${RUN}`, code: `D3D${RUN.slice(-4)}` };

interface Site {
  id: string;
  name: string;
}

interface World {
  token: string;
  main: Site;
  depot: Site;
  coca: string;
  carton: string;
  bundle: string;
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

async function scan(page: Page, code: string) {
  const field = page.getByLabel('Scanner un code-barres');
  await field.fill(code);
  await field.press('Enter');
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

async function posScan(page: Page, code: string) {
  const search = page.getByLabel('Rechercher un article (F2)');
  await search.fill(code);
  await search.press('Enter');
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
  const category = (await post(request, '/catalog/categories', { name: `Boissons 3D ${RUN}` })).id;
  world.coca = (
    await post(request, '/catalog/articles', {
      reference: REFERENCE,
      designation: DESIGNATION,
      category_id: category,
      unit: 'bouteille',
      sale_price: '500',
      barcode: PRIMARY,
    })
  ).id;
  world.carton = (
    await post(request, `/catalog/articles/${world.coca}/packagings`, {
      name: 'Carton 24',
      conversion: '24',
      sale_price: '10500',
    })
  ).id;
  // Conditionnement au prix non configuré : invendable, mais utilisable en stock.
  world.bundle = (
    await post(request, `/catalog/articles/${world.coca}/packagings`, {
      name: 'Fardeau 12',
      conversion: '12',
    })
  ).id;
  await post(request, `/catalog/packagings/${world.bundle}/barcodes`, { code: BUNDLE });
  const entry = await post(request, '/stock/entries', {
    site_id: world.main.id,
    kind: 'INITIAL_STOCK',
    lines: [{ article_id: world.coca, quantity: '100', unit_cost: '400' }],
  });
  await post(request, `/stock/entries/${entry.id}/validate`, {});
});

test.describe('Codes-barres — Lot 3-D', () => {
  test('1-2 — plusieurs codes pour l’article et pour le carton, unicité commune', async ({
    page,
  }) => {
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto(`/catalog/articles/${world.coca}`);
    const section = page.getByRole('region', { name: 'Codes-barres' });
    await expect(section.getByText(PRIMARY)).toBeVisible();
    for (const code of [BOTTLE_2, BOTTLE_3]) {
      await page.getByLabel("Nouveau code-barres pour l'unité de base (bouteille)").fill(code);
      await page
        .getByRole('button', { name: "Ajouter le code-barres à l'unité de base (bouteille)" })
        .click();
      await expect(section.getByText(code)).toBeVisible();
    }
    for (const code of [CARTON_1, CARTON_2]) {
      await page.getByLabel('Nouveau code-barres pour Carton 24').fill(code);
      await page.getByRole('button', { name: 'Ajouter le code-barres à Carton 24' }).click();
      await expect(page.getByRole('group', { name: 'Carton 24' }).getByText(code)).toBeVisible();
    }
    // Un code ne désigne qu'une présentation : refusé sur le fardeau.
    await page.getByLabel('Nouveau code-barres pour Fardeau 12').fill(CARTON_1);
    await page.getByRole('button', { name: 'Ajouter le code-barres à Fardeau 12' }).click();
    await expect(
      page.getByText(/Ce code-barres est déjà utilisé par un élément actif/),
    ).toBeVisible();
    await expect(page.getByRole('group', { name: 'Fardeau 12' }).getByText(CARTON_1)).toHaveCount(
      0,
    );
    // Recherche partielle du catalogue par un code de conditionnement.
    await page.goto('/catalog/articles');
    await page.getByLabel('Rechercher…').fill(CARTON_2.slice(0, -2));
    await expect(page.getByRole('row').filter({ hasText: REFERENCE })).toBeVisible();
  });

  test('3-4, 7 — POS : article en unité de base, 1 Carton 24, codes inconnus refusés', async ({
    page,
  }) => {
    await openPos(page);
    await posScan(page, BOTTLE_2);
    await expect(page.getByLabel(`Quantité de ${DESIGNATION}`, { exact: true })).toHaveValue('1');
    await posScan(page, CARTON_2);
    await expect(page.getByLabel(`Quantité de ${DESIGNATION} (Carton 24)`)).toHaveValue('1');
    await posScan(page, CARTON_1);
    await expect(page.getByLabel(`Quantité de ${DESIGNATION} (Carton 24)`)).toHaveValue('2');
    // Inconnu, fragment de code, conditionnement au prix non configuré : rien n'est ajouté.
    await posScan(page, `${CARTON_1}9`);
    await expect(page.getByRole('alert')).toHaveText('Code-barres inconnu');
    await posScan(page, BUNDLE);
    await expect(page.getByRole('alert')).toContainText('(Fardeau 12)');
    await expect(page.getByRole('list', { name: 'Panier' }).getByRole('listitem')).toHaveCount(2);
  });

  test('5 — inventaire : le scan du carton présélectionne la présentation, quantité saisie', async ({
    page,
    request,
  }) => {
    const draft = await post(request, '/inventories', {
      site_id: world.main.id,
      inventory_type: 'TARGETED',
      article_ids: [world.coca],
    });
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto(`/inventories/${draft.id}`);
    await page.getByRole('button', { name: 'Démarrer le comptage' }).click();
    await expect(page.getByText('Comptage en cours', { exact: true })).toBeVisible();
    await scan(page, CARTON_1);
    const cartons = page.getByLabel(`Nombre de Carton 24 comptés pour ${REFERENCE}`);
    await expect(cartons).toHaveValue('');
    await expect(cartons).toBeFocused();
    await cartons.fill('4');
    const loose = page.getByLabel(`bouteille en vrac pour ${REFERENCE}`);
    await loose.fill('4');
    await loose.blur();
    await expect(page.getByText('Articles comptés : 1 / 1')).toBeVisible();
    const lines = (await (
      await request.get(`/api/v1/inventories/${draft.id}/lines`, { headers: bearer(world.token) })
    ).json()) as { items: { quantity_physical: string }[] };
    expect(lines.items[0]?.quantity_physical).toBe('100.000');
    await request.post(`/api/v1/inventories/${draft.id}/cancel`, {
      headers: bearer(world.token),
      data: { reason: 'Scénario E2E terminé' },
    });
  });

  test('6 — entrée et transfert : présentation présélectionnée par le scan', async ({
    page,
    request,
  }) => {
    await loginUi(page, EMAIL, PASSWORD, TENANT);
    await page.goto('/stock/entries/new');
    await expect(page.getByRole('heading', { name: 'Nouvelle entrée' })).toBeVisible();
    if (await page.locator('#doc-site').isVisible())
      await choose(page, 'doc-site', world.main.name);
    await choose(page, 'doc-kind', 'Stock initial');
    await scan(page, CARTON_2);
    await expect(page.getByRole('status')).toContainText('(Carton 24)');
    await expect(page.getByTestId('presentation-equivalence')).toHaveCount(0); // quantité vide
    await page.getByLabel(/^Quantité \(Carton 24\)/).fill('2');
    await page.getByLabel(/^Coût unitaire \(par Carton 24\)/).fill('9600');
    await expect(page.getByTestId('presentation-equivalence')).toHaveText(
      '2 Carton 24 = 48 bouteille',
    );
    await page.getByRole('button', { name: 'Valider', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Valider' }).click();
    await expect(page.getByText(/Document ENT-\d{6} validé/)).toBeVisible();
    expect(await level(request, world.main)).toBe('148.000');

    await page.goto('/stock/transfers/new');
    await choose(page, 'transfer-source', world.main.name);
    await choose(page, 'transfer-destination', world.depot.name);
    // Le fardeau (prix non configuré) reste utilisable en stock.
    await scan(page, BUNDLE);
    await page.getByLabel(/^Quantité \(Fardeau 12\)/).fill('1');
    await page.getByRole('button', { name: 'Enregistrer le brouillon' }).click();
    await expect(page.getByText('Brouillon enregistré')).toBeVisible();
    await page.getByRole('button', { name: 'Valider le transfert' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Valider le transfert' }).click();
    await expect(page.getByText(/Transfert TRF-\d{6} validé/)).toBeVisible();
    expect(await level(request, world.depot)).toBe('12.000');
    // Code inconnu : message explicite, aucune ligne ajoutée.
    await page.goto('/stock/exits/new');
    await expect(page.getByRole('heading', { name: 'Nouvelle sortie' })).toBeVisible();
    await scan(page, 'INCONNU-3D');
    await expect(page.getByRole('alert')).toHaveText('Code-barres inconnu : INCONNU-3D');
  });

  test('8 — isolation : les codes d’une entreprise sont inconnus des autres', async ({
    request,
  }) => {
    await provisionTenant(request, {
      name: OTHER_TENANT,
      profile: 'retail.alimentation',
      email: OTHER_EMAIL,
      password: PASSWORD,
    });
    const other = await tokenFor(request, OTHER_EMAIL, PASSWORD, OTHER_TENANT);
    for (const code of [PRIMARY, BOTTLE_2, CARTON_1]) {
      const response = await request.get(`/api/v1/catalog/barcodes/resolve?code=${code}`, {
        headers: bearer(other),
      });
      expect(response.status()).toBe(404);
      expect(((await response.json()) as { code: string }).code).toBe('barcode_unknown');
    }
    // Le même code reste libre pour l'autre entreprise.
    const category = (
      await post(request, '/catalog/categories', { name: `Autre 3D ${RUN}` }, other)
    ).id;
    const own = await post(
      request,
      '/catalog/articles',
      {
        reference: `AUTRE3D-${RUN}`,
        designation: `Autre 3D ${RUN}`,
        category_id: category,
        unit: 'pièce',
        sale_price: '100',
        barcode: CARTON_1,
      },
      other,
    );
    const mine = (await (
      await request.get(`/api/v1/catalog/barcodes/resolve?code=${CARTON_1}`, {
        headers: bearer(other),
      })
    ).json()) as { article: { id: string } };
    expect(mine.article.id).toBe(own.id);
  });
});
