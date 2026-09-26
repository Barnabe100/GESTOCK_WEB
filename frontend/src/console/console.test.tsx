// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { Toast } from 'primereact/toast';
import type { RefObject } from 'react';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '@/core/i18n';
import { jsonResponse } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import { ConsoleAuthProvider } from './ConsoleAuth';
import { consoleRoutes } from './router';
import type { PlanDetail, PlatformAuditEntry } from './types';

const ADMIN = { id: 'a1', email: 'admin@technova.example', full_name: 'Awa Admin' };

const PLAN: PlanDetail = {
  code: 'STANDARD',
  name: 'Standard',
  description: 'Offre de base pour un commerce.',
  is_active: true,
  sort_order: 10,
  listed: true,
  price_display_enabled: true,
  monthly_price: '10000.00',
  monthly_price_enabled: true,
  annual_price: '120000.00',
  annual_price_enabled: true,
  currency: 'XOF',
  contact_required: false,
  commercial_description: null,
  display_order: 1,
  trial_days: 0,
  self_service: true,
  updated_at: '2026-09-25T10:00:00Z',
  structure: {
    modules: [
      { code: 'organization', status: 'available', core: true },
      { code: 'stock', status: 'available', core: false },
      { code: 'restaurant.menu', status: 'planned', core: false },
    ],
    features: [],
    limits: { max_sites: 1, max_users: 5 },
    grace_days: 7,
    permissions: [{ code: 'stock.level.view', module: 'stock', access: 'read', feature: null }],
  },
};

const AUDIT: PlatformAuditEntry = {
  id: 'e1',
  occurred_at: '2026-09-25T10:00:00Z',
  actor_user_id: 'a1',
  actor_label: 'admin@technova.example',
  action: 'plan.commercial.updated',
  target_type: 'plan',
  target_id: 'STANDARD',
  tenant_id: null,
  before: { annual_price: '100000.00' },
  after: { annual_price: '120000.00' },
  reason: 'Révision tarifaire annuelle TechNova',
  data: { fields: ['annual_price'] },
  ip_address: null,
};

const DASHBOARD = {
  admin: ADMIN,
  plans_total: 2,
  plans_active: 2,
  plans_listed: 1,
  plans_self_service: 1,
  plans_contact_required: 0,
  modules_available: 14,
  modules_planned: 9,
  permissions: 120,
  profiles_active: 28,
  active_countries: 249,
  tenants: {
    tenants_total: 3,
    tenants_active: 2,
    tenants_suspended: 1,
    subscriptions_active: 1,
    subscriptions_trial: 0,
    subscriptions_pending_activation: 1,
    subscriptions_past_due: 0,
    subscriptions_expired: 1,
    subscriptions_renewal_due: 0,
  },
};

const fetchMock = vi.fn<typeof fetch>();
const show = vi.fn();

function renderConsole(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(consoleRoutes, { initialEntries: [path] });
  const toast = { current: { show } as unknown as Toast } as RefObject<Toast | null>;
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastContext.Provider value={toast}>
        <ConsoleAuthProvider>
          <RouterProvider router={router} />
        </ConsoleAuthProvider>
      </ToastContext.Provider>
    </QueryClientProvider>,
  );
}

/** API de la console simulée ; `patch` : réponse à la modification commerciale. */
function consoleApi(options: { authenticated?: boolean; patch?: () => Response } = {}) {
  const authenticated = options.authenticated ?? true;
  fetchMock.mockImplementation(async (url, init) => {
    const u = String(url);
    if (u.endsWith('/me')) {
      return authenticated ? jsonResponse(ADMIN) : jsonResponse({ code: 'session_expired' }, 401);
    }
    if (u.endsWith('/auth/login')) return jsonResponse(ADMIN);
    if (u.endsWith('/dashboard')) return jsonResponse(DASHBOARD);
    if (u.endsWith('/plans')) return jsonResponse([PLAN]);
    if (u.endsWith('/plans/STANDARD/commercial') && init?.method === 'PATCH') {
      return options.patch?.() ?? jsonResponse(PLAN);
    }
    if (u.endsWith('/plans/STANDARD')) return jsonResponse(PLAN);
    if (u.endsWith('/catalog')) {
      return jsonResponse({
        modules: [],
        sectors: [],
        profiles: [],
        ux_profiles: [],
        role_templates: [],
        policies: [],
        currencies: ['EUR', 'XOF'],
        active_countries: 249,
      });
    }
    if (u.includes('/audit')) {
      return jsonResponse({ items: [AUDIT], total: 1, limit: 25, offset: 0 });
    }
    return jsonResponse({ code: 'not_found' }, 404);
  });
}

const patches = () =>
  fetchMock.mock.calls.filter(
    ([url, init]) => String(url).endsWith('/commercial') && init?.method === 'PATCH',
  );

describe('Console TechNova', () => {
  beforeEach(() => vi.stubGlobal('fetch', fetchMock));
  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    show.mockReset();
    vi.unstubAllGlobals();
  });

  it('accès protégé : sans session, page de connexion de la console', async () => {
    consoleApi({ authenticated: false });
    renderConsole('/tech-admin/plans');
    expect(
      await screen.findByText('Réservée aux administrateurs de la plateforme TechNova.'),
    ).toBeTruthy();
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/plans'))).toBe(false);
  });

  it('connexion puis accueil ; en-tête anti-CSRF sur chaque requête', async () => {
    consoleApi({ authenticated: false });
    renderConsole('/tech-admin/login');
    await screen.findByText('Réservée aux administrateurs de la plateforme TechNova.');
    fireEvent.input(document.querySelector('#email') as Element, {
      target: { value: ADMIN.email },
    });
    fireEvent.input(document.querySelector('#password') as Element, {
      target: { value: 'secret' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Se connecter' }));
    expect(await screen.findByTestId('console-identity')).toBeTruthy();
    expect(screen.getByTestId('console-identity').textContent).toContain(ADMIN.email);
    expect(screen.getByTestId('console-badge').textContent).toContain('Console TechNova');
    for (const [, init] of fetchMock.mock.calls) {
      expect((init?.headers as Record<string, string>)['X-TechNova-Console']).toBe('1');
    }
  });

  it('liste des offres : publication, type, prix, essai', async () => {
    consoleApi();
    renderConsole('/tech-admin/plans');
    const row = (await screen.findByText('Standard')).closest('tr') as HTMLElement;
    expect(within(row).getByText('STANDARD')).toBeTruthy();
    expect(within(row).getByText('Publié')).toBeTruthy();
    expect(within(row).getByText('Souscription directe')).toBeTruthy();
    expect(within(row).getByText(/120.000/)).toBeTruthy();
    expect(within(row).getByText('Aucun essai')).toBeTruthy();
  });

  it('structure technique en lecture seule', async () => {
    consoleApi();
    renderConsole('/tech-admin/plans/STANDARD');
    const structure = await screen.findByTestId('plan-structure');
    expect(within(structure).queryAllByRole('textbox')).toHaveLength(0);
    expect(within(structure).queryAllByRole('switch')).toHaveLength(0);
    expect(within(structure).queryAllByRole('button')).toHaveLength(0);
    expect(screen.getByTestId('limit-max_users').textContent).toBe('5');
    expect(within(structure).getAllByText('Stock').length).toBeGreaterThan(0);
    expect(within(structure).getByText('stock.level.view · lecture')).toBeTruthy();
  });

  it('modification : raison obligatoire, récapitulatif confirmé, seuls les champs modifiés envoyés', async () => {
    consoleApi();
    renderConsole('/tech-admin/plans/STANDARD');
    const annual = (await screen.findByLabelText('Prix annuel')) as HTMLInputElement;
    await waitFor(() => expect(annual.value).toBe('120000.00'));
    fireEvent.input(annual, { target: { value: '150000' } });
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer les modifications' }));
    expect(await screen.findByText('La raison de la modification est obligatoire.')).toBeTruthy();
    expect(screen.queryByRole('dialog')).toBeNull();

    fireEvent.input(screen.getByLabelText(/Raison de la modification/), {
      target: { value: '  Révision tarifaire annuelle TechNova  ' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer les modifications' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/Prix annuel : 120.000.*→ 150.000/)).toBeTruthy();
    expect(patches()).toHaveLength(0);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirmer' }));
    await waitFor(() => expect(patches()).toHaveLength(1));
    expect(JSON.parse(String(patches()[0]?.[1]?.body))).toEqual({
      reason: 'Révision tarifaire annuelle TechNova',
      annual_price: '150000',
    });
    await waitFor(() =>
      expect(show).toHaveBeenCalledWith(expect.objectContaining({ severity: 'success' })),
    );
  });

  it('validations : montant invalide bloqué ; aucune modification signalée', async () => {
    consoleApi();
    renderConsole('/tech-admin/plans/STANDARD');
    const monthly = (await screen.findByLabelText('Prix mensuel')) as HTMLInputElement;
    await waitFor(() => expect(monthly.value).toBe('10000.00'));
    fireEvent.input(screen.getByLabelText(/Raison de la modification/), {
      target: { value: 'Test' },
    });
    fireEvent.input(monthly, { target: { value: '-5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer les modifications' }));
    expect(await screen.findByText(/Montant invalide/)).toBeTruthy();

    fireEvent.input(monthly, { target: { value: '10000' } });
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer les modifications' }));
    await waitFor(() =>
      expect(show).toHaveBeenCalledWith(
        expect.objectContaining({
          severity: 'error',
          summary: 'Aucune modification à enregistrer.',
        }),
      ),
    );
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(patches()).toHaveLength(0);
  });

  it('refus du serveur traduit (le serveur reste la seule barrière)', async () => {
    consoleApi({
      patch: () => jsonResponse({ code: 'price_required', detail: 'x', period: 'monthly' }, 422),
    });
    renderConsole('/tech-admin/plans/STANDARD');
    const trial = (await screen.findByLabelText(
      'Durée d’essai (jours)'.replace('’', "'"),
    )) as HTMLInputElement;
    await waitFor(() => expect(trial.value).toBe('0'));
    fireEvent.input(trial, { target: { value: '14' } });
    fireEvent.input(screen.getByLabelText(/Raison de la modification/), {
      target: { value: 'Essai' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer les modifications' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/Aucun essai → 14 jours/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirmer' }));
    expect(
      await screen.findByText('Une période proposée doit avoir un prix (0 : gratuit).'),
    ).toBeTruthy();
  });

  it('journal de la plateforme : auteur, avant → après, raison', async () => {
    consoleApi();
    renderConsole('/tech-admin/audit');
    const changes = await screen.findByTestId('audit-changes');
    expect(changes.textContent).toContain('Prix annuel');
    expect(changes.textContent).toContain('100000.00 → 120000.00');
    expect(screen.getByText('Révision tarifaire annuelle TechNova')).toBeTruthy();
    expect(screen.getByText('plan.commercial.updated')).toBeTruthy();
  });
});
