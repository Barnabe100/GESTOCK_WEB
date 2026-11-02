import { expect, test } from '@playwright/test';

import { call } from './restaurant-support';
import { assortArticles, loginUi, provisionTenant, siteIdsOf, tokenFor } from './support';

/**
 * Palier R10 (ADR-0055) — rapports, parcours réels : ventes validées (dont une remisée) créées
 * par l'API, indicateurs calculés par le serveur, répartition par article, export téléchargé ;
 * ordinateur et mobile (aucun débordement horizontal).
 */

const PASSWORD = 'E2e-Rapports-2026';
let tenant: string;
let email: string;
let reference: string;

test.describe.configure({ mode: 'serial' });

test.beforeAll(async ({ request }) => {
  const suffix = Date.now().toString().slice(-7);
  tenant = `Rapports ${suffix}`;
  email = `e2e-rapports-${suffix}@example.com`;
  await provisionTenant(request, {
    name: tenant,
    profile: 'retail.quincaillerie',
    email,
    password: PASSWORD,
  });
  const token = await tokenFor(request, email, PASSWORD, tenant);
  const [site] = await siteIdsOf(request, token);
  const letters = suffix.replace(/\d/g, (d) => 'ABCDEFGHIJ'.charAt(Number(d)));
  reference = `CIM-${letters}`;
  const category = await call(request, token, 'post', '/catalog/categories', {
    name: `Matériaux ${suffix}`,
  });
  const article = await call(request, token, 'post', '/catalog/articles', {
    reference,
    designation: `Ciment ${letters}`,
    category_id: category.id,
    unit: 'sac',
    purchase_price: '4000',
    sale_price: '5000',
  });
  await assortArticles(request, token, [article.id]);
  const entry = await call(request, token, 'post', '/stock/entries', {
    site_id: site,
    kind: 'INITIAL_STOCK',
    lines: [{ article_id: article.id, quantity: '50', unit_cost: '4000' }],
  });
  await call(request, token, 'post', `/stock/entries/${entry.id}/validate`, {});
  // 2 × 5 000 = 10 000, remise 1 000 → 9 000 ; puis 1 × 5 000. CA 14 000, coût 12 000.
  for (const body of [
    { quantity: '2', discount: { mode: 'AMOUNT', value: '1000', reason: 'Client fidèle' } },
    { quantity: '1' },
  ]) {
    const sale = await call<{ id: string; total: string }>(request, token, 'post', '/sales', {
      site_id: site,
      lines: [{ article_id: article.id, quantity: body.quantity }],
      ...(body.discount ? { discount: body.discount } : {}),
    });
    await call(request, token, 'post', `/sales/${sale.id}/validate`, {
      payments: [{ amount: sale.total, method: 'CASH' }],
    });
  }
});

test('indicateurs, répartition par article et export', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginUi(page, email, PASSWORD, tenant);
  await page.goto('/reports');
  await expect(page.getByRole('heading', { name: 'Rapports', exact: true })).toBeVisible();
  const metrics = page.getByLabel('Indicateurs de la période');
  await expect(metrics).toContainText('14');
  await expect(metrics).toContainText('2 ventes');
  await expect(metrics).toContainText('Taux de marge : 14.29 %');
  await expect(page.getByText(`${reference} — Ciment`)).toBeVisible();
  const download = page.waitForEvent('download');
  await page
    .getByRole('button', { name: /Exporter/ })
    .first()
    .click();
  await page.getByRole('menuitem', { name: /CSV/ }).click();
  const file = await download;
  expect(file.suggestedFilename()).toMatch(/^rapport-ventes-.*\.csv$/);
});

test('@mobile rapports lisibles sans débordement', async ({ page }) => {
  await loginUi(page, email, PASSWORD, tenant);
  await page.goto('/reports');
  await expect(page.getByLabel('Indicateurs de la période')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});
