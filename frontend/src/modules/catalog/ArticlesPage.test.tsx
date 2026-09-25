// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Capabilities } from '@/core/api/types';
import { CapabilitiesContext } from '@/core/capabilities/CapabilitiesContext';
import i18n from '@/core/i18n';
import { applyTerminology } from '@/core/i18n/terminology';

import ArticlesPage from './ArticlesPage';

const article = {
  id: 'a1',
  reference: 'VIS-001',
  designation: 'Vis à bois',
  category_id: 'c1',
  category_name: 'Visserie',
  unit: 'boîte',
  main_supplier_id: null,
  main_supplier_name: null,
  purchase_price: '1500.00',
  sale_price: '2000.00',
  min_stock: '0.000',
  max_stock: null,
  description: null,
  barcode: null,
  is_active: true,
  created_at: '2026-09-24T00:00:00Z',
  updated_at: '2026-09-24T00:00:00Z',
};

function page(items: unknown[]) {
  return new Response(JSON.stringify({ items, total: items.length, limit: 25, offset: 0 }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function renderPage(permissions: string[]) {
  const caps = {
    tenant: { id: 't', name: 'T', slug: 't', currency: 'XOF', locale: 'fr', timezone: 'UTC' },
    modules: [{ code: 'catalog', status: 'available', core: false }],
  } as unknown as Capabilities;
  const set = new Set(permissions);
  render(
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
        {/* La page lit `?create=1` (lien d'onboarding) : routeur requis. */}
        <MemoryRouter>
          <ArticlesPage />
        </MemoryRouter>
      </CapabilitiesContext.Provider>
    </QueryClientProvider>,
  );
}

describe('page Articles', () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockImplementation(async (url) =>
      String(url).includes('/catalog/articles') ? page([article]) : page([]),
    );
  });

  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
    applyTerminology(i18n, undefined);
  });

  it('affiche la terminologie du profil et les prix dans la devise du tenant', async () => {
    applyTerminology(i18n, { fr: { catalog: { items: 'Produits' } } });
    renderPage(['catalog.article.view']);
    expect(await screen.findByText('VIS-001')).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Produits' })).toBeTruthy();
    expect(screen.getByText(/2\s000\sF\s?CFA/)).toBeTruthy();
    // Sans permission de création : pas de bouton d'ajout ni d'actions d'édition.
    expect(screen.queryByRole('button', { name: 'Ajouter' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Modifier' })).toBeNull();
  });

  it('transmet la recherche au serveur (pagination côté serveur)', async () => {
    renderPage(['catalog.article.view', 'catalog.article.create']);
    // En-tête (et état vide, tant que la liste est vide) : bouton de création.
    expect(screen.getAllByRole('button', { name: 'Ajouter' }).length).toBeGreaterThan(0);
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'vis' } });
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url]) => String(url).includes('search=vis'))).toBe(true),
    );
    const lastArticlesUrl = fetchMock.mock.calls
      .map(([url]) => String(url))
      .filter((url) => url.includes('/catalog/articles'))
      .at(-1);
    expect(lastArticlesUrl).toContain('limit=25');
    expect(lastArticlesUrl).toContain('sort=reference');
  });
});
