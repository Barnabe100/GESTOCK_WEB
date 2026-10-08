// @vitest-environment jsdom
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState, type ReactNode } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { FRONTEND_MODULES } from '@/app/modules';
import { RouteFallback } from '@/app/RouteFallback';
import { api, apiSession } from '@/core/api/client';
import type { Capabilities } from '@/core/api/types';
import { AuthContext } from '@/core/auth/AuthContext';
import { CapabilitiesContext, useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { CapabilitiesProvider } from '@/core/capabilities/CapabilitiesProvider';
import { resolveBusinessProfileTheme } from '@/core/theme/businessProfileTheme';
import i18n from '@/core/i18n';
import { jsonResponse } from '@/shared/testing';

import { AppLayout } from './AppLayout';

const CORE = ['dashboard', 'organization', 'users', 'subscription', 'audit'];
const SITES = [
  {
    id: 's1',
    name: 'Boutique Ouaga',
    code: 'BTQ',
    kind: 'store' as const,
    profile: { code: 'retail.alimentation', name: 'Alimentation', sector: 'retail' },
  },
  {
    id: 's2',
    name: 'Dépôt Central',
    code: 'DEP',
    kind: 'warehouse' as const,
    profile: { code: 'distribution.entrepot', name: 'Entrepôt', sector: 'distribution' },
  },
];

/** Capacités d'un site : profil, modules, navigation et thème propres à CE site. */
function siteCaps(siteId: 's1' | 's2'): Capabilities {
  const store = siteId === 's1';
  const modules = store ? ['stock', 'sales', 'pos', 'cash_register'] : ['stock'];
  return {
    user: { id: 'u', email: 'a@b.c', full_name: 'Awa', locale: 'fr', must_change_password: false },
    tenant: { id: 't', name: 'Faso', slug: 'faso', currency: 'XOF', locale: 'fr', timezone: 'UTC' },
    is_owner: true,
    profile: store
      ? {
          code: 'retail.alimentation',
          name: 'Alimentation',
          sector: { code: 'retail', name: 'Commerce', icon: 'pi pi-shopping-bag' },
          ux_profile: 'retail.default',
        }
      : {
          code: 'distribution.entrepot',
          name: 'Entrepôt',
          sector: { code: 'distribution', name: 'Distribution', icon: 'pi pi-truck' },
          ux_profile: 'distribution.default',
        },
    profile_scope: 'site',
    plan: { code: 'ENTREPRISE', name: 'Entreprise' },
    subscription: {
      status: 'active',
      billing_period: 'monthly',
      current_period_end: '2030-01-01',
      allowed_access: [],
    },
    site: SITES.find((s) => s.id === siteId) ?? null,
    sites: SITES,
    main_site_id: 's1',
    modules: [...CORE, ...modules].map((code) => ({
      code,
      status: 'available' as const,
      core: CORE.includes(code),
    })),
    permissions: ['stock.level.view', 'sales.sale.view', 'pos.terminal.use', 'cash.session.view'],
    restricted_permissions: [],
    navigation: ['dashboard', ...modules],
    terminology: {},
    ux: {
      code: store ? 'retail.default' : 'distribution.default',
      navigation: store
        ? [
            { group: 'home', modules: ['dashboard'] },
            { group: 'sales', modules: ['pos', 'sales'] },
            { group: 'stock', modules: ['stock'] },
          ]
        : [
            { group: 'home', modules: ['dashboard'] },
            { group: 'stock', modules: ['stock'] },
          ],
      dashboard: { widgets: [], shortcuts: [] },
      theme: store
        ? { accent: 'green', density: 'comfortable', icon: null }
        : { accent: 'indigo', density: 'compact', icon: null },
      upcoming: [],
    },
    features: [],
    limits: {},
  };
}

const AUTH = {
  status: 'authenticated' as const,
  user: null,
  tenantId: 't',
  memberships: [],
  login: async () => undefined,
  signup: async () => undefined,
  logout: async () => undefined,
  selectTenant: async () => undefined,
  changePassword: async () => undefined,
};

/** Coquille avec un sélecteur de site fonctionnel (capacités recalculées par site). */
function Harness({ children }: { children?: ReactNode }) {
  const [siteId, setSiteId] = useState<'s1' | 's2'>('s1');
  const caps = siteCaps(siteId);
  return (
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <AuthContext.Provider value={AUTH}>
          <CapabilitiesContext.Provider
            value={{
              capabilities: caps,
              can: (p) => caps.permissions.includes(p),
              isRestricted: () => false,
              hasModule: (code) => caps.modules.some((m) => m.code === code),
              siteId,
              setSiteId: (next) => setSiteId((next ?? 's1') as 's1' | 's2'),
            }}
          >
            <AppLayout modules={FRONTEND_MODULES}>
              <SwitchTo />
              {children}
            </AppLayout>
          </CapabilitiesContext.Provider>
        </AuthContext.Provider>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

function SwitchTo() {
  const { setSiteId } = useCapabilities();
  return (
    <button type="button" onClick={() => setSiteId('s2')}>
      vers le dépôt
    </button>
  );
}

const menu = () => screen.getAllByRole('link').map((link) => link.textContent);

describe('expérience par site (palier E)', () => {
  beforeEach(() =>
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ items: [], total: 0 })),
    ),
  );
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('changer de site change le profil, les modules, la navigation et le thème', () => {
    const { container } = render(<Harness />);
    const shell = () => container.querySelector('.sm-shell') as HTMLElement;
    // Boutique : profil Alimentation, accent vert, vente au menu.
    expect(screen.getByTestId('business-profile').textContent).toBe('Alimentation / Supérette');
    expect(screen.getByTestId('business-profile-scope').textContent).toBe(
      'Profil du site Boutique Ouaga',
    );
    expect(shell().dataset.accent).toBe('green');
    expect(shell().dataset.density).toBe('comfortable');
    expect(menu()).toContain('Point de vente');
    fireEvent.click(screen.getByRole('button', { name: 'vers le dépôt' }));
    // Dépôt : profil Entrepôt, accent indigo compact, plus de vente au menu.
    expect(screen.getByTestId('business-profile').textContent).toBe('Entrepôt');
    expect(screen.getByTestId('business-profile-scope').textContent).toBe(
      'Profil du site Dépôt Central',
    );
    expect(shell().dataset.accent).toBe('indigo');
    expect(shell().dataset.density).toBe('compact');
    expect(menu()).not.toContain('Point de vente');
    expect(menu()).toContain('Stock par site');
  });
});

describe('thème métier : résolution et repli', () => {
  const t = i18n.t.bind(i18n);

  it('dérivé du profil du site (accent, icône, motifs des modules mis en avant)', () => {
    const store = resolveBusinessProfileTheme(siteCaps('s1'), t, FRONTEND_MODULES);
    const depot = resolveBusinessProfileTheme(siteCaps('s2'), t, FRONTEND_MODULES);
    expect(store.fallback).toBe(false);
    expect(store.colors.accent).toBe('green');
    expect(store.icons.profile).toBe('pi pi-shopping-bag');
    expect(depot.colors.accent).toBe('indigo');
    expect(depot.icons.profile).toBe('pi pi-truck');
    expect(store.visuals.illustration.motifs).not.toEqual(depot.visuals.illustration.motifs);
    expect(depot.labels).toMatchObject({ site: 'Dépôt Central', scope: 'site' });
  });

  it('profil inconnu ou sans thème : thème neutre StockManager, jamais d’erreur', () => {
    const caps = siteCaps('s1');
    const unknown = resolveBusinessProfileTheme(
      {
        ...caps,
        profile: { code: '', name: '', sector: null, ux_profile: null },
        ux: undefined as unknown as Capabilities['ux'],
      },
      t,
      FRONTEND_MODULES,
    );
    expect(unknown).toMatchObject({
      fallback: true,
      colors: { accent: 'blue' },
      density: 'comfortable',
      icons: { profile: 'pi pi-briefcase' },
      labels: { profile: null, sector: null },
      dashboard: { widgets: [], shortcuts: [] },
    });
    const noTheme = resolveBusinessProfileTheme(
      { ...caps, ux: { ...caps.ux, theme: { accent: null, density: null, icon: null } } },
      t,
    );
    expect(noTheme.fallback).toBe(true);
    expect(noTheme.colors.accent).toBe('blue');
    expect(noTheme.labels.profile).toBe('Alimentation / Supérette');
  });
});

describe('route d’un module indisponible sur le site actif', () => {
  afterEach(cleanup);

  function renderAt(path: string) {
    const caps = siteCaps('s2');
    render(
      <MemoryRouter initialEntries={[path]}>
        <CapabilitiesContext.Provider
          value={{
            capabilities: caps,
            can: () => true,
            isRestricted: () => false,
            hasModule: () => true,
            siteId: 's2',
            setSiteId: () => undefined,
          }}
        >
          <Routes>
            <Route path="*" element={<RouteFallback modules={FRONTEND_MODULES} />} />
          </Routes>
        </CapabilitiesContext.Provider>
      </MemoryRouter>,
    );
  }

  it('page d’un module : message explicite nommant le site', () => {
    renderAt('/pos');
    expect(screen.getByText('Fonction non disponible sur ce site')).toBeTruthy();
    expect(screen.getByText(/Sur « Dépôt Central »/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /tableau de bord/i })).toBeTruthy();
  });

  it('adresse inconnue : page introuvable', () => {
    renderAt('/nimporte-quoi');
    expect(screen.queryByText('Fonction non disponible sur ce site')).toBeNull();
  });
});

describe('aucune donnée périmée du site précédent', () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    apiSession.setSiteId(null);
    sessionStorage.clear();
  });

  /** Liste dont la clé ne dépend pas du site (cas courant) : le cache doit être vidé. */
  function Listing() {
    const { setSiteId } = useCapabilities();
    const query = useQuery({
      queryKey: ['demo', 'listing'],
      queryFn: ({ signal }) => api.get<{ site: string }>('/demo', signal),
    });
    return (
      <>
        <p data-testid="listing">{query.data ? `données de ${query.data.site}` : 'chargement'}</p>
        <button type="button" onClick={() => setSiteId('s2')}>
          changer
        </button>
      </>
    );
  }

  it('changer de site vide le cache : jamais de données de l’ancien site affichées', async () => {
    const pending: ((r: Response) => void)[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        const site = new Headers(init?.headers).get('X-Site-Id') ?? 'tous';
        if (String(url).endsWith('/me/capabilities')) {
          return jsonResponse(siteCaps(site === 's2' ? 's2' : 's1'));
        }
        // Données du nouveau site retenues : l'écran ne doit pas montrer l'ancien entre-temps.
        if (site === 's2') return new Promise<Response>((resolve) => pending.push(resolve));
        return jsonResponse({ site });
      }),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <CapabilitiesProvider tenantId="t">
          <Listing />
        </CapabilitiesProvider>
      </QueryClientProvider>,
    );
    expect((await screen.findByText('données de tous')).textContent).toBe('données de tous');
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'changer' }));
    });
    expect(client.getQueryData(['demo', 'listing'])).toBeUndefined();
    await waitFor(() => expect(pending.length).toBe(1));
    expect(screen.getByTestId('listing').textContent).toBe('chargement');
    await act(async () => pending[0]?.(jsonResponse({ site: 's2' })));
    expect((await screen.findByText('données de s2')).textContent).toBe('données de s2');
  });
});
