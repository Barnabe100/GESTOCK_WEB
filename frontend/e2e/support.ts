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
  const cwd = process.env.E2E_BACKEND_DIR ?? resolve(process.cwd(), '../backend');
  return execFileSync('uv', ['run', 'stockmanager', ...args], { cwd, encoding: 'utf8' });
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
 * Caisse ouverte sur un site (Phase 2.9 : un paiement en espèces exige une session de caisse
 * ouverte sur le site de la vente). Réutilise une session ouverte existante, sinon ouvre la
 * caisse « Caisse E2E » du site (créée au besoin).
 */
export async function ensureCashOpen(
  request: APIRequestContext,
  token: string,
  siteId: string,
): Promise<OpenSession> {
  const headers = bearer(token);
  const open = (await (
    await request.get(`/api/v1/cash/sessions?status=OPEN&site_id=${siteId}`, { headers })
  ).json()) as { items: OpenSession[] };
  if (open.items[0]) return open.items[0];
  const registers = (await (
    await request.get(`/api/v1/cash/registers?site_id=${siteId}&search=Caisse%20E2E`, {
      headers,
    })
  ).json()) as { items: { id: string; is_active: boolean }[] };
  let registerId = registers.items.find((r) => r.is_active)?.id;
  if (!registerId) {
    const created = await request.post('/api/v1/cash/registers', {
      headers,
      data: { site_id: siteId, name: 'Caisse E2E' },
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
