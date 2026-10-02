// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { Toast } from 'primereact/toast';
import type { ReactNode, RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { jsonResponse, pageOf, renderWithCapabilities } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import type { Inventory, InventoryLine, InventoryLot } from './api';
import InventoryPage from './InventoryPage';
import { ALL_PERMISSIONS, inventory, line, summary } from './testData';

// Lot 3-H : inventaire d'un article suivi par lot — comptage PAR LOT (le serveur calcule tout).

function withToast(element: ReactNode, show: ReturnType<typeof vi.fn>) {
  const ref = { current: { show } as unknown as Toast } as RefObject<Toast | null>;
  return <ToastContext.Provider value={ref}>{element}</ToastContext.Provider>;
}

const calls = (fetchMock: ReturnType<typeof vi.fn<typeof fetch>>, method: string) =>
  fetchMock.mock.calls.filter(([, init]) => (init?.method ?? 'GET') === method);

const body = (call: Parameters<typeof fetch> | undefined) =>
  JSON.parse(String(call?.[1]?.body)) as unknown;

const lot = (over: Partial<InventoryLot> = {}): InventoryLot => ({
  id: 'r1',
  lot_id: 'lotA',
  lot_number: 'A',
  expiry_date: '2027-03-31',
  manufacturing_date: null,
  state: 'ok',
  discovered: false,
  stock_theoretical_initial: '60.000',
  stock_current: '60.000',
  stock_theoretical_at_validation: null,
  quantity_physical: null,
  quantity_variance: null,
  counted_at: null,
  counted_by_name: null,
  ...over,
});

const tracked = (over: Partial<InventoryLine> = {}): InventoryLine =>
  line({
    lot_tracked: true,
    stock_theoretical_initial: '100.000',
    stock_current: '100.000',
    quantity_physical: null,
    indicative_variance: null,
    quantity_variance: null,
    adjustment_value: null,
    counted_at: null,
    counted_by_name: null,
    lots: [
      lot(),
      lot({
        id: 'r2',
        lot_id: 'lotB',
        lot_number: 'B',
        expiry_date: '2026-10-10',
        state: 'expiring_soon',
        stock_theoretical_initial: '40.000',
        stock_current: '40.000',
      }),
    ],
    ...over,
  });

describe('inventaire par lot (Lot 3-H)', () => {
  const fetchMock = vi.fn<typeof fetch>();
  const show = vi.fn();
  let current: Inventory;
  let lines: InventoryLine[];
  let validateResponse: () => Response;

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    current = inventory({ line_count: 1, counted_count: 0, lot_tracked_count: 1 });
    lines = [tracked()];
    validateResponse = () => jsonResponse({ ...current, status: 'VALIDATED' });
    fetchMock.mockImplementation(async (url, init) => {
      const u = String(url);
      const method = init?.method ?? 'GET';
      if (u.includes('/lots') && method !== 'GET') {
        return jsonResponse({ lines, summary: summary() }, method === 'POST' ? 201 : 200);
      }
      if (method === 'POST' && u.endsWith('/validate')) return validateResponse();
      if (method === 'POST' && u.endsWith('/refresh-lots')) return jsonResponse(current);
      if (u.includes('/lines')) return pageOf(lines);
      return jsonResponse(current);
    });
  });

  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    show.mockReset();
    vi.unstubAllGlobals();
  });

  const render = (permissions = ALL_PERMISSIONS) =>
    renderWithCapabilities(withToast(<InventoryPage />, show), {
      permissions,
      path: '/inventories/:id',
      route: '/inventories/i1',
    });

  it('ligne suivie : lots dépliés, théorique par lot, aucune saisie au niveau de la ligne', async () => {
    render();
    const panel = await screen.findByRole('region', { name: 'Lots de RIZ-25' });
    expect(screen.getByText('Suivi par lot')).toBeTruthy();
    expect(within(panel).getByText(/Lot A/)).toBeTruthy();
    expect(within(panel).getByText(/Lot B/)).toBeTruthy();
    expect(within(panel).getByText('Bientôt périmé')).toBeTruthy();
    expect(within(panel).getByText('Théorique au démarrage : 60 sac')).toBeTruthy();
    expect(within(panel).getByText('Théorique au démarrage : 40 sac')).toBeTruthy();
    expect(within(panel).getByText(/compté 0 à la validation/)).toBeTruthy();
    // Le comptage d'un article suivi se saisit par lot, jamais globalement.
    expect(screen.queryByLabelText('Quantité physique de RIZ-25')).toBeNull();
    // Repliable.
    fireEvent.click(screen.getByRole('button', { name: 'Masquer les lots de RIZ-25' }));
    await waitFor(() =>
      expect(screen.queryByRole('region', { name: 'Lots de RIZ-25' })).toBeNull(),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Afficher les lots de RIZ-25' }));
    expect(await screen.findByRole('region', { name: 'Lots de RIZ-25' })).toBeTruthy();
  });

  it('saisie par lot : comptage de la ligne envoyé en entier (remplacement)', async () => {
    lines = [
      tracked({
        quantity_physical: '35.000',
        lots: [
          lot(),
          lot({ id: 'r2', lot_id: 'lotB', lot_number: 'B', quantity_physical: '35.000' }),
        ],
      }),
    ];
    render();
    const a = (await screen.findByLabelText('Quantité physique du lot A')) as HTMLInputElement;
    expect((screen.getByLabelText('Quantité physique du lot B') as HTMLInputElement).value).toBe(
      '35',
    );
    fireEvent.change(a, { target: { value: '55,5' } });
    fireEvent.blur(a);
    await waitFor(() => expect(calls(fetchMock, 'PUT')).toHaveLength(1));
    const put = calls(fetchMock, 'PUT')[0];
    expect(String(put?.[0])).toContain('/inventories/i1/lines/l1/lots');
    expect(body(put)).toEqual({
      counts: [
        { lot_row_id: 'r1', quantity_physical: '55.5' },
        { lot_row_id: 'r2', quantity_physical: '35' },
      ],
    });
  });

  it('saisie invalide : message, aucun envoi', async () => {
    render();
    const a = (await screen.findByLabelText('Quantité physique du lot A')) as HTMLInputElement;
    fireEvent.change(a, { target: { value: 'abc' } });
    fireEvent.blur(a);
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(calls(fetchMock, 'PUT')).toHaveLength(0);
  });

  it('conditionnement + vrac par lot : le serveur calcule la quantité en unité de base', async () => {
    lines = [
      tracked({
        packagings: [{ id: 'p1', name: 'Carton 24', conversion: '24.000' }],
        quantity_physical: '197.000',
        lots: [
          lot({
            quantity_physical: '197.000',
            count_packaging_id: 'p1',
            count_packaging_name: 'Carton 24',
            count_packaging_conversion: '24.000',
            count_packaging_quantity: '8.000',
            count_unit_quantity: '5.000',
          }),
        ],
      }),
    ];
    render();
    const cartons = (await screen.findByLabelText(
      'Nombre de Carton 24 du lot A',
    )) as HTMLInputElement;
    expect(cartons.value).toBe('8');
    const loose = screen.getByLabelText('Unités en vrac du lot A (sac)') as HTMLInputElement;
    expect(loose.value).toBe('5');
    fireEvent.change(loose, { target: { value: '6' } });
    fireEvent.blur(loose);
    await waitFor(() => expect(calls(fetchMock, 'PUT')).toHaveLength(1));
    expect(body(calls(fetchMock, 'PUT')[0])).toEqual({
      counts: [
        { lot_row_id: 'r1', packaging_id: 'p1', packaging_quantity: '8', unit_quantity: '6' },
      ],
    });
  });

  it('lot découvert : dialogue (numéro, péremption, quantité) puis retrait', async () => {
    render();
    fireEvent.click(await screen.findByRole('button', { name: 'Ajouter un lot découvert' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Lot découvert — RIZ-25')).toBeTruthy();
    const submit = within(dialog).getByRole('button', { name: 'Ajouter le lot' });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText(/Numéro de lot/), { target: { value: ' C ' } });
    fireEvent.change(within(dialog).getByLabelText(/Date de péremption/), {
      target: { value: '2027-06-30' },
    });
    fireEvent.change(within(dialog).getByLabelText('Quantité physique (sac)'), {
      target: { value: '4' },
    });
    fireEvent.click(submit);
    await waitFor(() => expect(calls(fetchMock, 'POST')).toHaveLength(1));
    const post = calls(fetchMock, 'POST')[0];
    expect(String(post?.[0])).toContain('/inventories/i1/lines/l1/lots');
    expect(body(post)).toEqual({
      lot_number: 'C',
      expiry_date: '2027-06-30',
      manufacturing_date: null,
      quantity_physical: '4',
    });

    cleanup();
    lines = [
      tracked({
        lots: [
          lot(),
          lot({
            id: 'r3',
            lot_id: null,
            lot_number: 'C',
            discovered: true,
            stock_theoretical_initial: '0.000',
            stock_current: null,
            quantity_physical: '4.000',
          }),
        ],
      }),
    ];
    render();
    const panel = await screen.findByRole('region', { name: 'Lots de RIZ-25' });
    expect(within(panel).getByText('Lot découvert')).toBeTruthy();
    // Un lot attendu ne se retire pas (non trouvé = 0) ; un lot découvert, oui.
    expect(within(panel).queryByRole('button', { name: 'Retirer le lot découvert A' })).toBeNull();
    fireEvent.click(within(panel).getByRole('button', { name: 'Retirer le lot découvert C' }));
    await waitFor(() => expect(calls(fetchMock, 'DELETE')).toHaveLength(1));
    expect(String(calls(fetchMock, 'DELETE')[0]?.[0])).toContain('/lines/l1/lots/r3');
  });

  it('lots apparus pendant le comptage : refus de la validation, liste, « Actualiser les lots »', async () => {
    current = inventory({
      status: 'READY_TO_VALIDATE',
      line_count: 1,
      counted_count: 1,
      lot_tracked_count: 1,
      summary: summary({ lines: 1, counted: 1 }),
    });
    lines = [tracked({ quantity_physical: '100.000' })];
    validateResponse = () =>
      jsonResponse(
        {
          code: 'inventory_lots_changed',
          detail: 'Des lots sont apparus',
          articles: ['RIZ-25'],
          lots: [
            {
              article_id: 'a1',
              reference: 'RIZ-25',
              lot_id: 'lotD',
              lot_number: 'D',
              quantity: '12.000',
            },
          ],
        },
        409,
      );
    render();
    // Actualisation possible à tout moment pendant le comptage ou à valider.
    expect(await screen.findByRole('button', { name: 'Actualiser les lots' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: "Valider l'inventaire" }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: "Valider l'inventaire" }));
    const alert = await screen.findByText(
      'Des lots sont apparus sur le site depuis le début du comptage',
    );
    const box = alert.closest('[role="alert"]') as HTMLElement;
    expect(within(box).getByText('RIZ-25 — lot D (12)')).toBeTruthy();
    await waitFor(() => expect(show).toHaveBeenCalled());
    fireEvent.click(within(box).getByRole('button', { name: 'Actualiser les lots' }));
    await waitFor(() =>
      expect(
        calls(fetchMock, 'POST').some(([url]) => String(url).endsWith('/i1/refresh-lots')),
      ).toBe(true),
    );
    await waitFor(() =>
      expect(
        screen.queryByText('Des lots sont apparus sur le site depuis le début du comptage'),
      ).toBeNull(),
    );
  });

  it('validé : lecture seule, comptage et écart par lot, stock à la validation', async () => {
    current = inventory({
      status: 'VALIDATED',
      line_count: 1,
      counted_count: 1,
      lot_tracked_count: 1,
      validated_at: '2026-09-24T10:00:00Z',
      validated_by_name: 'Awa',
      summary: summary({ lines: 1, counted: 1, final: true }),
    });
    lines = [
      tracked({
        quantity_physical: '197.000',
        stock_theoretical_at_validation: '100.000',
        quantity_variance: '97.000',
        lots: [
          lot({
            quantity_physical: '197.000',
            stock_theoretical_at_validation: '60.000',
            quantity_variance: '137.000',
            count_packaging_id: 'p1',
            count_packaging_name: 'Carton 24',
            count_packaging_conversion: '24.000',
            count_packaging_quantity: '8.000',
            count_unit_quantity: '5.000',
          }),
          lot({
            id: 'r2',
            lot_id: 'lotB',
            lot_number: 'B',
            quantity_physical: null,
            stock_theoretical_at_validation: '40.000',
            quantity_variance: '-40.000',
          }),
        ],
      }),
    ];
    render();
    const panel = await screen.findByRole('region', { name: 'Lots de RIZ-25' });
    expect(within(panel).getByText('8 Carton 24 + 5 sac = 197 sac')).toBeTruthy();
    expect(within(panel).getByText('Stock à la validation : 60 sac')).toBeTruthy();
    expect(within(panel).getByText('+137')).toBeTruthy();
    expect(within(panel).getByText('-40')).toBeTruthy();
    expect(within(panel).queryByRole('textbox')).toBeNull();
    expect(within(panel).queryByRole('button', { name: 'Ajouter un lot découvert' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Actualiser les lots' })).toBeNull();
  });

  it('consultation seule : lots visibles, aucune saisie ni action', async () => {
    render(['inventory_count.inventory.view']);
    const panel = await screen.findByRole('region', { name: 'Lots de RIZ-25' });
    expect(within(panel).queryByRole('textbox')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Ajouter un lot découvert' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Actualiser les lots' })).toBeNull();
  });
});
