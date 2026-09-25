import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import { apiToken, bearer, loginUi, OWNER } from './support';

/**
 * Phase 2.9 — Caisse. Les tests travaillent sur le site « Dépôt E2E » (créé au besoin) pour ne
 * pas interférer avec la caisse de la boutique des autres suites ; les sessions du dépôt
 * laissées ouvertes par une exécution interrompue sont d'abord clôturées. Données suffixées.
 */

interface Site {
  id: string;
  code: string;
}

interface Setup {
  token: string;
  suffix: string;
  articleId: string;
  depot: string;
}

const DEPOT = { name: 'Dépôt E2E', code: 'DEPOT-E2E' };

async function call(
  request: APIRequestContext,
  token: string,
  method: 'get' | 'post',
  path: string,
  data?: unknown,
) {
  const response = await request[method](`/api/v1${path}`, { headers: bearer(token), data });
  return { status: response.status(), body: (await response.json()) as Record<string, unknown> };
}

async function ok(request: APIRequestContext, token: string, path: string, data: unknown) {
  const { status, body } = await call(request, token, 'post', path, data);
  expect(status, JSON.stringify(body)).toBeLessThan(300);
  return body as { id: string; number: string };
}

async function setup(request: APIRequestContext): Promise<Setup> {
  const token = await apiToken(request, OWNER.email, OWNER.password);
  const sites = (await (
    await request.get('/api/v1/sites', { headers: bearer(token) })
  ).json()) as Site[];
  const depot =
    sites.find((s) => s.code === DEPOT.code) ??
    ((await ok(request, token, '/sites', { ...DEPOT, kind: 'warehouse' })) as unknown as Site);
  // Sessions du dépôt restées ouvertes (exécution interrompue) : clôturées.
  const open = (await call(request, token, 'get', `/cash/sessions?status=OPEN&site_id=${depot.id}`))
    .body as { items: { id: string; theoretical_balance: string }[] };
  for (const s of open.items) {
    await ok(request, token, `/cash/sessions/${s.id}/close`, {
      counted_balance: s.theoretical_balance,
    });
  }
  const suffix = Date.now().toString().slice(-7);
  const category = await ok(request, token, '/catalog/categories', {
    name: `Caisse E2E ${suffix}`,
  });
  const article = await ok(request, token, '/catalog/articles', {
    reference: `E2E-C${suffix}`,
    designation: `Fer à béton E2E ${suffix}`,
    category_id: category.id,
    unit: 'barre',
    purchase_price: '6000',
    sale_price: '10000',
  });
  const entry = await ok(request, token, '/stock/entries', {
    site_id: depot.id,
    kind: 'INITIAL_STOCK',
    lines: [{ article_id: article.id, quantity: '50', unit_cost: '6000' }],
  });
  await ok(request, token, `/stock/entries/${entry.id}/validate`, {});
  return { token, suffix, articleId: article.id, depot: depot.id };
}

/** Vente du dépôt de `units` × 10 000, brouillon ou validée. */
async function sale(request: APIRequestContext, s: Setup, units: number, validate = true) {
  const created = await ok(request, s.token, '/sales', {
    site_id: s.depot,
    lines: [{ article_id: s.articleId, quantity: String(units) }],
  });
  if (validate) await ok(request, s.token, `/sales/${created.id}/validate`, {});
  return created;
}

/** Montant affiché (espaces insécables du format XOF). */
const amount = (text: string) =>
  new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/ /g, '\\s'));

async function pickOption(page: Page, inputId: string, label: string) {
  await page.locator('.p-dropdown', { has: page.locator(`#${inputId}`) }).click();
  await page.locator('.p-dropdown-panel').last().getByText(label, { exact: true }).click();
}

const summary = (page: Page) => page.getByRole('group', { name: 'Résumé de la session' });

test.describe('Caisse', () => {
  test('cycle complet : caisse, ouverture, vente en espèces, entrée, sortie, clôture, écart', async ({
    page,
    request,
  }) => {
    const s = await setup(request);
    const name = `Caisse E2E ${s.suffix}`;
    await loginUi(page, OWNER.email, OWNER.password);

    // 1. Création de la caisse (site du dépôt).
    await page.getByRole('link', { name: 'Caisses', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Caisses' })).toBeVisible();
    await page.getByRole('button', { name: 'Nouvelle caisse' }).first().click();
    const create = page.getByRole('dialog');
    await pickOption(page, 'register-site', DEPOT.name);
    await create.getByLabel(/Nom de la caisse/).fill(name);
    await create.getByRole('button', { name: 'Enregistrer' }).click();
    await expect(page.getByText(/Caisse CAI-\d{3} créée/)).toBeVisible();

    // 2. Ouverture avec un fond initial de 100 000.
    await page.getByRole('searchbox').fill(name);
    const row = page.getByRole('row').filter({ hasText: name });
    await expect(row).toContainText('Fermée');
    await row.getByRole('button', { name: 'Ouvrir la caisse' }).click();
    const opening = page.getByRole('dialog');
    await expect(opening).toContainText(DEPOT.name);
    await opening.getByLabel(/Fond initial/).fill('100000');
    await opening.getByRole('button', { name: 'Ouvrir la caisse' }).click();
    await expect(page.getByText(/Session SES-\d{6} ouverte/)).toBeVisible();
    await expect(summary(page)).toContainText(amount('100 000'));
    const sessionUrl = page.url();

    // 3-4. Vente en espèces encaissée à la validation, dans cette caisse.
    const draft = await sale(request, s, 5, false);
    await page.goto(`/sales/${draft.id}`);
    await page.getByRole('button', { name: 'Valider la vente' }).click();
    const validate = page.getByRole('dialog');
    await validate.getByLabel('Encaisser un paiement maintenant').check();
    await expect(validate).toContainText(`Encaissé dans : ${name}`);
    await validate.getByRole('button', { name: 'Valider la vente' }).click();
    await expect(page.getByText(`Vente ${draft.number} validée`)).toBeVisible();
    await expect(page.getByText('Payée', { exact: true }).first()).toBeVisible();
    const payment = page.getByRole('row').filter({ hasText: /PAY-\d{6}/ });
    await expect(payment).toContainText('Espèces');
    await expect(payment).toContainText('Effectué');

    // 5-6. Mouvement de caisse lié à la vente ; solde 150 000.
    await page.goto(sessionUrl);
    const cashIn = page.getByRole('row').filter({ hasText: 'Encaissement vente' });
    await expect(cashIn.getByRole('link', { name: new RegExp(draft.number) })).toBeVisible();
    await expect(summary(page).locator('.sm-metric').nth(3)).toContainText(amount('150 000'));

    // 7. Entrée manuelle de 10 000.
    await page.getByRole('button', { name: 'Entrée de caisse' }).click();
    let dialog = page.getByRole('dialog');
    await dialog.getByLabel(/^Montant/).fill('10000');
    await dialog.getByLabel(/^Motif/).fill('Apport de monnaie');
    await dialog.getByRole('button', { name: 'Entrée de caisse' }).click();
    await expect(page.getByText(/Entrée de .* enregistrée/)).toBeVisible();

    // 8. Sortie de 30 000 (dépense).
    await page.getByRole('button', { name: 'Sortie de caisse' }).click();
    dialog = page.getByRole('dialog');
    await dialog.getByLabel(/^Montant/).fill('30000');
    await dialog.getByLabel(/^Motif/).fill('Carburant groupe électrogène');
    await dialog.getByRole('button', { name: 'Sortie de caisse' }).click();
    await expect(page.getByText(/Sortie de .* enregistrée/)).toBeVisible();
    await expect(page.getByRole('row').filter({ hasText: 'Carburant' })).toContainText('Dépense');

    // 9-12. Clôture : théorique 130 000, compté 128 500, écart −1 500.
    await page.getByRole('button', { name: 'Clôturer la caisse' }).click();
    dialog = page.getByRole('dialog');
    await expect(dialog.getByTestId('close-theoretical')).toHaveText(amount('130 000'));
    await dialog.getByLabel(/Montant compté/).fill('128500');
    await expect(dialog.getByTestId('close-variance')).toContainText(amount('-1 500'));
    await expect(dialog.getByTestId('close-variance')).toContainText('Manquant');
    await dialog.getByLabel(/Je confirme/).check();
    await dialog.getByRole('button', { name: 'Clôturer la caisse' }).click();
    await expect(page.getByText(/Session SES-\d{6} clôturée/)).toBeVisible();
    await expect(page.getByText('Fermée', { exact: true })).toBeVisible();
    await expect(page.getByText('Manquant').first()).toBeVisible();

    // 13. Session fermée : plus aucune action, mouvements refusés par le serveur.
    await expect(page.getByRole('button', { name: 'Entrée de caisse' })).toHaveCount(0);
    const sessionId = sessionUrl.split('/').pop() ?? '';
    const late = await call(request, s.token, 'post', `/cash/sessions/${sessionId}/movements`, {
      movement_type: 'MANUAL_CASH_IN',
      amount: '1000',
      category: 'OTHER',
      reason: 'Après clôture',
    });
    expect(late.status).toBe(409);
    expect(late.body.code).toBe('cash_session_closed');
  });

  test('paiement en espèces sans caisse ouverte refusé ; idempotence', async ({
    page,
    request,
  }) => {
    const s = await setup(request);
    const unpaid = await sale(request, s, 3);
    await loginUi(page, OWNER.email, OWNER.password);
    // 14. Aucune caisse ouverte au dépôt : avertissement, puis refus du serveur.
    await page.goto(`/sales/${unpaid.id}`);
    await page.getByRole('button', { name: 'Enregistrer un paiement' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText('Aucune caisse ouverte sur le site de la vente');
    await dialog.getByRole('button', { name: 'Enregistrer le paiement' }).click();
    await expect(dialog.getByText(/ouvrez une session de caisse/).last()).toBeVisible();
    await dialog.getByRole('button', { name: 'Annuler' }).click();
    await expect(page.getByRole('row').filter({ hasText: /PAY-\d{6}/ })).toHaveCount(0);
    const api = await call(request, s.token, 'post', `/sales/${unpaid.id}/payments`, {
      amount: '10000',
      method: 'CASH',
    });
    expect(api.status).toBe(422);
    expect(api.body.code).toBe('cash_session_required');

    // 15. Même clé d'idempotence : un seul paiement, un seul mouvement de caisse.
    const register = await ok(request, s.token, '/cash/registers', {
      site_id: s.depot,
      name: `Caisse idempotence ${s.suffix}`,
    });
    const session = await ok(request, s.token, '/cash/sessions', {
      cash_register_id: register.id,
      opening_float: '0',
    });
    const key = crypto.randomUUID();
    const body = { amount: '10000', method: 'CASH', idempotency_key: key };
    const first = await call(request, s.token, 'post', `/sales/${unpaid.id}/payments`, body);
    const replay = await call(request, s.token, 'post', `/sales/${unpaid.id}/payments`, body);
    expect([first.status, replay.status]).toEqual([201, 200]);
    expect(replay.body.id).toBe(first.body.id);
    const journal = (await call(request, s.token, 'get', `/cash/sessions/${session.id}/movements`))
      .body as { items: { payment_id: string | null }[] };
    expect(journal.items.filter((m) => m.payment_id === first.body.id)).toHaveLength(1);
    await ok(request, s.token, `/cash/sessions/${session.id}/close`, { counted_balance: '10000' });
  });

  test('caisses et session sur mobile, sans débordement @mobile', async ({ page, request }) => {
    const s = await setup(request);
    const register = await ok(request, s.token, '/cash/registers', {
      site_id: s.depot,
      name: `Caisse mobile ${s.suffix}`,
    });
    const session = await ok(request, s.token, '/cash/sessions', {
      cash_register_id: register.id,
      opening_float: '5000',
    });
    await loginUi(page, OWNER.email, OWNER.password);
    const overflow = () =>
      page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    await page.goto('/cash/registers');
    await expect(page.getByRole('heading', { name: 'Caisses' })).toBeVisible();
    expect(await overflow()).toBe(false);
    await page.goto(`/cash/sessions/${session.id}`);
    await expect(summary(page)).toContainText(amount('5 000'));
    expect(await overflow()).toBe(false);
    await page.getByRole('button', { name: 'Clôturer la caisse' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel(/Montant compté/).fill('5000');
    await dialog.getByLabel(/Je confirme/).check();
    await dialog.getByRole('button', { name: 'Clôturer la caisse' }).click();
    await expect(page.getByText('Fermée', { exact: true })).toBeVisible();
    expect(await overflow()).toBe(false);
  });
});
