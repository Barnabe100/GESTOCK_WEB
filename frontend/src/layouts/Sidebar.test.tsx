// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, describe, expect, it } from 'vitest';

import { FRONTEND_MODULES } from '@/app/modules';
import type { Capabilities } from '@/core/api/types';
import { CapabilitiesContext } from '@/core/capabilities/CapabilitiesContext';
import '@/core/i18n';

import { Sidebar } from './AppLayout';

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
    profile: { code: 'restaurant', name: 'Restaurant' },
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
