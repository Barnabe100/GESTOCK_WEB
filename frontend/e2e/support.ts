import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';

import { expect, type APIRequestContext, type Page } from '@playwright/test';

/** Compte propriétaire d'une entreprise de test (voir e2e/README.md). */
export const OWNER = {
  email: process.env.E2E_OWNER_EMAIL ?? 'e2e-owner@example.com',
  password: process.env.E2E_OWNER_PASSWORD ?? 'E2e-Proprietaire-2026',
  tenant: process.env.E2E_TENANT_NAME ?? 'Démo E2E',
};

/** Propriétaire d'une entreprise au plan STANDARD (sans transferts ; voir e2e/README.md). */
export const STANDARD_OWNER = {
  email: process.env.E2E_STANDARD_EMAIL ?? 'e2e-standard@example.com',
  password: process.env.E2E_STANDARD_PASSWORD ?? 'E2e-Standard-2026',
  tenant: process.env.E2E_STANDARD_TENANT ?? 'Démo E2E Standard',
};

/** Entreprise ENTREPRISE rétrogradée en STANDARD par le test (voir e2e/README.md). */
export const DOWNGRADE_OWNER = {
  email: process.env.E2E_DOWNGRADE_EMAIL ?? 'e2e-downgrade@example.com',
  password: process.env.E2E_DOWNGRADE_PASSWORD ?? 'E2e-Retrograde-2026',
  tenant: process.env.E2E_DOWNGRADE_TENANT ?? 'Démo E2E Rétrogradé',
};

export const unique = (prefix: string) => `${prefix} ${Date.now().toString().slice(-7)}`;

/** Connexion par l'interface ; choisit l'entreprise si le compte en a plusieurs. */
export async function loginUi(page: Page, email: string, password: string, tenant = OWNER.tenant) {
  await page.goto('/login');
  await page.locator('#email').fill(email);
  await page.locator('#password').fill(password);
  await page.locator('button[type=submit]').click();
  const chooser = page.getByRole('button', { name: tenant });
  const home = page.getByText('Modules de votre offre');
  await expect(chooser.or(home)).toBeVisible();
  if (await chooser.isVisible()) await chooser.click();
  await expect(home).toBeVisible();
}

interface Session {
  access_token: string;
  tenant_id: string | null;
  memberships: { tenant_id: string; tenant_name: string }[];
}

/** Jeton d'accès lié à l'entreprise de test (appels API directs des tests). */
export async function apiToken(
  request: APIRequestContext,
  email: string,
  password: string,
): Promise<string> {
  const login = async (tenantId?: string) => {
    const response = await request.post('/api/v1/auth/login', {
      data: { email, password, ...(tenantId ? { tenant_id: tenantId } : {}) },
    });
    expect(response.ok(), await response.text()).toBeTruthy();
    return (await response.json()) as Session;
  };
  const session = await login();
  if (session.tenant_id) return session.access_token;
  const membership = session.memberships.find((m) => m.tenant_name === OWNER.tenant);
  expect(membership, `entreprise « ${OWNER.tenant} » introuvable`).toBeTruthy();
  return (await login(membership?.tenant_id)).access_token;
}

export const bearer = (token: string) => ({ Authorization: `Bearer ${token}` });

/** Entreprise active d'un jeton (charge utile JWT, sans vérification : usage de test). */
export function tenantOf(token: string): string {
  const payload = JSON.parse(Buffer.from(token.split('.')[1] ?? '', 'base64url').toString()) as {
    tid?: string;
  };
  return payload.tid ?? '';
}

/**
 * Commande d'administration TechNova (`stockmanager`) exécutée dans le backend local :
 * certaines opérations (changement de plan) n'existent pas dans l'API des entreprises.
 */
export function adminCli(...args: string[]): string {
  return adminCliWithInput('', ...args);
}

/** Idem, avec une entrée standard (ex. mot de passe provisoire `--owner-password-stdin`). */
export function adminCliWithInput(input: string, ...args: string[]): string {
  const cwd = process.env.E2E_BACKEND_DIR ?? resolve(process.cwd(), '../backend');
  return execFileSync('uv', ['run', 'stockmanager', ...args], { cwd, encoding: 'utf8', input });
}

/**
 * Nouvelle entreprise d'un profil d'activité donné (provisioning TechNova, plan ENTREPRISE),
 * dont le propriétaire a déjà remplacé son mot de passe provisoire. Un propriétaire existant
 * reçoit une appartenance de plus (son mot de passe est conservé).
 */
export async function provisionTenant(
  request: APIRequestContext,
  {
    name,
    profile,
    email,
    password,
  }: { name: string; profile: string; email: string; password: string },
): Promise<void> {
  const temporary = 'Provisoire-Profil-2026';
  let output: string;
  try {
    output = adminCliWithInput(
      `${temporary}\n`,
      'create-tenant',
      '--name',
      name,
      '--slug',
      name
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-|-$/g, ''),
      '--business-profile',
      profile,
      '--country',
      'BF',
      '--plan',
      'ENTREPRISE',
      '--owner-email',
      email,
      '--owner-name',
      `Propriétaire ${name}`,
      '--owner-password-stdin',
    );
  } catch (error) {
    // Déjà créée par un autre projet Playwright (même nom, même exécution).
    if (String((error as { stderr?: string }).stderr).includes('déjà utilisé')) return;
    throw error;
  }
  if (!output.includes('(créé)')) return;
  const login = await request.post('/api/v1/auth/login', {
    data: { email, password: temporary },
  });
  expect(login.ok(), await login.text()).toBeTruthy();
  const { access_token: token } = (await login.json()) as { access_token: string };
  const changed = await request.post('/api/v1/me/password', {
    headers: bearer(token),
    data: { current_password: temporary, new_password: password },
  });
  expect(changed.status()).toBe(204);
}

/** Jeton d'un compte pour une entreprise donnée (par son nom). */
export async function tokenFor(
  request: APIRequestContext,
  email: string,
  password: string,
  tenant: string,
): Promise<string> {
  const login = async (tenantId?: string) => {
    const response = await request.post('/api/v1/auth/login', {
      data: { email, password, ...(tenantId ? { tenant_id: tenantId } : {}) },
    });
    expect(response.ok(), await response.text()).toBeTruthy();
    return (await response.json()) as Session;
  };
  const session = await login();
  const membership = session.memberships.find((m) => m.tenant_name === tenant);
  expect(membership, `entreprise « ${tenant} » introuvable`).toBeTruthy();
  return (await login(membership?.tenant_id)).access_token;
}

/** Crée un membre avec un rôle de base (tous sites) et fixe son mot de passe définitif. */
export async function createMember(
  request: APIRequestContext,
  ownerToken: string,
  template: string,
  password: string,
): Promise<string> {
  const roles = (await (
    await request.get('/api/v1/roles', { headers: bearer(ownerToken) })
  ).json()) as { id: string; template_code: string | null }[];
  const role = roles.find((r) => r.template_code === template);
  expect(role, `rôle de base « ${template} » introuvable`).toBeTruthy();
  const email = `${template}-${Date.now()}@example.com`;
  const temporary = 'Provisoire-E2E-2026';
  const created = await request.post('/api/v1/members', {
    headers: bearer(ownerToken),
    data: {
      email,
      full_name: unique(template),
      password: temporary,
      roles: [{ role_id: role?.id }],
      all_sites: true,
    },
  });
  expect(created.status(), await created.text()).toBe(201);
  const firstToken = await apiToken(request, email, temporary);
  const changed = await request.post('/api/v1/me/password', {
    headers: bearer(firstToken),
    data: { current_password: temporary, new_password: password },
  });
  expect(changed.status()).toBe(204);
  return email;
}

interface OpenSession {
  id: string;
  cash_register_id: string;
}

/**
 * Session de caisse **de l'utilisateur du jeton** ouverte sur un site (Lot 1 : session = site
 * + poste + utilisateur ; la caisse est optionnelle par site : elle est activée ici, ce qui
 * exige `organization.site.manage`). Réutilise sa session ouverte, sinon ouvre un poste
 * « Caisse E2E » libre du site (créé au besoin).
 */
export async function ensureCashOpen(
  request: APIRequestContext,
  token: string,
  siteId: string,
): Promise<OpenSession> {
  const headers = bearer(token);
  const enabled = await request.put(`/api/v1/cash/sites/${siteId}`, {
    headers,
    data: { enabled: true },
  });
  expect(enabled.status(), await enabled.text()).toBe(200);
  const me = (await (await request.get('/api/v1/me', { headers })).json()) as {
    user: { id: string };
  };
  const open = (await (
    await request.get(
      `/api/v1/cash/sessions?status=OPEN&site_id=${siteId}&opened_by=${me.user.id}`,
      { headers },
    )
  ).json()) as { items: OpenSession[] };
  if (open.items[0]) return open.items[0];
  const registers = (await (
    await request.get(`/api/v1/cash/registers?site_id=${siteId}&search=Caisse%20E2E&limit=100`, {
      headers,
    })
  ).json()) as { items: { id: string; is_active: boolean; current_session: unknown }[] };
  let registerId = registers.items.find((r) => r.is_active && r.current_session === null)?.id;
  if (!registerId) {
    const created = await request.post('/api/v1/cash/registers', {
      headers,
      data: { site_id: siteId, name: unique('Caisse E2E') },
    });
    expect(created.status(), await created.text()).toBe(201);
    registerId = ((await created.json()) as { id: string }).id;
  }
  const session = await request.post('/api/v1/cash/sessions', {
    headers,
    data: { cash_register_id: registerId, opening_float: '0' },
  });
  expect(session.status(), await session.text()).toBe(201);
  return (await session.json()) as OpenSession;
}

/** Identifiant d'un moyen de paiement configuré de l'entreprise, par libellé. */
export async function paymentMethodId(
  request: APIRequestContext,
  token: string,
  label: string,
): Promise<string> {
  const methods = (await (
    await request.get('/api/v1/payment-methods', { headers: bearer(token) })
  ).json()) as { id: string; label: string }[];
  const method = methods.find((m) => m.label === label);
  expect(method, `moyen de paiement « ${label} » introuvable`).toBeTruthy();
  return method?.id ?? '';
}

/** Numéro définitif d'une vente validée : `VENT-{SITE}-{ANNÉE}-{SÉQUENCE}` (Lot 1). */
export const SALE_NUMBER = /VENT-[A-Z0-9-]+-\d{4}-\d{6,}/;

/**
 * Lot 3-H (P1-b levée) : active le suivi par lot ET de péremption d'un article par l'API, comme
 * un utilisateur (article géré en stock, stock nul, aucun document ouvert : règles du serveur).
 */
export async function enableLotTracking(
  request: APIRequestContext,
  token: string,
  articleId: string,
  expiry = true,
): Promise<void> {
  const response = await request.patch(`/api/v1/catalog/articles/${articleId}`, {
    headers: bearer(token),
    data: { lot_tracked: true, expiry_tracked: expiry },
  });
  expect(response.status(), await response.text()).toBe(200);
}

/**
 * SQL exécuté avec le rôle propriétaire de la base (préparation de données de test que
 * seule l'administration TechNova peut modifier, ex. paramètres commerciaux d'un plan).
 */
export function ownerSql(sql: string): void {
  const cwd = process.env.E2E_BACKEND_DIR ?? resolve(process.cwd(), '../backend');
  const script = [
    'import sys',
    'from sqlalchemy import create_engine, text',
    'from app.core.config import get_settings',
    'with create_engine(get_settings().migration_database_url).begin() as c:',
    '    c.execute(text(sys.stdin.read()))',
  ].join('\n');
  execFileSync('uv', ['run', 'python', '-c', script], { cwd, encoding: 'utf8', input: sql });
}

/**
 * Nouveau site par l'API : 1 site = 1 abonnement (Phase 3.3-B1, ADR-0033). L'offre ENTREPRISE
 * est publiée le temps de l'appel (paramètres commerciaux remis à leurs valeurs neutres
 * ensuite), puis l'abonnement du site est rendu opérationnel comme le fera la licence
 * (paiement confirmé + licence, 3.3-B) : ces suites ne portent pas sur l'abonnement.
 */
export async function createActiveSite<T = { id: string }>(
  request: APIRequestContext,
  token: string,
  site: { name: string; code: string; kind?: string },
): Promise<T> {
  ownerSql(
    'UPDATE plans SET listed = true, contact_required = false, monthly_price_enabled = true, ' +
      "monthly_price = coalesce(monthly_price, 25000), currency = coalesce(currency, 'XOF') " +
      "WHERE code = 'ENTREPRISE'",
  );
  let response;
  try {
    response = await request.post('/api/v1/sites', {
      headers: bearer(token),
      data: { ...site, plan_code: 'ENTREPRISE', billing_period: 'monthly' },
    });
  } finally {
    ownerSql(
      'UPDATE plans SET listed = false, monthly_price_enabled = false, monthly_price = NULL, ' +
        "currency = CASE WHEN annual_price_enabled THEN currency END WHERE code = 'ENTREPRISE'",
    );
  }
  expect(response.status(), await response.text()).toBe(201);
  const created = (await response.json()) as T & { id: string };
  ownerSql(
    "UPDATE subscriptions SET status = 'active', current_period_start = now(), " +
      `current_period_end = now() + interval '1 year' WHERE site_id = '${created.id}'`,
  );
  return created;
}

/** Plan de l'abonnement de chaque site de l'entreprise (CLI TechNova, ADR-0033). */
export function changePlanOfSites(tenantId: string, siteIds: string[], plan: string): string {
  return siteIds
    .map((siteId) =>
      adminCli('change-plan', '--tenant-id', tenantId, '--site-id', siteId, '--plan', plan),
    )
    .join('\n');
}

/**
 * Déclare un paiement d'abonnement par l'API (Phase 3.3-B4) : la période est calculée par le
 * serveur ; le montant n'est envoyé que pour une offre sans tarif (sinon, calculé par le serveur).
 */
export async function declareSubscriptionPayment(
  request: APIRequestContext,
  token: string,
  reference: string,
  { amount, method = 'BANK_TRANSFER' }: { amount: string; method?: string },
): Promise<{ id: string; status: string; amount: string }> {
  const headers = { Authorization: `Bearer ${token}` };
  const subscription = (await (await request.get('/api/v1/subscription', { headers })).json()) as {
    id: string;
  };
  const quote = (await (
    await request.get(`/api/v1/subscriptions/${subscription.id}/renewal-quote`, { headers })
  ).json()) as { amount: string | null };
  const response = await request.post('/api/v1/subscription/payments', {
    headers,
    data: {
      subscription_id: subscription.id,
      ...(quote.amount === null ? { amount } : {}),
      payment_method: method,
      declared_reference: reference,
      idempotency_key: crypto.randomUUID(),
    },
  });
  expect(response.status(), await response.text()).toBe(201);
  return (await response.json()) as { id: string; status: string; amount: string };
}

/** Sites accessibles à un jeton (`GET /sites`) : identifiants, dans l'ordre du serveur. */
export async function siteIdsOf(request: APIRequestContext, token: string): Promise<string[]> {
  const response = await request.get('/api/v1/sites', { headers: bearer(token) });
  expect(response.ok(), await response.text()).toBeTruthy();
  return ((await response.json()) as { id: string }[]).map((s) => s.id);
}

/**
 * Ajout EXPLICITE d'articles à l'assortiment de sites (Recette, étape 1, ADR-0046) :
 * CATALOGUE ≠ ASSORTIMENT SITE ≠ STOCK SITE, aucun article n'est proposé par un site sans ce
 * choix. Sans `siteIds`, tous les sites accessibles au jeton (choix explicite du test).
 */
export async function assortArticles(
  request: APIRequestContext,
  token: string,
  articleIds: string[],
  siteIds?: string[],
): Promise<void> {
  for (const siteId of siteIds ?? (await siteIdsOf(request, token))) {
    const response = await request.post(`/api/v1/catalog/sites/${siteId}/articles`, {
      headers: bearer(token),
      data: { article_ids: articleIds },
    });
    expect(response.ok(), await response.text()).toBeTruthy();
  }
}
