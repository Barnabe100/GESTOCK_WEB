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
import type { TenantDetail, TenantListItem } from './types';

const ADMIN = { id: 'a1', email: 'admin@technova.example', full_name: 'Awa Admin' };

const ROW: TenantListItem = {
  id: 't-1',
  name: 'ABC Commerce',
  trade_name: 'Chez ABC',
  slug: 'abc-commerce',
  status: 'active',
  business_profile_code: 'retail.alimentation',
  business_profile_name: 'Alimentation',
  created_at: '2026-09-01T10:00:00Z',
  plan_code: 'STANDARD',
  plan_name: 'Standard',
  subscription_status: 'pending_activation',
  effective_status: 'pending_activation',
  current_period_end: '2026-09-01T10:00:00Z',
  sites: 1,
  users: 3,
};

const DETAIL: TenantDetail = {
  ...ROW,
  country_code: 'BF',
  country_name: 'Burkina Faso',
  currency: 'XOF',
  locale: 'fr',
  timezone: 'Africa/Ouagadougou',
  usage: { max_sites: { used: 1, limit: 1 }, max_users: { used: 3, limit: 5 } },
  subscription: {
    id: 's-1',
    plan_code: 'STANDARD',
    plan_name: 'Standard',
    billing_period: 'monthly',
    status: 'pending_activation',
    effective_status: 'pending_activation',
    started_at: '2026-09-01T10:00:00Z',
    current_period_start: '2026-09-01T10:00:00Z',
    current_period_end: '2026-09-01T10:00:00Z',
    cancelled_at: null,
    grace_days: 7,
    price_at_subscription: '10000.00',
    currency_at_subscription: 'XOF',
  },
  actions: {
    can_suspend: true,
    can_reactivate: false,
    can_activate: true,
    can_extend: false,
    can_change_plan: true,
    activation_start: '2026-09-25',
    activation_end: '2026-10-25',
    extension_end: '2026-10-25',
    available_plans: [{ code: 'ENTREPRISE', name: 'Entreprise' }],
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

function consoleApi(action?: (url: string, body: unknown) => Response) {
  fetchMock.mockImplementation(async (url, init) => {
    const u = String(url);
    if (u.endsWith('/me')) return jsonResponse(ADMIN);
    if (u.endsWith('/plans')) return jsonResponse([{ code: 'STANDARD', name: 'Standard' }]);
    if (init?.method === 'POST' && u.includes('/tenants/t-1/')) {
      return action?.(u, JSON.parse(String(init.body))) ?? jsonResponse(DETAIL);
    }
    if (u.endsWith('/tenants/t-1')) return jsonResponse(DETAIL);
    if (u.includes('/tenants?'))
      return jsonResponse({ items: [ROW], total: 1, limit: 25, offset: 0 });
    if (u.includes('/audit')) return jsonResponse({ items: [], total: 0, limit: 20, offset: 0 });
    return jsonResponse({ code: 'not_found' }, 404);
  });
}

/** Option d'une liste déroulante PrimeReact ouverte (panneau rendu dans le document). */
async function panelOption(label: string) {
  const panel = await waitFor(() => {
    const found = document.querySelector('.p-dropdown-panel');
    if (!found) throw new Error('panneau fermé');
    return found as HTMLElement;
  });
  return within(panel).getByText(label);
}

const posts = () =>
  fetchMock.mock.calls.filter(
    ([, init]) => init?.method === 'POST' && !String(init.body).includes('password'),
  );

describe('Console TechNova : tenants', () => {
  beforeEach(() => vi.stubGlobal('fetch', fetchMock));
  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    show.mockReset();
    vi.unstubAllGlobals();
  });

  it('liste : métadonnées plateforme, filtres et tri transmis au serveur', async () => {
    consoleApi();
    renderConsole('/tech-admin/tenants');
    const row = (await screen.findByText('ABC Commerce')).closest('tr') as HTMLElement;
    expect(within(row).getByText('Active')).toBeTruthy();
    expect(within(row).getByText("En attente d'activation")).toBeTruthy();
    expect(within(row).getByText('Chez ABC · Alimentation')).toBeTruthy();
    const first = fetchMock.mock.calls.find(([u]) => String(u).includes('/tenants?'));
    expect(String(first?.[0])).toContain('sort=name');

    fireEvent.click(screen.getByTestId('filter-status'));
    fireEvent.click(await panelOption('Suspendue'));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes('status=suspended'))).toBe(true),
    );
  });

  it('fiche : identité, utilisation, abonnement et prix figé ; aucune donnée métier', async () => {
    consoleApi();
    renderConsole('/tech-admin/tenants/t-1');
    expect(await screen.findByText('Burkina Faso')).toBeTruthy();
    expect(screen.getByTestId('usage-max_users').textContent).toContain('3 / 5');
    expect(screen.getByTestId('subscription-plan').textContent).toBe('Standard (STANDARD)');
    expect(screen.getByTestId('subscription-price').textContent).toMatch(/10.000/);
    const actions = screen.getByTestId('tenant-actions');
    expect(within(actions).getByRole('button', { name: "Activer l'abonnement" })).toBeTruthy();
    expect(within(actions).queryByRole('button', { name: 'Réactiver' })).toBeNull();
  });

  it('activation : dates proposées par le serveur, raison et confirmation exigées', async () => {
    consoleApi();
    renderConsole('/tech-admin/tenants/t-1');
    fireEvent.click(await screen.findByRole('button', { name: "Activer l'abonnement" }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/ni un paiement confirmé ni une licence/)).toBeTruthy();
    expect((within(dialog).getByLabelText(/Début/) as HTMLInputElement).value).toBe('2026-09-25');
    const confirm = within(dialog).getByRole('button', { name: "Confirmer l'activation" });
    expect(confirm.hasAttribute('disabled')).toBe(true);
    fireEvent.click(within(dialog).getByLabelText('Je confirme cette action.'));
    fireEvent.click(confirm);
    expect(await within(dialog).findByText('La raison est obligatoire.')).toBeTruthy();
    expect(posts()).toHaveLength(0);

    fireEvent.input(within(dialog).getByLabelText(/Raison/), {
      target: { value: '  Activation commerciale temporaire ' },
    });
    fireEvent.click(confirm);
    await waitFor(() => expect(posts()).toHaveLength(1));
    const [url, init] = posts()[0] ?? [];
    expect(String(url)).toMatch(/\/tenants\/t-1\/subscription\/activate$/);
    expect(JSON.parse(String(init?.body))).toEqual({
      reason: 'Activation commerciale temporaire',
      period_start: '2026-09-25',
      period_end: '2026-10-25',
    });
    expect((init?.headers as Record<string, string>)['X-TechNova-Console']).toBe('1');
    await waitFor(() =>
      expect(show).toHaveBeenCalledWith(expect.objectContaining({ severity: 'success' })),
    );
  });

  it('changement de plan et refus du serveur traduit', async () => {
    consoleApi(() => jsonResponse({ code: 'invalid_period', detail: 'x' }, 422));
    renderConsole('/tech-admin/tenants/t-1');
    fireEvent.click(await screen.findByRole('button', { name: 'Changer de plan' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(dialog.querySelector('#new-plan')?.closest('.p-dropdown') as Element);
    fireEvent.click(await panelOption('Entreprise'));
    fireEvent.input(within(dialog).getByLabelText(/Raison/), {
      target: { value: 'Montée en gamme' },
    });
    fireEvent.click(within(dialog).getByLabelText('Je confirme cette action.'));
    fireEvent.click(
      within(dialog).getByRole('button', { name: 'Confirmer le changement de plan' }),
    );
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(JSON.parse(String(posts()[0]?.[1]?.body))).toEqual({
      reason: 'Montée en gamme',
      plan_code: 'ENTREPRISE',
    });
    expect(await within(dialog).findByText(/Dates invalides/)).toBeTruthy();
  });

  it('suspension : action sensible, confirmation explicite', async () => {
    consoleApi();
    renderConsole('/tech-admin/tenants/t-1');
    fireEvent.click(await screen.findByRole('button', { name: 'Suspendre' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/perdent l'accès immédiatement/)).toBeTruthy();
    fireEvent.input(within(dialog).getByLabelText(/Raison/), { target: { value: 'Impayé' } });
    fireEvent.click(within(dialog).getByLabelText('Je confirme cette action.'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirmer la suspension' }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(String(posts()[0]?.[0])).toMatch(/\/tenants\/t-1\/suspend$/);
    expect(JSON.parse(String(posts()[0]?.[1]?.body))).toEqual({ reason: 'Impayé' });
  });
});
