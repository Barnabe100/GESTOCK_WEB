// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, describe, expect, it } from 'vitest';

import { FRONTEND_MODULES } from '@/app/modules';
import type { Capabilities } from '@/core/api/types';
import { AuthContext } from '@/core/auth/AuthContext';
import { CapabilitiesContext } from '@/core/capabilities/CapabilitiesContext';
import '@/core/i18n';

import { AppLayout, Sidebar } from './AppLayout';

const CORE = ['dashboard', 'organization', 'users', 'subscription', 'audit'];

function capabilities(permissions: string[]): Capabilities {
  return {
    user: { id: 'u', email: 'a@b.c', full_name: 'Awa', locale: 'fr', must_change_password: false },
    tenant: {
      id: 't',
      name: 'Maquis',
      slug: 'maquis',
      currency: 'XOF',
      locale: 'fr',
      timezone: 'UTC',
    },
    is_owner: false,
    profile: { code: 'restaurant.maquis', name: 'Maquis', sector: null, ux_profile: null },
    plan: { code: 'STANDARD', name: 'Standard' },
    subscription: {
      status: 'active',
      billing_period: 'monthly',
      current_period_end: '2030-01-01',
      allowed_access: [],
    },
    site: null,
    sites: [],
    modules: [
      ...CORE.map((code) => ({ code, status: 'available' as const, core: true })),
      { code: 'restaurant.tables', status: 'planned', core: false },
    ],
    permissions,
    restricted_permissions: [],
    navigation: ['dashboard', 'restaurant.tables', ...CORE.slice(1)],
    terminology: {},
    ux: {
      code: null,
      navigation: [],
      dashboard: { widgets: [], shortcuts: [] },
      theme: { accent: null, density: null, icon: null },
      upcoming: ['restaurant.tables'],
    },
    features: [],
    limits: {},
  };
}

function renderSidebar(permissions: string[]) {
  const caps = capabilities(permissions);
  render(
    <MemoryRouter>
      <CapabilitiesContext.Provider
        value={{
          capabilities: caps,
          can: () => true,
          isRestricted: () => false,
          hasModule: () => true,
          siteId: null,
          setSiteId: () => undefined,
        }}
      >
        <Sidebar modules={FRONTEND_MODULES} />
      </CapabilitiesContext.Provider>
    </MemoryRouter>,
  );
  return screen.getAllByRole('link').map((link) => link.textContent);
}

describe('menu dynamique', () => {
  afterEach(cleanup);

  it('construit le menu à partir des capacités (lecture seule)', () => {
    expect(renderSidebar(['users.member.view', 'audit.log.view'])).toEqual([
      'Tableau de bord',
      'Utilisateurs',
      "Journal d'audit",
    ]);
  });

  it("n'affiche pas de module prévu mais non implémenté", () => {
    expect(renderSidebar([])).toEqual(['Tableau de bord']);
  });
});

describe('coquille selon le profil UX', () => {
  afterEach(cleanup);

  function renderLayout(ux: Capabilities['ux'], profile: Capabilities['profile']) {
    const caps: Capabilities = {
      ...capabilities(['stock.level.view', 'sales.sale.view', 'users.member.view']),
      modules: [...CORE, 'stock', 'sales'].map((code) => ({
        code,
        status: 'available' as const,
        core: CORE.includes(code),
      })),
      navigation: ux.navigation.flatMap((g) => g.modules),
      profile,
      ux,
    };
    const { container } = render(
      <MemoryRouter>
        <AuthContext.Provider
          value={{
            status: 'authenticated',
            user: null,
            tenantId: 't',
            memberships: [],
            login: async () => undefined,
            logout: async () => undefined,
            selectTenant: async () => undefined,
            changePassword: async () => undefined,
          }}
        >
          <CapabilitiesContext.Provider
            value={{
              capabilities: caps,
              can: (p) => caps.permissions.includes(p),
              isRestricted: () => false,
              hasModule: () => true,
              siteId: null,
              setSiteId: () => undefined,
            }}
          >
            <AppLayout modules={FRONTEND_MODULES}>
              <p>contenu</p>
            </AppLayout>
          </CapabilitiesContext.Provider>
        </AuthContext.Provider>
      </MemoryRouter>,
    );
    return container.querySelector('.sm-shell') as HTMLElement;
  }

  it('applique rubriques, accent, densité et libellé traduit du profil', () => {
    const shell = renderLayout(
      {
        code: 'distribution.default',
        navigation: [
          { group: 'home', modules: ['dashboard'] },
          { group: 'stock', modules: ['stock'] },
          { group: 'sales', modules: ['sales'] },
          { group: 'admin', modules: ['users'] },
        ],
        dashboard: { widgets: [], shortcuts: [] },
        theme: { accent: 'indigo', density: 'compact', icon: null },
        upcoming: [],
      },
      {
        code: 'distribution.grossiste',
        name: 'Nom du catalogue',
        sector: { code: 'distribution', name: 'Distribution', icon: 'pi pi-truck' },
        ux_profile: 'distribution.default',
      },
    );
    expect(shell.dataset.accent).toBe('indigo');
    expect(shell.dataset.density).toBe('compact');
    // Libellé traduit par le code (pas le nom du catalogue), icône du secteur.
    expect(screen.getByTestId('business-profile').textContent).toBe('Grossiste');
    expect(shell.querySelector('.sm-brand-profile .pi-truck')).not.toBeNull();
    // Rubriques dans l'ordre du profil, titres accessibles (texte, pas seulement icône).
    const titles = screen.getAllByRole('list').map((list) => list.getAttribute('aria-labelledby'));
    expect(titles).toEqual([null, 'nav-stock', 'nav-sales', 'nav-admin']);
    expect(screen.getByText('Stock', { selector: 'p' })).toBeTruthy();
  });

  it("un profil sans rubrique pour un module planifié n'affiche aucune entrée fictive", () => {
    renderLayout(
      {
        code: 'restaurant.default',
        navigation: [
          { group: 'home', modules: ['dashboard'] },
          { group: 'sales', modules: ['sales'] },
        ],
        dashboard: { widgets: [], shortcuts: [] },
        theme: { accent: 'orange', density: 'comfortable', icon: null },
        upcoming: ['restaurant.tables', 'restaurant.kitchen'],
      },
      {
        code: 'restaurant.restaurant',
        name: 'Restaurant',
        sector: { code: 'restaurant', name: 'Restauration', icon: 'pi pi-shop' },
        ux_profile: 'restaurant.default',
      },
    );
    const links = screen.getAllByRole('link').map((link) => link.textContent);
    expect(links.join(' ')).not.toMatch(/Tables|Cuisine|Salle/);
    expect(links).toContain('Ventes');
  });
});
