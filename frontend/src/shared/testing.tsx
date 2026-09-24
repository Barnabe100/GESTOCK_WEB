// Aides de test des pages (non incluses dans le bundle : importées par les tests seuls).
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import { ConfirmDialog } from 'primereact/confirmdialog';
import type { ReactNode } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router';

import type { Capabilities } from '@/core/api/types';
import { CapabilitiesContext } from '@/core/capabilities/CapabilitiesContext';
import '@/core/i18n';

export function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

export function pageOf(items: unknown[]) {
  return jsonResponse({ items, total: items.length, limit: 25, offset: 0 });
}

export const SITES = [
  { id: 's1', name: 'Boutique', code: 'BTQ', kind: 'store' },
  { id: 's2', name: 'Dépôt', code: 'DEP', kind: 'warehouse' },
];

export function renderWithCapabilities(
  element: ReactNode,
  {
    permissions,
    sites = SITES,
    path = '/',
    route = '/',
    isOwner = false,
    features = [],
  }: {
    permissions: string[];
    sites?: typeof SITES;
    path?: string;
    route?: string;
    isOwner?: boolean;
    /** Fonctionnalités du plan (ex. `stock.transfers`). */
    features?: string[];
  },
) {
  const caps = {
    user: { id: 'u-me', email: 'me@example.com', full_name: 'Moi' },
    tenant: { id: 't', name: 'T', slug: 't', currency: 'XOF', locale: 'fr', timezone: 'UTC' },
    is_owner: isOwner,
    profile: { code: 'retail', name: 'Commerce' },
    plan: { code: 'STANDARD', name: 'Standard' },
    subscription: {
      status: 'active',
      billing_period: 'monthly',
      current_period_end: '2026-12-31T00:00:00Z',
      allowed_access: ['read', 'write', 'export', 'admin', 'billing'],
    },
    restricted_permissions: [],
    site: null,
    sites,
    modules: [],
    features,
  } as unknown as Capabilities;
  const set = new Set(permissions);
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <CapabilitiesContext.Provider
        value={{
          capabilities: caps,
          can: (p) => set.has(p),
          isRestricted: () => false,
          hasModule: () => true,
          siteId: null,
          setSiteId: () => undefined,
        }}
      >
        <MemoryRouter initialEntries={[route]}>
          <Routes>
            <Route path={path} element={element} />
          </Routes>
        </MemoryRouter>
        {/* Comme la coquille de l'application : un seul dialogue de confirmation. */}
        <ConfirmDialog />
      </CapabilitiesContext.Provider>
    </QueryClientProvider>,
  );
}
