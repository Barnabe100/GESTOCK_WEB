import { expect, type APIRequestContext, type Locator, type Page } from '@playwright/test';

import {
  apiToken,
  assortArticles,
  bearer,
  provisionTenant,
  siteIdsOf,
  tokenFor,
  unique,
} from './support';

/**
 * Appui des E2E de la restauration (commandes et tables, livrées par la migration 0048) :
 * entreprise dédiée créée à chaque exécution, article EN STOCK sur le site, menu d'un élément,
 * membres de rôles de base, éventuellement limités à certains sites.
 */

export interface Resto {
  name: string;
  email: string;
  password: string;
  token: string;
  site: string;
  article: string;
  item: string;
  itemLabel: string;
  suffix: string;
}

type Method = 'get' | 'post' | 'put' | 'patch';

/** Appel de l'API attendu en succès (2xx) ; renvoie le corps JSON (``null`` pour 204). */
export async function call<T = { id: string }>(
  request: APIRequestContext,
  token: string,
  method: Method,
  path: string,
  data?: unknown,
): Promise<T> {
  const response = await request[method](`/api/v1${path}`, {
    headers: bearer(token),
    ...(data === undefined ? {} : { data }),
  });
  expect(
    response.status(),
    `${method.toUpperCase()} ${path} : ${await response.text()}`,
  ).toBeLessThan(300);
  return (response.status() === 204 ? null : await response.json()) as T;
}

/** Appel brut (statut et code d'erreur vérifiés par le test). */
export async function raw(
  request: APIRequestContext,
  token: string,
  method: Method,
  path: string,
  data?: unknown,
) {
  const response = await request[method](`/api/v1${path}`, {
    headers: bearer(token),
    ...(data === undefined ? {} : { data }),
  });
  const body = (await response.json().catch(() => ({}))) as { code?: string };
  return { status: response.status(), code: body.code, body };
}

/** Entreprise de restauration (profil Maquis : commandes proposées, tables facultatives). */
export async function setupRestaurant(
  request: APIRequestContext,
  prefix: string,
  password: string,
): Promise<Resto> {
  const suffix = Date.now().toString().slice(-7);
  const name = `${prefix} ${suffix}`;
  const email = `e2e-${prefix.toLowerCase().replace(/[^a-z]+/g, '-')}-${suffix}@example.com`;
  await provisionTenant(request, { name, profile: 'restaurant.maquis', email, password });
  const token = await tokenFor(request, email, password, name);
  const [site] = await siteIdsOf(request, token);
  // Désignation sans chiffre : jamais confondue avec un prix ou un numéro affiché.
  const letters = suffix.replace(/\d/g, (d) => 'ABCDEFGHIJ'.charAt(Number(d)));
  const itemLabel = `Poulet ${letters}`;
  const category = await call(request, token, 'post', '/catalog/categories', {
    name: `Plats ${suffix}`,
  });
  const article = await call(request, token, 'post', '/catalog/articles', {
    reference: `PLAT-${letters}`,
    designation: itemLabel,
    category_id: category.id,
    unit: 'portion',
    purchase_price: '1500',
    sale_price: '2500',
  });
  await assortArticles(request, token, [article.id]);
  const entry = await call(request, token, 'post', '/stock/entries', {
    site_id: site,
    kind: 'INITIAL_STOCK',
    lines: [{ article_id: article.id, quantity: '100', unit_cost: '1500' }],
  });
  await call(request, token, 'post', `/stock/entries/${entry.id}/validate`, {});
  const section = await call(request, token, 'post', '/restaurant/menu/sections', {
    site_id: site,
    name: 'Plats',
  });
  const item = await call(request, token, 'post', '/restaurant/menu/items', {
    site_id: site,
    section_id: section.id,
    article_id: article.id,
  });
  return {
    name,
    email,
    password,
    token,
    site: site ?? '',
    article: article.id,
    item: item.id,
    itemLabel,
    suffix,
  };
}

/** Commande créée par l'API (clé d'idempotence neuve). */
export async function createOrder(
  request: APIRequestContext,
  token: string,
  site: string,
  item: string,
  extra: Record<string, unknown> = {},
) {
  return call<{ id: string; daily_number: number }>(request, token, 'post', '/restaurant/orders', {
    site_id: site,
    service_mode: 'ON_SITE',
    lines: [{ menu_item_id: item, quantity: '1' }],
    idempotency_key: crypto.randomUUID(),
    ...extra,
  });
}

/**
 * Membre d'un rôle de base (``template``), éventuellement limité à des sites ; mot de passe
 * provisoire remplacé. Renvoie son e-mail et son jeton.
 */
export async function addMember(
  request: APIRequestContext,
  owner: Resto,
  template: string,
  sites: string[] | null = null,
): Promise<{ email: string; token: string }> {
  const roles = await call<{ id: string; template_code: string | null }[]>(
    request,
    owner.token,
    'get',
    '/roles',
  );
  const role = roles.find((r) => r.template_code === template);
  expect(role, `rôle « ${template} »`).toBeTruthy();
  const email = `${template}-${unique('m').replace(/\D/g, '')}-${owner.suffix}@example.com`;
  const temporary = 'Provisoire-E2E-2026';
  await call(request, owner.token, 'post', '/members', {
    email,
    full_name: `${template} ${owner.suffix}`,
    password: temporary,
    roles: [{ role_id: role?.id }],
    ...(sites ? { all_sites: false, site_ids: sites } : { all_sites: true }),
  });
  const first = await apiToken(request, email, temporary);
  const changed = await request.post('/api/v1/me/password', {
    headers: bearer(first),
    data: { current_password: temporary, new_password: owner.password },
  });
  expect(changed.status()).toBe(204);
  return { email, token: await tokenFor(request, email, owner.password, owner.name) };
}

/** Liste déroulante PrimeReact (identifiant de l'entrée) dans un conteneur. */
export async function choose(page: Page, scope: Locator, inputId: string, option: string | RegExp) {
  await scope.locator('.p-dropdown', { has: page.locator(`#${inputId}`) }).click();
  await page.locator('.p-dropdown-panel').last().getByRole('option', { name: option }).click();
}
