// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import i18n from 'i18next';
import type { Toast } from 'primereact/toast';
import { useState, type ReactNode, type RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/core/api/client';
import { translateError } from '@/shared/lib/errors';
import { jsonResponse, pageOf, renderWithCapabilities } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import { transferLotsPath } from './api';
import { LotAllocationEditor, type LotChoice } from './LotAllocationEditor';
import TransferPage from './TransferPage';

/** Lot 3-H-B1 : transferts inter-sites par lot (desktop et mobile). */

const available = (over: Record<string, unknown> = {}) => ({
  lot_id: 'lotA',
  number: 'A',
  quantity: '100.000',
  expiry_date: '2027-01-10',
  manufacturing_date: null,
  state: 'ok',
  expired: false,
  created_at: '2026-10-01T08:00:00Z',
  ...over,
});

const LOTS = {
  article_id: 'a1',
  lot_tracked: true,
  expiry_tracked: true,
  lots: [
    available(),
    available({ lot_id: 'lotB', number: 'B', quantity: '50.000', expiry_date: '2027-03-20' }),
    available({
      lot_id: 'lotX',
      number: 'X',
      quantity: '4.000',
      expiry_date: '2026-09-20',
      state: 'expired',
      expired: true,
    }),
  ],
};

const line = (over: Record<string, unknown> = {}) => ({
  id: 'l1',
  line_no: 1,
  article_id: 'a1',
  article_reference: 'EAU-1',
  article_designation: 'Eau minérale',
  unit: 'bouteille',
  quantity: '70.000',
  unit_cost: null,
  amount: null,
  packaging_id: null,
  packaging_name: null,
  packaging_conversion: null,
  base_quantity: '70.000',
  lots: [
    { lot_id: 'lotA', lot_number: 'A', expiry_date: '2027-01-10', state: 'ok', quantity: '40.000' },
  ],
  ...over,
});

const transfer = (over: Record<string, unknown> = {}) => ({
  id: 't1',
  number: 'TRF-000001',
  source_site_id: 's1',
  source_site_name: 'Boutique',
  destination_site_id: 's2',
  destination_site_name: 'Dépôt',
  status: 'DRAFT',
  operation_date: '2026-10-02',
  comment: null,
  total_amount: null,
  line_count: 1,
  created_at: '2026-10-02T08:00:00Z',
  created_by_name: 'Awa',
  validated_at: null,
  validated_by_name: null,
  cancelled_at: null,
  cancelled_by_name: null,
  cancellation_reason: null,
  lines: [line()],
  ...over,
});

const ALL = [
  'stock.transfer.view',
  'stock.transfer.create',
  'stock.transfer.update',
  'stock.transfer.validate',
  'stock.transfer.cancel',
];

function withToast(element: ReactNode, show = vi.fn()) {
  const ref = { current: { show } as unknown as Toast } as RefObject<Toast | null>;
  return <ToastContext.Provider value={ref}>{element}</ToastContext.Provider>;
}

const fetchMock = vi.fn<typeof fetch>();
const calls = (method = 'GET') =>
  fetchMock.mock.calls
    .filter(([, init]) => (init?.method ?? 'GET') === method)
    .map(([u, init]) => [String(u), init?.body ? JSON.parse(String(init.body)) : null] as const);

beforeEach(() => vi.stubGlobal('fetch', fetchMock));
afterEach(() => {
  cleanup();
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

const text = (el: HTMLElement) => (el.textContent ?? '').replace(/\s+/g, ' ').trim();

function Editor({ initial, requested }: { initial: LotChoice[]; requested: string | null }) {
  const [value, setValue] = useState(initial);
  return (
    <>
      <LotAllocationEditor
        id="ed"
        articleId="a1"
        siteId="s1"
        unit="bouteille"
        requested={requested}
        value={value}
        locale="fr"
        onChange={setValue}
        lotsPath={transferLotsPath}
        blockExpired
      />
      <output data-testid="value">{JSON.stringify(value)}</output>
    </>
  );
}

describe('transferts par lot (Lot 3-H-B1)', () => {
  it('éditeur : point d’accès des transferts, 70 = A 50 + B 20, reste calculé', async () => {
    fetchMock.mockImplementation(async () => jsonResponse(LOTS));
    renderWithCapabilities(<Editor initial={[]} requested="70.000" />, {
      permissions: ['stock.transfer.create'],
    });
    const summary = await screen.findByTestId('ed-summary');
    expect(calls()[0]?.[0]).toContain('/stock/transfers/available-lots?article_id=a1&site_id=s1');
    expect(text(summary)).toContain('Demandé : 70 bouteille');
    expect(text(summary)).toContain('Reste : 70 bouteille');
    fireEvent.change(screen.getByLabelText('Quantité du lot A'), { target: { value: '50' } });
    await waitFor(() => expect(text(summary)).toContain('Reste : 20 bouteille'));
    expect(within(summary).getByText('Répartition incomplète')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Quantité du lot B'), { target: { value: '20' } });
    await waitFor(() => expect(within(summary).getByText('Répartition complète')).toBeTruthy());
    expect(text(summary)).toContain('Réparti : 70 bouteille');
    expect(text(summary)).toContain('Reste : 0 bouteille');
    expect(JSON.parse(screen.getByTestId('value').textContent ?? '')).toEqual([
      { lot_id: 'lotA', quantity: '50' },
      { lot_id: 'lotB', quantity: '20' },
    ]);
  });

  it('lot périmé : affiché avec son état, non sélectionnable', async () => {
    fetchMock.mockImplementation(async () => jsonResponse(LOTS));
    renderWithCapabilities(<Editor initial={[]} requested="10.000" />, {
      permissions: ['stock.transfer.create'],
    });
    const expired = (await screen.findByLabelText('Quantité du lot X')) as HTMLInputElement;
    expect(expired.disabled).toBe(true);
    expect(screen.getByText('Périmé — non transférable')).toBeTruthy();
    expect(screen.getByText('Périmé')).toBeTruthy();
    expect((screen.getByLabelText('Quantité du lot A') as HTMLInputElement).disabled).toBe(false);
  });

  it('dépassement : reste négatif signalé (le serveur refuse au brouillon)', async () => {
    fetchMock.mockImplementation(async () => jsonResponse(LOTS));
    renderWithCapabilities(
      <Editor
        initial={[
          { lot_id: 'lotA', quantity: '60' },
          { lot_id: 'lotB', quantity: '20' },
        ]}
        requested="70.000"
      />,
      { permissions: ['stock.transfer.create'] },
    );
    const summary = await screen.findByTestId('ed-summary');
    expect(text(summary)).toContain('Reste : -10 bouteille');
    expect(within(summary).getByText('Répartition incomplète')).toBeTruthy();
  });

  it('brouillon : lots relus, modifiés puis envoyés au serveur (incomplets admis)', async () => {
    fetchMock.mockImplementation(async (url, init) => {
      const u = String(url);
      if (init?.method === 'PUT') return jsonResponse(transfer());
      if (u.includes('/stock/transfers/available-lots')) return jsonResponse(LOTS);
      if (u.includes('/stock/transfers/t1')) return jsonResponse(transfer());
      return pageOf([]);
    });
    renderWithCapabilities(withToast(<TransferPage />), {
      features: ['stock.transfers'],
      permissions: ALL,
      path: '/stock/transfers/:id',
      route: '/stock/transfers/t1',
    });
    const lotB = await screen.findByLabelText('Quantité du lot B');
    expect((screen.getByLabelText('Quantité du lot A') as HTMLInputElement).value).toBe('40.000');
    expect(text(screen.getByTestId('line-0-lots-summary'))).toContain('Reste : 30 bouteille');
    fireEvent.change(lotB, { target: { value: '20' } });
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer le brouillon' }));
    await waitFor(() => expect(calls('PUT')).toHaveLength(1));
    const sent = (calls('PUT')[0]?.[1] as { lines: Record<string, unknown>[] }).lines[0];
    expect(sent).toEqual({
      article_id: 'a1',
      packaging_id: null,
      quantity: '70.000',
      lots: [
        { lot_id: 'lotA', quantity: '40.000' },
        { lot_id: 'lotB', quantity: '20' },
      ],
    });
  });

  it('validation refusée (répartition incomplète) : message du serveur affiché', async () => {
    const show = vi.fn();
    fetchMock.mockImplementation(async (url, init) => {
      const u = String(url);
      if (init?.method === 'POST' && u.endsWith('/validate')) {
        return jsonResponse(
          {
            code: 'lot_allocation_incomplete',
            status: 422,
            articles: ['EAU-1'],
            requested: '70.000',
            allocated: '40.000',
          },
          422,
        );
      }
      if (u.includes('/stock/transfers/available-lots')) return jsonResponse(LOTS);
      return jsonResponse(transfer());
    });
    renderWithCapabilities(withToast(<TransferPage />, show), {
      features: ['stock.transfers'],
      permissions: ALL,
      path: '/stock/transfers/:id',
      route: '/stock/transfers/t1',
    });
    await screen.findByLabelText('Quantité du lot B');
    fireEvent.click(screen.getByRole('button', { name: 'Valider le transfert' }));
    const dialog = await screen.findByRole('dialog');
    const accept = [...dialog.querySelectorAll('button')].find(
      (b) => b.textContent === 'Valider le transfert',
    );
    accept?.click();
    await waitFor(() =>
      expect(show).toHaveBeenCalledWith(
        expect.objectContaining({
          severity: 'error',
          summary:
            'La répartition par lot ne correspond pas à la quantité de la ligne (EAU-1 : demandé 70.000, réparti 40.000).',
        }),
      ),
    );
  });

  it('transfert validé : lots transférés sous l’article, aucune saisie', async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse(
        transfer({
          status: 'VALIDATED',
          lines: [
            line({
              lots: [
                {
                  lot_id: 'lotA',
                  lot_number: 'A',
                  expiry_date: '2027-01-10',
                  state: 'ok',
                  quantity: '50.000',
                },
                {
                  lot_id: 'lotB',
                  lot_number: 'B',
                  expiry_date: '2027-03-20',
                  state: 'ok',
                  quantity: '20.000',
                },
              ],
            }),
          ],
        }),
      ),
    );
    renderWithCapabilities(withToast(<TransferPage />), {
      features: ['stock.transfers'],
      permissions: ['stock.transfer.view'],
      path: '/stock/transfers/:id',
      route: '/stock/transfers/t1',
    });
    const cell = (await screen.findByText('EAU-1 — Eau minérale')).closest('td') as HTMLElement;
    const lots = within(cell).getByRole('list', { name: 'Lots de la ligne' });
    expect(within(lots).getAllByRole('listitem').map(text)).toEqual([
      expect.stringMatching(/^Lot A · péremption .*50 bouteille$/),
      expect.stringMatching(/^Lot B · péremption .*20 bouteille$/),
    ]);
    expect(screen.queryByLabelText('Quantité du lot A')).toBeNull();
  });

  it('erreurs propres aux transferts traduites', () => {
    const t = i18n.t.bind(i18n);
    expect(
      translateError(
        t,
        new ApiError(422, 'lot_expired_not_transferable', 'x', { lots: ['A'], articles: ['E'] }),
      ),
    ).toBe('Un lot périmé ne peut pas être transféré (A).');
    expect(
      translateError(t, new ApiError(422, 'lot_invariant_broken', 'x', { articles: ['EAU-1'] })),
    ).toBe(
      "Les soldes des lots ne correspondent pas au stock de l'article (EAU-1) : opération refusée.",
    );
  });

  it('mobile : éditeur en une colonne, mêmes libellés accessibles', async () => {
    window.innerWidth = 390;
    window.dispatchEvent(new Event('resize'));
    fetchMock.mockImplementation(async () => jsonResponse(LOTS));
    renderWithCapabilities(<Editor initial={[]} requested="70.000" />, {
      permissions: ['stock.transfer.create'],
    });
    const group = await screen.findByRole('group', { name: 'Lots' });
    expect(group.className).toContain('sm-lot-allocation');
    const rows = group.querySelectorAll('.sm-lot-allocation-row');
    expect(rows).toHaveLength(3);
    // Chaque lot : numéro, péremption, disponible et champ de quantité dans la même ligne.
    expect(within(rows[0] as HTMLElement).getByText('Disponible : 100 bouteille')).toBeTruthy();
    expect(within(rows[0] as HTMLElement).getByLabelText('Quantité du lot A')).toBeTruthy();
  });
});
