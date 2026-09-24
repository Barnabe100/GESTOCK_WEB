// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { pageOf, renderWithCapabilities } from '@/shared/testing';

import TransfersPage from './TransfersPage';

const transfer = (over: Record<string, unknown>) => ({
  id: 't1',
  number: 'TRF-000001',
  source_site_id: 's1',
  source_site_name: 'Boutique',
  destination_site_id: 's2',
  destination_site_name: 'Dépôt',
  status: 'VALIDATED',
  operation_date: '2026-09-24',
  comment: null,
  total_amount: '300.00',
  line_count: 2,
  created_at: '2026-09-24T08:00:00Z',
  created_by_name: 'Awa',
  validated_at: '2026-09-24T08:05:00Z',
  validated_by_name: 'Awa',
  cancelled_at: null,
  cancelled_by_name: null,
  cancellation_reason: null,
  lines: [],
  ...over,
});

describe('liste des transferts', () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockImplementation(async () =>
      pageOf([
        transfer({}),
        transfer({
          id: 't2',
          number: 'TRF-000002',
          source_site_id: 's2',
          source_site_name: 'Dépôt',
          destination_site_id: 's1',
          destination_site_name: 'Boutique',
          status: 'DRAFT',
          line_count: 1,
          created_by_name: 'Moussa',
        }),
      ]),
    );
  });

  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it('affiche numéro, sites, nombre d’articles, statut et auteur', async () => {
    renderWithCapabilities(<TransfersPage />, { permissions: ['stock.transfer.view'] });
    expect(await screen.findByText('TRF-000001')).toBeTruthy();
    const first = screen.getByText('TRF-000001').closest('tr') as HTMLElement;
    expect(first.textContent).toContain('Boutique');
    expect(first.textContent).toContain('Dépôt');
    expect(first.textContent).toContain('Validé');
    expect(first.textContent).toContain('Awa');
    const second = screen.getByText('TRF-000002').closest('tr') as HTMLElement;
    expect(second.textContent).toContain('Brouillon');
    expect(second.textContent).toContain('Moussa');
    // Consultation seule : pas de création.
    expect(screen.queryByRole('button', { name: 'Nouveau transfert' })).toBeNull();
  });

  it('propose la création avec la permission et filtre côté serveur', async () => {
    renderWithCapabilities(<TransfersPage />, {
      permissions: ['stock.transfer.view', 'stock.transfer.create'],
    });
    expect(await screen.findByRole('button', { name: 'Nouveau transfert' })).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText('N° de transfert…'), {
      target: { value: '000002' },
    });
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url]) => String(url).includes('search=000002'))).toBe(
        true,
      ),
    );
    fireEvent.change(screen.getByLabelText('Du'), { target: { value: '2026-09-01' } });
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]) => String(url).includes('date_from=2026-09-01')),
      ).toBe(true),
    );
    const last = String(fetchMock.mock.calls.at(-1)?.[0]);
    expect(last).toContain('/api/v1/stock/transfers?');
    expect(last).toContain('sort=-number');
  });
});
