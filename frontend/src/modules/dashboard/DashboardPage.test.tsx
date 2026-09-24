// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { jsonResponse, pageOf, renderWithCapabilities } from '@/shared/testing';

import DashboardPage from './DashboardPage';

const SALE = {
  id: 'v1',
  number: 'VTE-000001',
  site_id: 's1',
  site_name: 'Boutique',
  customer_id: null,
  customer_code: null,
  customer_name: null,
  status: 'VALIDATED',
  sale_date: '2026-09-24',
  total: '15000.00',
};

const called = (fetchMock: ReturnType<typeof vi.fn<typeof fetch>>, fragment: string) =>
  fetchMock.mock.calls.some(([url]) => String(url).includes(fragment));

describe('tableau de bord', () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes('/alerts/stock/summary')) return jsonResponse({ out: 2, low: 5 });
      if (u.includes('/sales?limit=1'))
        return jsonResponse({ items: [], total: 3, limit: 1, offset: 0 });
      if (u.includes('/stock/transfers?limit=1'))
        return jsonResponse({ items: [], total: 1, limit: 1, offset: 0 });
      return pageOf([SALE]);
    });
  });

  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it('affiche indicateurs, dernières ventes et actions rapides selon les permissions', async () => {
    renderWithCapabilities(<DashboardPage />, {
      permissions: [
        'alerts.stock.view',
        'sales.sale.view',
        'sales.sale.create',
        'stock.transfer.view',
        'stock.transfer.create',
      ],
      features: ['stock.transfers'],
    });
    const indicators = screen.getByRole('region', { name: 'Indicateurs' });
    expect(await within(indicators).findByText('2')).toBeTruthy();
    expect(within(indicators).getByText('Articles en rupture')).toBeTruthy();
    expect(await within(indicators).findByText('5')).toBeTruthy();
    expect(await within(indicators).findByText('3')).toBeTruthy();
    expect(within(indicators).getByText('Transferts en brouillon')).toBeTruthy();
    expect(await screen.findByText('VTE-000001')).toBeTruthy();
    const shortcuts = screen.getByRole('navigation', { name: 'Actions rapides' });
    expect(within(shortcuts).getByRole('button', { name: 'Nouvelle vente' })).toBeTruthy();
    expect(within(shortcuts).getByRole('button', { name: 'Nouveau transfert' })).toBeTruthy();
  });

  it('sans permission ni fonctionnalité : ni indicateur ni requête ni raccourci correspondant', async () => {
    renderWithCapabilities(<DashboardPage />, {
      // Consultation de l'historique des transferts, mais fonctionnalité absente du plan.
      permissions: ['stock.transfer.view', 'stock.transfer.create'],
      features: [],
    });
    expect(await screen.findByText('Transferts en brouillon')).toBeTruthy();
    expect(screen.queryByText('Articles en rupture')).toBeNull();
    expect(screen.queryByText('Dernières ventes')).toBeNull();
    expect(screen.queryByRole('navigation', { name: 'Actions rapides' })).toBeNull();
    expect(called(fetchMock, '/alerts/')).toBe(false);
    expect(called(fetchMock, '/sales')).toBe(false);
    // L'abonnement reste affiché (données des capacités).
    expect(screen.getByText(/Standard/)).toBeTruthy();
  });
});
