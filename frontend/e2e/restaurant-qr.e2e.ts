import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import { call, setupRestaurant, type Resto } from './restaurant-support';
import { loginUi } from './support';

/**
 * Palier R7 (ADR-0052) — commandes par QR Code, parcours réels : QR de table et de comptoir,
 * menu public sans compte, jeton effacé de l'adresse, commande « à confirmer », confirmation et
 * refus par le personnel, suivi public temporaire, régénération (anciens QR invalides),
 * ordinateur et mobile.
 */

const PASSWORD = 'E2e-Qr-Code-2026';
let r: Resto;

test.describe.configure({ mode: 'serial' });

type Issued = { point: { id: string }; url: string; svg: string };

/** Fragment (jeton) de l'adresse émise par le serveur : seule partie utile à la page. */
const fragment = (url: string) => url.slice(url.indexOf('#'));

async function enable(request: APIRequestContext, resto: Resto) {
  for (const code of ['restaurant.tables', 'restaurant.qr']) {
    await call(request, resto.token, 'put', `/sites/${resto.site}/modules/${code}`, {
      enabled: true,
    });
  }
}

async function tablePoint(request: APIRequestContext, resto: Resto, label: string) {
  const table = await call(request, resto.token, 'post', '/restaurant/tables', {
    site_id: resto.site,
    label,
  });
  return call<Issued>(request, resto.token, 'post', '/restaurant/qr/points', {
    site_id: resto.site,
    kind: 'TABLE',
    table_id: table.id,
  });
}

async function order(page: Page, quantity: number) {
  for (let i = 0; i < quantity; i += 1) {
    await page.getByRole('button', { name: `Ajouter un ${r.itemLabel}` }).click();
  }
  await page.getByRole('button', { name: 'Envoyer la commande' }).click();
  await expect(page).toHaveURL(/\/q\/track$/);
}

const overflow = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);

test.beforeAll(async ({ request }) => {
  r = await setupRestaurant(request, 'Maquis QR', PASSWORD);
  await enable(request, r);
});

test('table : menu public, commande sans compte, confirmation puis suivi', async ({
  page,
  browser,
  request,
}) => {
  const issued = await tablePoint(request, r, 'T7');
  await page.goto(`/q${fragment(issued.url)}`);
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(r.name);
  await expect(page.getByText('Table T7')).toBeVisible();
  // Jeton retiré de la barre d'adresse dès l'ouverture.
  await expect.poll(() => page.evaluate(() => window.location.hash)).toBe('');
  await order(page, 2);
  await expect(page.getByTestId('qr-order-status')).toHaveText(
    "En attente de confirmation par l'établissement",
  );
  await expect.poll(() => page.evaluate(() => window.location.hash)).toBe('');
  const number = (await page.getByTestId('qr-order-number').textContent()) ?? '';

  // Personnel : onglet « À confirmer », puis confirmation.
  const staff = await (await browser.newContext()).newPage();
  await loginUi(staff, r.email, PASSWORD, r.name);
  await staff.goto('/restaurant/orders');
  await staff.getByRole('tab', { name: 'À confirmer' }).click();
  const list = staff.getByRole('list', { name: 'Commandes reçues par QR Code à confirmer' });
  await expect(list).toContainText(`n° ${number}`);
  await list.getByRole('button', { name: `Confirmer la commande n° ${number}` }).click();
  await expect(staff.getByText('Aucune commande à confirmer.')).toBeVisible();

  await page.reload();
  await expect(page.getByTestId('qr-order-status')).toHaveText('Confirmée — en cours');
  await expect(page.getByTestId('qr-remaining-due')).toContainText('5');
});

test('régénération : l’ancien QR imprimé cesse aussitôt de fonctionner', async ({
  page,
  request,
}) => {
  const issued = await tablePoint(request, r, 'T8');
  const renewed = await call<Issued>(
    request,
    r.token,
    'post',
    `/restaurant/qr/points/${issued.point.id}/regenerate`,
    { confirm_invalidation: true },
  );
  await page.goto(`/q${fragment(issued.url)}`);
  await expect(page.getByText(/QR Code n'est pas disponible/)).toBeVisible();
  await page.goto(`/q${fragment(renewed.url)}`);
  await expect(page.getByText('Table T8')).toBeVisible();
});

test('administration : création d’un QR de comptoir, affichage unique, confirmation', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginUi(page, r.email, PASSWORD, r.name);
  await page.goto('/restaurant/qr');
  await page.getByRole('button', { name: 'Nouveau QR Code' }).first().click();
  const dialog = page.getByRole('dialog', { name: 'Nouveau QR Code' });
  await dialog.getByRole('button', { name: 'Comptoir' }).click();
  await dialog.getByLabel(/Nom du point/).fill('Bar');
  await dialog.getByRole('button', { name: 'Générer le QR Code' }).click();
  const issued = page.getByRole('dialog', { name: 'QR Code — Bar' });
  await expect(issued).toContainText('il ne pourra plus être affiché');
  await expect(issued.getByRole('img', { name: 'QR Code à scanner' }).first()).toBeVisible();
  await issued.getByRole('button', { name: 'Fermer' }).click();
  const row = page.getByRole('row', { name: /Bar/ });
  await row.getByRole('button', { name: 'Régénérer' }).click();
  await expect(
    page.getByText(/anciens QR Codes imprimés de ce point deviendront immédiatement invalides/),
  ).toBeVisible();
});

test('@mobile comptoir : commande, refus motivé visible au client, sans débordement', async ({
  page,
  request,
}) => {
  const counter = await call<Issued>(request, r.token, 'post', '/restaurant/qr/points', {
    site_id: r.site,
    kind: 'COUNTER',
    label: `Comptoir ${r.suffix}`,
  });
  await page.goto(`/q${fragment(counter.url)}`);
  await expect(page.getByText(`Comptoir ${r.suffix}`)).toBeVisible();
  expect(await overflow(page)).toBe(false);
  await order(page, 1);
  expect(await overflow(page)).toBe(false);
  const number = Number((await page.getByTestId('qr-order-number').textContent()) ?? '0');
  const pending = await call<{ items: { id: string; daily_number: number }[] }>(
    request,
    r.token,
    'get',
    '/restaurant/orders?state=pending&limit=50',
  );
  const target = pending.items.find((o) => o.daily_number === number);
  await call(request, r.token, 'post', `/restaurant/orders/${target?.id}/reject`, {
    reason: 'Cuisine fermée',
  });
  await page.reload();
  await expect(page.getByTestId('qr-order-status')).toHaveText('Refusée');
  await expect(page.getByText('Motif : Cuisine fermée')).toBeVisible();
  // Aucun champ de décision accepté du public (site, prix…), même avec le bon jeton.
  const response = await request.post('/api/v1/public/restaurant/qr/orders', {
    headers: { 'X-QR-Token': fragment(counter.url).slice(1) },
    data: {
      client_key: crypto.randomUUID(),
      lines: [{ menu_item_id: r.item, quantity: 1, unit_price: '1' }],
      site_id: r.site,
    },
  });
  expect(response.status()).toBe(422);
});
