// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { Toast } from 'primereact/toast';
import type { ReactNode, RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import InventoryPage from '@/modules/inventory_count/InventoryPage';
import { ALL_PERMISSIONS, inventory, line, summary } from '@/modules/inventory_count/testData';
import { jsonResponse, pageOf, renderWithCapabilities } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import { EntryPage } from './DocumentPage';
import MovementsPage from './MovementsPage';

/** Lot 3-C : présentations et équivalences dans les opérations de stock. */
const text = (el: Element | null) => (el?.textContent ?? '').replace(/\s/g, ' ');

const PACKAGINGS = [
  { id: 'p6', article_id: 'a1', name: 'Pack 6', conversion: '6.000', sale_price: null },
  { id: 'p24', article_id: 'a1', name: 'Carton 24', conversion: '24.000', sale_price: '9000' },
].map((p) => ({ ...p, is_active: true, in_use: false, created_at: '', updated_at: '' }));

const entry = (over: Record<string, unknown> = {}) => ({
  id: 'e1',
  number: 'ENT-000001',
  site_id: 's1',
  site_name: 'Boutique',
  status: 'DRAFT',
  operation_date: '2026-09-20',
  comment: null,
  total_amount: '120000.00',
  line_count: 1,
  created_at: '2026-09-20T08:00:00Z',
  created_by_name: 'Awa',
  validated_at: null,
  validated_by_name: null,
  cancelled_at: null,
  cancelled_by_name: null,
  cancellation_reason: null,
  kind: 'PURCHASE',
  supplier_id: null,
  supplier_name: null,
  document_reference: null,
  lines: [
    {
      id: 'l1',
      line_no: 1,
      article_id: 'a1',
      article_reference: 'COCA-33',
      article_designation: 'Coca-Cola 33 cl',
      unit: 'bouteille',
      quantity: '10.000',
      unit_cost: '12000.00',
      amount: '120000.00',
      packaging_id: 'p24',
      packaging_name: 'Carton 24',
      packaging_conversion: '24.000',
      base_quantity: '240.000',
    },
  ],
  ...over,
});

function withToast(element: ReactNode) {
  const ref = { current: { show: vi.fn() } as unknown as Toast } as RefObject<Toast | null>;
  return <ToastContext.Provider value={ref}>{element}</ToastContext.Provider>;
}

const fetchMock = vi.fn<typeof fetch>();
const bodies = (method: string) =>
  fetchMock.mock.calls
    .filter(([, init]) => init?.method === method)
    .map(([, init]) => JSON.parse(String(init?.body)) as Record<string, unknown>);

beforeEach(() => vi.stubGlobal('fetch', fetchMock));
afterEach(() => {
  cleanup();
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

const chooseOption = async (label: string, option: RegExp) => {
  const field = screen.getByLabelText(label, { selector: 'input, select, span, div' });
  fireEvent.click(field.closest('.p-dropdown') ?? field);
  fireEvent.click((await screen.findAllByRole('option', { name: option, hidden: true })).at(-1)!);
};

describe('opérations de stock en conditionnement (Lot 3-C)', () => {
  it('entrée : présentation, équivalences en unité de base et dans les conditionnements', async () => {
    fetchMock.mockImplementation(async (url, init) => {
      if (String(url).includes('/packagings')) return pageOf(PACKAGINGS);
      if (init?.method === 'PUT') return jsonResponse(entry());
      return jsonResponse(entry());
    });
    renderWithCapabilities(withToast(<EntryPage />), {
      permissions: ['stock.entry.view', 'stock.entry.update', 'catalog.article.view'],
      path: '/stock/entries/:id',
      route: '/stock/entries/e1',
    });
    // Conditionnement saisi : « 10 Carton 24 = 240 bouteille », coût par carton.
    expect(await screen.findByLabelText(/^Quantité \(Carton 24\)/)).toBeTruthy();
    expect(screen.getByLabelText(/^Coût unitaire \(par Carton 24\)/)).toBeTruthy();
    await waitFor(() =>
      expect(text(screen.getByTestId('presentation-equivalence'))).toBe(
        '10 Carton 24 = 240 bouteille',
      ),
    );
    // Unité de base : 48 bouteilles = 8 Pack 6 = 2 Carton 24.
    await chooseOption('Présentation', /Unité de base/);
    fireEvent.change(screen.getByLabelText(/^Quantité \(bouteille\)/), { target: { value: '48' } });
    await waitFor(() =>
      expect(text(screen.getByTestId('presentation-equivalence'))).toBe(
        '48 bouteille = 8 Pack 6 = 2 Carton 24',
      ),
    );
    // Retour au carton : le serveur reçoit la présentation, jamais de quantité de base.
    await chooseOption('Présentation', /Carton 24/);
    fireEvent.change(screen.getByLabelText(/^Quantité \(Carton 24\)/), { target: { value: '2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer le brouillon' }));
    await waitFor(() => expect(bodies('PUT')).toHaveLength(1));
    const lines = bodies('PUT')[0]?.lines as Record<string, unknown>[];
    expect(lines[0]).toMatchObject({ article_id: 'a1', packaging_id: 'p24', quantity: '2' });
    expect(JSON.stringify(bodies('PUT')[0])).not.toContain('base_quantity');
  });

  it('entrée validée : présentation et quantité de base dans le détail', async () => {
    fetchMock.mockImplementation(async () => jsonResponse(entry({ status: 'VALIDATED' })));
    renderWithCapabilities(<EntryPage />, {
      permissions: ['stock.entry.view'],
      path: '/stock/entries/:id',
      route: '/stock/entries/e1',
    });
    expect(await screen.findByText('10 Carton 24 = 240 bouteille')).toBeTruthy();
  });

  it('journal : présentation saisie et quantité en unité de base', async () => {
    fetchMock.mockImplementation(async () =>
      pageOf([
        {
          id: 'm1',
          occurred_at: '2026-09-24T08:00:00Z',
          site_id: 's1',
          site_name: 'Boutique',
          article_id: 'a1',
          article_reference: 'COCA-33',
          article_designation: 'Coca-Cola 33 cl',
          unit: 'bouteille',
          movement_type: 'EXIT',
          quantity: '-72.000',
          quantity_before: '100.000',
          quantity_after: '28.000',
          source_type: 'stock_exit',
          source_id: 'x1',
          document_number: 'SOR-000001',
          origin_movement_id: null,
          user_name: 'Awa',
          comment: null,
          packaging_name: 'Carton 24',
          packaging_conversion: '24.000',
          packaging_quantity: '3.000',
        },
      ]),
    );
    renderWithCapabilities(<MovementsPage />, { permissions: ['stock.movement.view'] });
    expect(await screen.findByText('-3 Carton 24')).toBeTruthy();
    expect(screen.getByText(/→ -72 bouteille/)).toBeTruthy();
  });

  it('inventaire : comptage en conditionnement + unités en vrac, équivalence et envoi', async () => {
    const counting = line({
      quantity_physical: null,
      indicative_variance: null,
      quantity_variance: null,
      adjustment_value: null,
      unit: 'bouteille',
      reference: 'COCA-33',
      packagings: [
        { id: 'p6', name: 'Pack 6', conversion: '6.000' },
        { id: 'p24', name: 'Carton 24', conversion: '24.000' },
      ],
    });
    fetchMock.mockImplementation(async (url, init) => {
      const u = String(url);
      if (init?.method === 'PATCH') return jsonResponse({ lines: [counting], summary: summary() });
      if (u.includes('/lines')) return pageOf([counting]);
      return jsonResponse(inventory());
    });
    renderWithCapabilities(withToast(<InventoryPage />), {
      permissions: ALL_PERMISSIONS,
      path: '/inventories/:id',
      route: '/inventories/i1',
    });
    await screen.findByLabelText('Quantité physique de COCA-33');
    await chooseOption('Présentation du comptage de COCA-33', /Carton 24/);
    fireEvent.change(screen.getByLabelText('Nombre de Carton 24 comptés pour COCA-33'), {
      target: { value: '8' },
    });
    fireEvent.change(screen.getByLabelText('bouteille en vrac pour COCA-33'), {
      target: { value: '5' },
    });
    expect(text(screen.getByTestId('count-equivalence-l1'))).toBe('= 197 bouteille');
    fireEvent.blur(screen.getByLabelText('bouteille en vrac pour COCA-33'));
    await waitFor(() => expect(bodies('PATCH')).toHaveLength(1));
    expect(bodies('PATCH')[0]).toEqual({
      counts: [{ line_id: 'l1', packaging_id: 'p24', packaging_quantity: '8', unit_quantity: '5' }],
    });
  });

  it('inventaire : comptage en unité de base avec ses équivalences ; affichage du comptage', async () => {
    const counted = line({
      status: undefined,
      unit: 'bouteille',
      reference: 'COCA-33',
      quantity_physical: '197.000',
      count_packaging_id: 'p24',
      count_packaging_name: 'Carton 24',
      count_packaging_conversion: '24.000',
      count_packaging_quantity: '8.000',
      count_unit_quantity: '5.000',
    } as never);
    fetchMock.mockImplementation(async (url) =>
      String(url).includes('/lines')
        ? pageOf([counted])
        : jsonResponse(inventory({ status: 'READY_TO_VALIDATE' })),
    );
    renderWithCapabilities(withToast(<InventoryPage />), {
      permissions: ALL_PERMISSIONS,
      path: '/inventories/:id',
      route: '/inventories/i1',
    });
    const cell = await screen.findByText('8 Carton 24 + 5 bouteille = 197 bouteille');
    expect(within(cell.closest('tr') as HTMLElement).getByText('COCA-33')).toBeTruthy();
  });
});
