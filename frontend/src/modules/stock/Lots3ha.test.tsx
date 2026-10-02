// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import i18n from 'i18next';
import type { Toast } from 'primereact/toast';
import { useState, type ReactNode, type RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/core/api/client';
import { PosReceipt } from '@/modules/pos/PosReceipt';
import { ExpiredLotOverridePanel } from '@/modules/sales/ExpiredLotOverridePanel';
import { saleFixture } from '@/modules/sales/testData';
import { ValidateSaleDialog } from '@/modules/sales/ValidateSaleDialog';
import { jsonResponse, pageOf, renderWithCapabilities } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import { ExitPage } from './DocumentPage';
import { LotAllocationEditor, type LotChoice } from './LotAllocationEditor';
import { expiredShortages, LineLotsList, stockError, type ExpiredShortage } from './ui';

/** Lot 3-H-A : consommation des lots (sorties, ventes, POS, dérogation lot périmé). */

const available = (over: Record<string, unknown> = {}) => ({
  lot_id: 'lotA',
  number: 'A',
  quantity: '60.000',
  expiry_date: '2026-11-01',
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
    available({ lot_id: 'lotB', number: 'B', quantity: '50.000', expiry_date: '2026-12-01' }),
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

const exitLine = (over: Record<string, unknown> = {}) => ({
  id: 'l1',
  line_no: 1,
  article_id: 'a1',
  article_reference: 'EAU-1',
  article_designation: 'Eau minérale',
  unit: 'bouteille',
  quantity: '100.000',
  unit_cost: null,
  amount: null,
  packaging_id: null,
  packaging_name: null,
  packaging_conversion: null,
  base_quantity: '100.000',
  location_name: null,
  lots: [
    { lot_id: 'lotA', lot_number: 'A', expiry_date: '2026-11-01', state: 'ok', quantity: '60.000' },
  ],
  ...over,
});

const exitDoc = (over: Record<string, unknown> = {}) => ({
  id: 'x1',
  number: 'SOR-000001',
  site_id: 's1',
  site_name: 'Boutique',
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
  reason_id: 'r1',
  reason_label: 'Perte',
  beneficiary: null,
  reference: null,
  lines: [exitLine()],
  ...over,
});

const SHORTAGES: ExpiredShortage[] = [
  {
    article_id: 'a1',
    reference: 'EAU-1',
    missing: '2.000',
    expired_available: '5.000',
    expired_lots: [
      { lot_id: 'lotX', lot_number: 'X', expiry_date: '2026-09-20', available: '5.000' },
    ],
  },
];

function withToast(element: ReactNode) {
  const ref = { current: { show: vi.fn() } as unknown as Toast } as RefObject<Toast | null>;
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
      />
      <output data-testid="value">{JSON.stringify(value)}</output>
    </>
  );
}

describe('consommation des lots (Lot 3-H-A)', () => {
  it('sortie : lots disponibles, demandé / réparti / reste, répartition incomplète puis complète', async () => {
    fetchMock.mockImplementation(async () => jsonResponse(LOTS));
    renderWithCapabilities(
      <Editor initial={[{ lot_id: 'lotA', quantity: '60' }]} requested="100.000" />,
      {
        permissions: ['stock.exit.create'],
      },
    );
    const summary = await screen.findByTestId('ed-summary');
    expect(text(summary)).toContain('Demandé : 100 bouteille');
    expect(text(summary)).toContain('Réparti : 60 bouteille');
    expect(text(summary)).toContain('Reste : 40 bouteille');
    expect(within(summary).getByText('Répartition incomplète')).toBeTruthy();
    // Lots du serveur dans son ordre : quantité disponible, péremption, état (périmé signalé).
    expect(screen.getByText('Lot A')).toBeTruthy();
    expect(screen.getByText('Disponible : 50 bouteille')).toBeTruthy();
    expect(screen.getByText('Périmé')).toBeTruthy();
    expect(calls()[0]?.[0]).toContain('/stock/available-lots?article_id=a1&site_id=s1');
    fireEvent.change(screen.getByLabelText('Quantité du lot B'), { target: { value: '40' } });
    await waitFor(() => expect(within(summary).getByText('Répartition complète')).toBeTruthy());
    expect(JSON.parse(screen.getByTestId('value').textContent ?? '')).toEqual([
      { lot_id: 'lotA', quantity: '60' },
      { lot_id: 'lotB', quantity: '40' },
    ]);
    // Effacer une quantité retire le lot du choix.
    fireEvent.change(screen.getByLabelText('Quantité du lot A'), { target: { value: '' } });
    await waitFor(() =>
      expect(JSON.parse(screen.getByTestId('value').textContent ?? '')).toEqual([
        { lot_id: 'lotB', quantity: '40' },
      ]),
    );
  });

  it('article non suivi par lot : aucun sélecteur', async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse({ ...LOTS, lot_tracked: false, expiry_tracked: false, lots: [] }),
    );
    renderWithCapabilities(<Editor initial={[]} requested="5.000" />, {
      permissions: ['stock.exit.create'],
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(screen.queryByText('Répartition incomplète')).toBeNull();
    expect(screen.queryByRole('group', { name: 'Lots' })).toBeNull();
  });

  it('brouillon de sortie : choix des lots relus puis envoyés au serveur (incomplets admis)', async () => {
    fetchMock.mockImplementation(async (url, init) => {
      const u = String(url);
      if (init?.method === 'PUT') return jsonResponse(exitDoc());
      if (u.includes('/stock/available-lots')) return jsonResponse(LOTS);
      if (u.includes('/stock/exit-reasons')) {
        return pageOf([{ id: 'r1', code: 'PERTE', label: 'Perte', is_active: true }]);
      }
      if (u.includes('/stock/exits/x1')) return jsonResponse(exitDoc());
      return pageOf([]);
    });
    renderWithCapabilities(withToast(<ExitPage />), {
      permissions: ['stock.exit.view', 'stock.exit.update', 'stock.exit.create'],
      path: '/stock/exits/:id',
      route: '/stock/exits/x1',
    });
    const lotB = await screen.findByLabelText('Quantité du lot B');
    expect((screen.getByLabelText('Quantité du lot A') as HTMLInputElement).value).toBe('60.000');
    fireEvent.change(lotB, { target: { value: '30' } });
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer le brouillon' }));
    await waitFor(() => expect(calls('PUT')).toHaveLength(1));
    const line = (calls('PUT')[0]?.[1] as { lines: Record<string, unknown>[] }).lines[0];
    expect(line).toMatchObject({
      article_id: 'a1',
      quantity: '100.000',
      lots: [
        { lot_id: 'lotA', quantity: '60.000' },
        { lot_id: 'lotB', quantity: '30' },
      ],
    });
  });

  it('sortie validée : répartition réelle par lot sous l’article', async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse(
        exitDoc({
          status: 'VALIDATED',
          lines: [
            exitLine({
              lots: [
                {
                  lot_id: 'lotA',
                  lot_number: 'A',
                  expiry_date: '2026-11-01',
                  state: 'ok',
                  quantity: '60.000',
                },
                {
                  lot_id: 'lotB',
                  lot_number: 'B',
                  expiry_date: null,
                  state: 'no_expiry',
                  quantity: '40.000',
                },
              ],
            }),
          ],
        }),
      ),
    );
    renderWithCapabilities(<ExitPage />, {
      permissions: ['stock.exit.view'],
      path: '/stock/exits/:id',
      route: '/stock/exits/x1',
    });
    const cell = (await screen.findByText('EAU-1 — Eau minérale')).closest('td') as HTMLElement;
    const lots = within(cell).getByRole('list', { name: 'Lots de la ligne' });
    expect(within(lots).getAllByRole('listitem').map(text)).toEqual([
      expect.stringMatching(/^Lot A · péremption .*60 bouteille$/),
      'Lot B40 bouteille',
    ]);
  });

  it('erreurs : stock non périmé insuffisant, répartition incomplète, garde-fou', () => {
    const t = i18n.t.bind(i18n);
    const unexpired = new ApiError(422, 'insufficient_unexpired_stock', 'x', {
      articles: SHORTAGES,
    });
    expect(stockError(t, unexpired)).toBe(
      'Stock non périmé insuffisant : EAU-1 (manque 2 ; 5 en lots périmés). Un lot périmé ne peut être vendu que par une dérogation explicite.',
    );
    expect(expiredShortages(unexpired)).toEqual(SHORTAGES);
    expect(expiredShortages(new ApiError(422, 'insufficient_stock', 'x', {}))).toEqual([]);
    expect(
      stockError(
        t,
        new ApiError(422, 'lot_allocation_incomplete', 'x', {
          articles: ['EAU-1'],
          requested: '100',
          allocated: '60',
        }),
      ),
    ).toBe(
      'La répartition par lot ne correspond pas à la quantité de la ligne (EAU-1 : demandé 100, réparti 60).',
    );
    expect(stockError(t, new ApiError(422, 'lot_required', 'x', { articles: ['EAU-1'] }))).toBe(
      'Article suivi par lot : cette opération exige un lot (EAU-1).',
    );
  });

  it('dérogation : refusée sans la permission (aucun champ proposé)', () => {
    const onConfirm = vi.fn();
    renderWithCapabilities(
      <ExpiredLotOverridePanel
        shortages={SHORTAGES}
        idPrefix="t"
        pending={false}
        onConfirm={onConfirm}
      />,
      { permissions: ['sales.sale.validate'] },
    );
    expect(screen.getByText(/Seul un utilisateur autorisé peut vendre un lot périmé/)).toBeTruthy();
    expect(screen.queryByLabelText(/Motif de la dérogation/)).toBeNull();
    expect(screen.queryByRole('button', { name: 'Vendre avec dérogation' })).toBeNull();
  });

  it('dérogation explicite : lot périmé signalé, motif et confirmation obligatoires', () => {
    const onConfirm = vi.fn();
    renderWithCapabilities(
      <ExpiredLotOverridePanel
        shortages={SHORTAGES}
        idPrefix="t"
        pending={false}
        onConfirm={onConfirm}
      />,
      { permissions: ['sales.sale.validate', 'sales.sale.expired_lot_override'] },
    );
    expect(screen.getByText(/Périmé depuis le/)).toBeTruthy();
    expect(screen.getByText('EAU-1 : il manque 2 en lots non périmés.')).toBeTruthy();
    // Quantité proposée = le manque (à confirmer ou corriger).
    expect((screen.getByLabelText('Quantité du lot X') as HTMLInputElement).value).toBe('2.000');
    const sell = screen.getByRole('button', { name: 'Vendre avec dérogation' });
    expect((sell as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/Motif de la dérogation/), { target: { value: 'abc' } });
    fireEvent.click(screen.getByLabelText('Je confirme vendre ces lots périmés'));
    expect((sell as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/Motif de la dérogation/), {
      target: { value: 'Déstockage autorisé' },
    });
    expect((sell as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(sell);
    expect(onConfirm).toHaveBeenCalledWith({
      reason: 'Déstockage autorisé',
      lots: [{ article_id: 'a1', lot_id: 'lotX', quantity: '2.000' }],
    });
  });

  it('validation d’une vente : la dérogation est transmise avec les paiements', () => {
    const onConfirm = vi.fn();
    renderWithCapabilities(
      <ValidateSaleDialog
        total="750.00"
        siteId="s1"
        hasCustomer
        pending={false}
        error="Stock non périmé insuffisant"
        expiredShortages={SHORTAGES}
        onConfirm={onConfirm}
        onClose={() => undefined}
      />,
      {
        permissions: [
          'sales.sale.validate',
          'sales.sale.credit_create',
          'sales.sale.expired_lot_override',
        ],
      },
    );
    fireEvent.change(screen.getByLabelText(/Motif de la dérogation/), {
      target: { value: 'Déstockage autorisé' },
    });
    fireEvent.click(screen.getByLabelText('Je confirme vendre ces lots périmés'));
    fireEvent.click(screen.getByRole('button', { name: 'Vendre avec dérogation' }));
    expect(onConfirm).toHaveBeenCalledWith([], null, {
      reason: 'Déstockage autorisé',
      lots: [{ article_id: 'a1', lot_id: 'lotX', quantity: '2.000' }],
    });
  });

  it('POS : le reçu affiche les lots consommés et leur péremption', () => {
    renderWithCapabilities(
      <PosReceipt
        result={{
          replayed: false,
          payments: [],
          sale: saleFixture({
            number: 'VENT-BOU-2026-000050',
            total: '4500.00',
            lines: [
              {
                id: 'l1',
                line_no: 1,
                article_id: 'a1',
                article_reference: 'EAU-1',
                article_designation: 'Eau minérale',
                unit: 'bouteille',
                quantity: '30.000',
                unit_price: '150.00',
                line_total: '4500.00',
                packaging_id: null,
                packaging_name: null,
                packaging_conversion: null,
                base_quantity: '30.000',
                lots: [
                  {
                    lot_id: 'lotA',
                    lot_number: 'A',
                    expiry_date: '2026-11-01',
                    quantity: '20.000',
                  },
                  { lot_id: 'lotB', lot_number: 'B', expiry_date: null, quantity: '10.000' },
                ],
              },
            ],
          }),
        }}
        onNewSale={() => undefined}
      />,
      { permissions: ['pos.terminal.use'] },
    );
    const line = screen.getByTestId('receipt-line');
    const lots = within(line).getByRole('list', { name: 'Lots de la ligne' });
    expect(within(lots).getAllByRole('listitem').map(text)).toEqual([
      'Lot A · péremption 1 nov. 202620 bouteille',
      'Lot B10 bouteille',
    ]);
  });

  it('ligne sans lot : rien d’affiché', () => {
    renderWithCapabilities(<LineLotsList lots={[]} unit="u" locale="fr" />, { permissions: [] });
    expect(screen.queryByRole('list')).toBeNull();
  });
});
