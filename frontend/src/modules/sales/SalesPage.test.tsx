// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { formatMoney } from '@/shared/lib/decimal';
import { pageOf, renderWithCapabilities } from '@/shared/testing';

import SalesPage from './SalesPage';

const sale = (over: Record<string, unknown>) => ({
  id: 'v1',
  number: 'VTE-000001',
  site_id: 's1',
  site_name: 'Boutique',
  customer_id: 'c1',
  customer_code: 'CLI-000001',
  customer_name: 'Awa Ouédraogo',
  status: 'VALIDATED',
  sale_date: '2026-09-24',
  notes: null,
  subtotal: '4500.00',
  total: '4500.00',
  line_count: 1,
  created_at: '2026-09-24T08:00:00Z',
  updated_at: '2026-09-24T08:00:00Z',
  created_by_name: 'Moussa',
  validated_at: null,
  validated_by_name: null,
  cancelled_at: null,
  cancelled_by_name: null,
  cancellation_reason: null,
  lines: [],
  ...over,
});

describe('liste des ventes', () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockImplementation(async () =>
      pageOf([
        sale({}),
        sale({
          id: 'v2',
          number: 'VTE-000002',
          customer_id: null,
          customer_code: null,
          customer_name: null,
          status: 'DRAFT',
          total: '1200.00',
        }),
      ]),
    );
  });

  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it('affiche numéro, client, montant, statut et vendeur', async () => {
    renderWithCapabilities(<SalesPage />, { permissions: ['sales.sale.view'] });
    expect(await screen.findByText('VTE-000001')).toBeTruthy();
    expect(screen.getByText('Awa Ouédraogo')).toBeTruthy();
    expect(screen.getByText('Sans client')).toBeTruthy();
    expect(screen.getByText(formatMoney('4500.00', 'XOF', 'fr').replace(/\s/g, ' '))).toBeTruthy();
    expect(screen.getByText('Validée')).toBeTruthy();
    expect(screen.getByText('Brouillon')).toBeTruthy();
    expect(screen.getAllByText('Moussa')).toHaveLength(2);
    // Plusieurs sites accessibles : colonne et filtre de site.
    expect(screen.getAllByText('Boutique').length).toBeGreaterThan(0);
    // Consultation seule : pas de création.
    expect(screen.queryByRole('button', { name: 'Nouvelle vente' })).toBeNull();
  });

  it('propose la création avec la permission et filtre côté serveur', async () => {
    renderWithCapabilities(<SalesPage />, {
      permissions: ['sales.sale.view', 'sales.sale.create'],
    });
    expect(await screen.findByRole('button', { name: 'Nouvelle vente' })).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Du'), { target: { value: '2026-09-01' } });
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]) => String(url).includes('date_from=2026-09-01')),
      ).toBe(true),
    );
    fireEvent.change(screen.getByPlaceholderText('Numéro, client (code, nom, téléphone)…'), {
      target: { value: 'awa' },
    });
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url]) => String(url).includes('search=awa'))).toBe(true),
    );
    const last = String(fetchMock.mock.calls.at(-1)?.[0]);
    expect(last).toContain('/api/v1/sales?');
    expect(last).toContain('sort=-number');
  });
});
