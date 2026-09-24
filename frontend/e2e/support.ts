import { expect, type APIRequestContext, type Page } from '@playwright/test';

/** Compte propriétaire d'une entreprise de test (voir e2e/README.md). */
export const OWNER = {
  email: process.env.E2E_OWNER_EMAIL ?? 'e2e-owner@example.com',
  password: process.env.E2E_OWNER_PASSWORD ?? 'E2e-Proprietaire-2026',
  tenant: process.env.E2E_TENANT_NAME ?? 'Démo E2E',
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
