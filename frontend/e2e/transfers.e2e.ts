import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import {
  adminCli,
  apiToken,
  bearer,
  DOWNGRADE_OWNER,
  loginUi,
  OWNER,
  STANDARD_OWNER,
  tenantOf,
} from './support';

/**
 * Phase 2.5 — Transferts inter-sites : parcours complet avec le vrai backend (StockService,
 * RLS, RBAC, fonctionnalité de plan `stock.transfers`). Chaque test crée son propre article
 * et son stock (suffixe unique) ; le site destination « Dépôt E2E » est créé au besoin.
 */

const DESTINATION = { name: 'Dépôt E2E', code: 'DEPOT-E2E' };

interface Setup {
  token: string;
  reference: string;
  articleId: string;
  sourceId: string;
  sourceName: string;
  destinationId: string;
}

interface Site {
  id: string;
  name: string;
  code: string;
}

/** Article : 100 u sur le site principal (coût 1 000), 20 u sur le dépôt (coût 2 000). */
async function setup(request: APIRequestContext, account = OWNER): Promise<Setup> {
  const token = await apiToken(request, account.email, account.password);
  const headers = bearer(token);
  const post = async (path: string, data: unknown, status = 201) => {
    const response = await request.post(`/api/v1${path}`, { headers, data });
    expect(response.status(), await response.text()).toBe(status);
    return (await response.json()) as { id: string };
  };
  const sites = (await (await request.get('/api/v1/sites', { headers })).json()) as Site[];
  const source = sites.find((s) => s.code !== DESTINATION.code) as Site;
  const destination =
    sites.find((s) => s.code === DESTINATION.code) ??
    ((await post('/sites', { ...DESTINATION, kind: 'warehouse' })) as Site);
  const suffix = Date.now().toString().slice(-7);
  const reference = `E2E-T${suffix}`;
  const category = await post('/catalog/categories', { name: `Transferts E2E ${suffix}` });
  const article = await post('/catalog/articles', {
    reference,
    designation: `Riz 25 kg E2E ${suffix}`,
    category_id: category.id,
    unit: 'u',
    purchase_price: '1000',
    sale_price: '1500',
  });
  for (const [siteId, quantity, cost] of [
    [source.id, '100', '1000'],
    [destination.id, '20', '2000'],
  ] as const) {
    const entry = await post('/stock/entries', {
      site_id: siteId,
      kind: 'INITIAL_STOCK',
      lines: [{ article_id: article.id, quantity, unit_cost: cost }],
    });
    await post(`/stock/entries/${entry.id}/validate`, {}, 200);
  }
  return {
    token,
    reference,
    articleId: article.id,
    sourceId: source.id,
    sourceName: source.name,
    destinationId: destination.id,
  };
}

async function levels(request: APIRequestContext, s: Setup) {
  const response = await request.get(`/api/v1/stock/levels?article_id=${s.articleId}`, {
    headers: bearer(s.token),
  });
  const page = (await response.json()) as {
    items: { site_id: string; quantity: string; average_cost: string }[];
  };
  const bySite = new Map(page.items.map((l) => [l.site_id, l]));
  return { source: bySite.get(s.sourceId), destination: bySite.get(s.destinationId) };
}

async function choose(page: Page, inputId: string, label: string) {
  await page.locator('.p-dropdown', { has: page.locator(`#${inputId}`) }).click();
  // Dernier panneau ouvert : celui d'une liste précédente peut encore se refermer.
  await page
    .locator('.p-dropdown-panel')
    .last()
    .locator('.p-dropdown-item', { hasText: label })
    .click();
}

/** Saisie d'un transfert site principal → dépôt ; renvoie son numéro. */
async function enterTransfer(page: Page, s: Setup, quantity: string) {
  await page.getByRole('link', { name: 'Transferts' }).click();
  await expect(page.getByRole('heading', { name: 'Transferts inter-sites' })).toBeVisible();
  await page.getByRole('button', { name: 'Nouveau transfert' }).click();
  await expect(page.getByRole('heading', { name: 'Nouveau transfert' })).toBeVisible();
  await choose(page, 'transfer-source', s.sourceName);
  await choose(page, 'transfer-destination', DESTINATION.name);
  await page.getByRole('button', { name: 'Ajouter une ligne' }).click();
  await page.locator('#line-0-article').fill(s.reference);
  await page
    .getByRole('option', { name: new RegExp(s.reference) })
    .first()
    .click();
  await page.locator('#line-0-quantity').fill(quantity);
  // Stock disponible du site source, indicatif.
  await expect(page.getByTestId('available-0')).toHaveText(/Stock disponible : 100 u/);
  await page.getByRole('button', { name: 'Enregistrer le brouillon' }).click();
  await expect(page.getByText('Brouillon enregistré')).toBeVisible();
  const heading = page.getByRole('heading', { name: /^Transfert TRF-\d{6}$/ });
  await expect(heading).toBeVisible();
  return ((await heading.innerText()).match(/TRF-\d{6}/) ?? [''])[0];
}

async function confirmValidation(page: Page) {
  await page.getByRole('button', { name: 'Valider le transfert' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Valider le transfert' }).click();
}

test.describe('Transferts inter-sites', () => {
  test('transfert complet : stock des deux sites, CMUP, mouvements, audit', async ({
    page,
    request,
  }) => {
    const s = await setup(request);
    await loginUi(page, OWNER.email, OWNER.password);

    const number = await enterTransfer(page, s, '30');
    const draft = await levels(request, s);
    expect([draft.source?.quantity, draft.destination?.quantity]).toEqual(['100.000', '20.000']);

    await confirmValidation(page);
    await expect(page.getByText(`Transfert ${number} validé`)).toBeVisible();
    await expect(page.getByText('Validé', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Enregistrer le brouillon' })).toHaveCount(0);

    // Site A : 100 − 30 = 70 (CMUP 1 000 inchangé) ; site B : 20 + 30 = 50,
    // CMUP (20 × 2 000 + 30 × 1 000) / 50 = 1 400.
    const after = await levels(request, s);
    expect([after.source?.quantity, after.source?.average_cost]).toEqual(['70.000', '1000.0000']);
    expect([after.destination?.quantity, after.destination?.average_cost]).toEqual([
      '50.000',
      '1400.0000',
    ]);
    await page.getByRole('link', { name: 'Stock par site' }).click();
    await page.getByRole('searchbox').fill(s.reference);
    const rows = page.getByRole('row').filter({ hasText: s.reference });
    await expect(rows.filter({ hasText: DESTINATION.name })).toContainText('50');
    await expect(rows.filter({ hasText: s.sourceName })).toContainText('70');

    // Journal : sortie du site source et entrée du dépôt, rattachées au transfert.
    await page.getByRole('link', { name: 'Mouvements' }).click();
    const movements = page.getByRole('row').filter({ hasText: number });
    await expect(movements.filter({ hasText: 'Transfert sortant' })).toContainText('-30');
    await expect(movements.filter({ hasText: 'Transfert entrant' })).toContainText(
      DESTINATION.name,
    );

    // Audit.
    await page.getByRole('link', { name: "Journal d'audit" }).click();
    for (const action of ['stock_transfer.validated', 'stock_transfer.created']) {
      await expect(
        page.getByRole('row').filter({ hasText: action }).filter({ hasText: number }).first(),
      ).toBeVisible();
    }
  });

  test('stock insuffisant : validation refusée, rien ne bouge', async ({ page, request }) => {
    const s = await setup(request);
    await loginUi(page, OWNER.email, OWNER.password);
    await enterTransfer(page, s, '150');
    await expect(page.getByTestId('available-0')).toHaveClass(/p-error/);
    await confirmValidation(page);
    await expect(page.getByText(/Stock insuffisant : .*disponible : 100/)).toBeVisible();
    await expect(page.getByText('Brouillon', { exact: true })).toBeVisible();
    const after = await levels(request, s);
    expect([after.source?.quantity, after.destination?.quantity]).toEqual(['100.000', '20.000']);
  });

  test('plan STANDARD : consultation seule, aucune opération', async ({ page, request }) => {
    await loginUi(page, STANDARD_OWNER.email, STANDARD_OWNER.password, STANDARD_OWNER.tenant);
    await page.getByRole('link', { name: 'Transferts' }).click();
    await expect(page.getByRole('heading', { name: 'Transferts inter-sites' })).toBeVisible();
    await expect(page.getByText(/Consultation seule/)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Nouveau transfert' })).toHaveCount(0);
    // Le backend refuse toute opération, quel que soit l'écran.
    const token = await apiToken(request, STANDARD_OWNER.email, STANDARD_OWNER.password);
    const list = await request.get('/api/v1/stock/transfers', { headers: bearer(token) });
    expect(list.status()).toBe(200);
    const created = await request.post('/api/v1/stock/transfers', {
      headers: bearer(token),
      data: { destination_site_id: crypto.randomUUID(), lines: [] },
    });
    expect(created.status()).toBe(403);
    expect(((await created.json()) as { code: string }).code).toBe('feature_unavailable');
  });

  test('rétrogradation ENTREPRISE → STANDARD : historique conservé, lecture seule', async ({
    page,
    request,
  }) => {
    const s = await setup(request, DOWNGRADE_OWNER);
    const tenant = tenantOf(s.token);
    const headers = bearer(s.token);
    const create = async (quantity: string) => {
      const response = await request.post('/api/v1/stock/transfers', {
        headers,
        data: {
          source_site_id: s.sourceId,
          destination_site_id: s.destinationId,
          lines: [{ article_id: s.articleId, quantity }],
        },
      });
      expect(response.status(), await response.text()).toBe(201);
      return (await response.json()) as { id: string; number: string };
    };
    adminCli('change-plan', '--tenant-id', tenant, '--plan', 'ENTREPRISE');
    const validated = await create('30');
    const validation = await request.post(`/api/v1/stock/transfers/${validated.id}/validate`, {
      headers,
    });
    expect(validation.status()).toBe(200);
    const draft = await create('5');
    try {
      expect(adminCli('change-plan', '--tenant-id', tenant, '--plan', 'STANDARD')).toContain(
        'ENTREPRISE → STANDARD',
      );

      // Historique consultable dans l'interface, en lecture seule.
      await loginUi(page, DOWNGRADE_OWNER.email, DOWNGRADE_OWNER.password, DOWNGRADE_OWNER.tenant);
      await page.getByRole('link', { name: 'Transferts' }).click();
      await expect(page.getByText(/Consultation seule/)).toBeVisible();
      await expect(page.getByRole('button', { name: 'Nouveau transfert' })).toHaveCount(0);
      await expect(page.getByRole('row').filter({ hasText: validated.number })).toContainText(
        'Validé',
      );
      await page.goto(`/stock/transfers/${validated.id}`);
      await expect(
        page.getByRole('heading', { name: `Transfert ${validated.number}` }),
      ).toBeVisible();
      await expect(page.getByText(s.reference)).toBeVisible();
      await expect(page.getByRole('button', { name: 'Annuler le transfert' })).toHaveCount(0);
      await page.goto(`/stock/transfers/${draft.id}`);
      await expect(page.getByText('Brouillon', { exact: true })).toBeVisible();
      await expect(page.getByRole('button', { name: 'Valider le transfert' })).toHaveCount(0);
      await expect(page.getByRole('button', { name: 'Enregistrer le brouillon' })).toHaveCount(0);

      // Le backend refuse toute opération ; données et stock intacts.
      const body = {
        destination_site_id: s.destinationId,
        lines: [{ article_id: s.articleId, quantity: '1' }],
      };
      for (const response of [
        await request.post('/api/v1/stock/transfers', {
          headers,
          data: { ...body, source_site_id: s.sourceId },
        }),
        await request.put(`/api/v1/stock/transfers/${draft.id}`, { headers, data: body }),
        await request.post(`/api/v1/stock/transfers/${draft.id}/validate`, { headers }),
        await request.post(`/api/v1/stock/transfers/${validated.id}/cancel`, {
          headers,
          data: { reason: 'Tentative après rétrogradation' },
        }),
      ]) {
        expect(response.status()).toBe(403);
        expect(((await response.json()) as { code: string }).code).toBe('feature_unavailable');
      }
      const after = await levels(request, s);
      expect([after.source?.quantity, after.destination?.quantity]).toEqual(['70.000', '50.000']);
    } finally {
      adminCli('change-plan', '--tenant-id', tenant, '--plan', 'ENTREPRISE');
    }
  });

  test('affichage mobile sans débordement @mobile', async ({ page, request }) => {
    const s = await setup(request);
    await loginUi(page, OWNER.email, OWNER.password);
    await page.goto('/stock/transfers');
    await expect(page.getByRole('heading', { name: 'Transferts inter-sites' })).toBeVisible();
    const overflow = () =>
      page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    expect(await overflow()).toBe(false);
    await page.getByRole('button', { name: 'Nouveau transfert' }).click();
    await choose(page, 'transfer-source', s.sourceName);
    await page.getByRole('button', { name: 'Ajouter une ligne' }).click();
    await page.locator('#line-0-article').fill(s.reference);
    await page
      .getByRole('option', { name: new RegExp(s.reference) })
      .first()
      .click();
    await expect(page.getByTestId('available-0')).toBeVisible();
    expect(await overflow()).toBe(false);
  });
});
