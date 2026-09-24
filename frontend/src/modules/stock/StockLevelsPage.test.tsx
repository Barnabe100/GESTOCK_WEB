// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import StockLevelsPage from './StockLevelsPage';
import { pageOf, renderWithCapabilities, SITES } from './testing';

const level = {
  site_id: 's1',
  site_name: 'Boutique',
  article_id: 'a1',
  reference: 'VIS-001',
  designation: 'Vis à bois',
  unit: 'boîte',
  category_name: 'Visserie',
  article_active: true,
  quantity: '3.000',
  average_cost: '1500.1234',
  stock_value: '4500.37',
  min_stock: '5.000',
  max_stock: null,
  min_override: '5.000',
  max_override: null,
  state: 'low',
};

describe('page Stock par site', () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockImplementation(async (url) =>
      String(url).includes('/stock/levels') ? pageOf([level]) : pageOf([]),
    );
  });

  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it("affiche l'état, le CMUP à 4 décimales et la surcharge du site", async () => {
    renderWithCapabilities(<StockLevelsPage />, { permissions: ['stock.level.view'] });
    expect(await screen.findByText('VIS-001')).toBeTruthy();
    expect(screen.getByText('Stock faible')).toBeTruthy();
    expect(screen.getByText(/1\s500,1234/)).toBeTruthy();
    expect(screen.getByText('5 * / —')).toBeTruthy();
    expect(screen.getByText('Boutique')).toBeTruthy(); // plusieurs sites : colonne Site
    // Sans permission de gestion des seuils : pas d'action.
    expect(screen.queryByRole('button', { name: 'Seuils du site' })).toBeNull();
  });

  it('masque la colonne Site pour un tenant mono-site et filtre les alertes', async () => {
    renderWithCapabilities(<StockLevelsPage />, {
      permissions: ['stock.level.view', 'stock.threshold.manage'],
      sites: [SITES[0]!],
    });
    expect(await screen.findByText('VIS-001')).toBeTruthy();
    expect(screen.queryByText('Boutique')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Seuils du site' }));
    expect(await screen.findByLabelText('Stock minimum')).toBeTruthy();
    expect((screen.getByLabelText('Stock minimum') as HTMLInputElement).value).toBe('5.000');
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'vis' } });
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url]) => String(url).includes('search=vis'))).toBe(true),
    );
  });
});
