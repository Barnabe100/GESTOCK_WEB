// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import i18n from 'i18next';
import type { Toast } from 'primereact/toast';
import type { ReactNode, RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/core/api/client';
import ArticleDetailPage from '@/modules/catalog/ArticleDetailPage';
import ArticlesPage from '@/modules/catalog/ArticlesPage';
import { jsonResponse, pageOf, renderWithCapabilities } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import { EntryPage } from './DocumentPage';
import LotDetailPage from './LotDetailPage';
import LotsPage from './LotsPage';
import MovementsPage from './MovementsPage';
import { stockError } from './ui';

/** Lot 3-G : lots et péremption (fermeture P1-b, réception, page Lots, fiche lot, fiche article). */

const article = (over: Record<string, unknown> = {}) => ({
  id: 'a1',
  reference: 'LAIT-1',
  designation: 'Lait UHT',
  category_id: 'c1',
  category_name: 'Laitiers',
  unit: 'brique',
  main_supplier_id: null,
  main_supplier_name: null,
  sale_price: '900.00',
  min_stock: '0.000',
  max_stock: null,
  description: null,
  barcode: null,
  is_active: true,
  stock_managed: true,
  decimal_quantity_allowed: false,
  lot_tracked: false,
  expiry_tracked: false,
  created_at: '2026-09-24T00:00:00Z',
  updated_at: '2026-09-24T00:00:00Z',
  ...over,
});

const lot = (over: Record<string, unknown> = {}) => ({
  id: 'lot1',
  article_id: 'a1',
  article_reference: 'LAIT-1',
  article_designation: 'Lait UHT',
  unit: 'brique',
  number: 'L001',
  expiry_date: '2026-10-15',
  manufacturing_date: '2026-09-01',
  state: 'expiring_soon',
  quantity: '58.000',
  site_count: 1,
  created_at: '2026-10-02T08:00:00Z',
  ...over,
});

const entryLine = (over: Record<string, unknown> = {}) => ({
  id: 'x1',
  line_no: 1,
  article_id: 'a1',
  article_reference: 'LAIT-1',
  article_designation: 'Lait UHT',
  unit: 'brique',
  quantity: '10.000',
  unit_cost: '700.00',
  amount: '7000.00',
  packaging_id: null,
  packaging_name: null,
  packaging_conversion: null,
  base_quantity: '10.000',
  location_name: null,
  lot_id: null,
  lot_number: 'L001',
  lot_expiry_date: '2026-10-15',
  lot_manufacturing_date: null,
  lot_state: 'expiring_soon',
  ...over,
});

const entry = (over: Record<string, unknown> = {}) => ({
  id: 'e1',
  number: 'ENT-000001',
  site_id: 's1',
  site_name: 'Boutique',
  status: 'DRAFT',
  operation_date: '2026-10-02',
  comment: null,
  total_amount: '7000.00',
  line_count: 1,
  created_at: '2026-10-02T08:00:00Z',
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
  lines: [entryLine()],
  ...over,
});

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

function mockArticles(available: boolean, current = article()) {
  fetchMock.mockImplementation(async (url, init) => {
    const u = String(url);
    if (init?.method === 'PATCH') return jsonResponse(current);
    if (u.includes('/catalog/lot-tracking')) return jsonResponse({ available });
    if (u.includes('/catalog/articles/a1')) return jsonResponse(current);
    if (u.includes('/catalog/articles')) return pageOf([current]);
    if (u.includes('/catalog/categories')) {
      return pageOf([{ id: 'c1', name: 'Laitiers', is_active: true }]);
    }
    return pageOf([]);
  });
}

const openEdit = async () => {
  renderWithCapabilities(<ArticlesPage />, {
    permissions: ['catalog.article.view', 'catalog.article.update'],
  });
  await screen.findByText('LAIT-1');
  fireEvent.click(screen.getByRole('button', { name: 'Modifier' }));
  return screen.findByRole('dialog');
};

describe('lots et péremption (Lot 3-G)', () => {
  it('P1-b : suivi par lot non proposé tant que la consommation des lots n’est pas livrée', async () => {
    mockArticles(false);
    const dialog = await openEdit();
    expect(await within(dialog).findByTestId('lot-tracking-unavailable')).toBeTruthy();
    expect(within(dialog).queryByLabelText('Suivi par lot')).toBeNull();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() => expect(calls('PATCH')).toHaveLength(1));
    const body = calls('PATCH')[0]?.[1] as Record<string, unknown>;
    expect(body).not.toHaveProperty('lot_tracked');
    expect(body).not.toHaveProperty('expiry_tracked');
  });

  it('suivi activable (tests) : péremption seulement avec le suivi par lot', async () => {
    mockArticles(true);
    const dialog = await openEdit();
    const lots = await within(dialog).findByLabelText('Suivi par lot');
    const expiry = within(dialog).getByLabelText('Suivi de la date de péremption');
    expect((expiry as HTMLInputElement).disabled).toBe(true);
    fireEvent.click(lots);
    await waitFor(() => expect((expiry as HTMLInputElement).disabled).toBe(false));
    fireEvent.click(expiry);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() => expect(calls('PATCH')).toHaveLength(1));
    expect(calls('PATCH')[0]?.[1]).toMatchObject({ lot_tracked: true, expiry_tracked: true });
  });

  it('réception : lot, péremption et fabrication saisis, contrôles d’ergonomie, envoi', async () => {
    fetchMock.mockImplementation(async (url, init) => {
      if (init?.method === 'PUT') return jsonResponse(entry());
      if (String(url).includes('/packagings')) return pageOf([]);
      return jsonResponse(entry());
    });
    renderWithCapabilities(withToast(<EntryPage />), {
      permissions: ['stock.entry.view', 'stock.entry.update', 'catalog.article.view'],
      path: '/stock/entries/:id',
      route: '/stock/entries/e1',
    });
    const number = (await screen.findByLabelText(/^Numéro de lot/)) as HTMLInputElement;
    expect(number.value).toBe('L001');
    expect((screen.getByLabelText(/^Date de péremption/) as HTMLInputElement).value).toBe(
      '2026-10-15',
    );
    // Lot obligatoire pour un article suivi ; fabrication postérieure à la péremption refusée.
    fireEvent.change(number, { target: { value: ' ' } });
    fireEvent.change(screen.getByLabelText(/^Date de fabrication/), {
      target: { value: '2026-10-20' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer le brouillon' }));
    expect(await screen.findByText('Champ obligatoire')).toBeTruthy();
    expect(
      screen.getByText(
        'La date de fabrication ne peut pas être postérieure à la date de péremption',
      ),
    ).toBeTruthy();
    expect(calls('PUT')).toHaveLength(0);
    fireEvent.change(number, { target: { value: ' L002 ' } });
    fireEvent.change(screen.getByLabelText(/^Date de fabrication/), {
      target: { value: '2026-09-01' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer le brouillon' }));
    await waitFor(() => expect(calls('PUT')).toHaveLength(1));
    const lines = (calls('PUT')[0]?.[1] as { lines: Record<string, unknown>[] }).lines;
    expect(lines[0]).toMatchObject({
      article_id: 'a1',
      lot_number: 'L002',
      lot_expiry_date: '2026-10-15',
      lot_manufacturing_date: '2026-09-01',
    });
  });

  it('réception validée : lot, péremption et état sous l’article', async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse(entry({ status: 'VALIDATED', lines: [entryLine({ lot_id: 'lot1' })] })),
    );
    renderWithCapabilities(<EntryPage />, {
      permissions: ['stock.entry.view'],
      path: '/stock/entries/:id',
      route: '/stock/entries/e1',
    });
    const cell = (await screen.findByText('LAIT-1 — Lait UHT')).closest('td') as HTMLElement;
    expect(within(cell).getByText(/Lot L001 · péremption/)).toBeTruthy();
    expect(within(cell).getByText('Bientôt périmé')).toBeTruthy();
  });

  it('page Lots : états, seuil du tenant, filtres et modification du seuil', async () => {
    fetchMock.mockImplementation(async (url, init) => {
      const u = String(url);
      if (u.includes('/stock/settings')) {
        return jsonResponse({ expiry_warning_days: init?.method === 'PUT' ? 10 : 30 });
      }
      return pageOf([
        lot({ id: 'lot0', number: 'L000', expiry_date: '2026-09-30', state: 'expired' }),
        lot(),
        lot({ id: 'lot2', number: 'L999', expiry_date: null, state: 'no_expiry' }),
      ]);
    });
    renderWithCapabilities(withToast(<LotsPage />), {
      permissions: ['stock.level.view', 'stock.threshold.manage'],
    });
    const expired = (await screen.findByText('L000')).closest('tr') as HTMLElement;
    expect(within(expired).getByText('Périmé')).toBeTruthy();
    expect(
      within(screen.getByText('L001').closest('tr') as HTMLElement).getByText('Bientôt périmé'),
    ).toBeTruthy();
    expect(
      within(screen.getByText('L999').closest('tr') as HTMLElement).getByText('Sans péremption'),
    ).toBeTruthy();
    expect(screen.getByTestId('lots-threshold').textContent).toContain('30 jours');
    // Tri par défaut : échéance la plus proche.
    expect(
      calls().some(([u]) => u.includes('/stock/lots?') && u.includes('sort=expiry_date')),
    ).toBe(true);
    fireEvent.change(screen.getByLabelText("Péremption jusqu'au"), {
      target: { value: '2026-10-31' },
    });
    await waitFor(() =>
      expect(calls().some(([u]) => u.includes('expires_before=2026-10-31'))).toBe(true),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Modifier le seuil' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/^Nombre de jours/), {
      target: { value: '10' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() =>
      expect(calls('PUT')).toEqual([['/api/v1/stock/settings', { expiry_warning_days: 10 }]]),
    );
  });

  it('page Lots sans stock.threshold.manage : seuil affiché, non modifiable', async () => {
    fetchMock.mockImplementation(async (url) =>
      String(url).includes('/stock/settings')
        ? jsonResponse({ expiry_warning_days: 30 })
        : pageOf([lot()]),
    );
    renderWithCapabilities(<LotsPage />, { permissions: ['stock.level.view'] });
    expect(await screen.findByText('L001')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Modifier le seuil' })).toBeNull();
  });

  it('fiche lot : soldes par site, réceptions et mouvements selon les permissions', async () => {
    fetchMock.mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes('/stock/lots/lot1')) {
        return jsonResponse({
          ...lot(),
          balances: [
            { site_id: 's1', site_name: 'Boutique', quantity: '40.000' },
            { site_id: 's2', site_name: 'Dépôt', quantity: '18.000' },
          ],
        });
      }
      if (u.includes('/stock/entries')) return pageOf([entry({ status: 'VALIDATED' })]);
      return pageOf([]);
    });
    renderWithCapabilities(<LotDetailPage />, {
      permissions: ['stock.level.view', 'stock.entry.view'],
      path: '/stock/lots/:id',
      route: '/stock/lots/lot1',
    });
    expect(await screen.findByRole('heading', { name: 'Lot L001' })).toBeTruthy();
    const balances = screen.getByRole('region', { name: 'Solde par site' });
    expect(within(balances).getByText('Dépôt')).toBeTruthy();
    expect(within(balances).getByText('18 brique')).toBeTruthy();
    expect(await screen.findByText('ENT-000001')).toBeTruthy();
    expect(calls().some(([u]) => u.includes('/stock/entries?') && u.includes('lot_id=lot1'))).toBe(
      true,
    );
    // Sans stock.movement.view : aucun appel au journal.
    expect(calls().some(([u]) => u.includes('/stock/movements'))).toBe(false);
  });

  it('fiche article suivie par lot : lots de l’article sur les sites visibles', async () => {
    mockArticles(false, article({ lot_tracked: true, expiry_tracked: true }));
    const base = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (url, init) =>
      String(url).includes('/stock/lots') ? pageOf([lot()]) : base(url, init),
    );
    renderWithCapabilities(<ArticleDetailPage />, {
      permissions: ['catalog.article.view', 'stock.level.view'],
      path: '/catalog/articles/:id',
      route: '/catalog/articles/a1',
    });
    expect(await screen.findByText('Par lot, avec date de péremption')).toBeTruthy();
    const section = await screen.findByRole('region', { name: 'Lots' });
    expect(await within(section).findByText('L001')).toBeTruthy();
    expect(calls().some(([u]) => u.includes('/stock/lots?') && u.includes('article_id=a1'))).toBe(
      true,
    );
  });

  it('journal des mouvements : lot de la réception', async () => {
    fetchMock.mockImplementation(async () =>
      pageOf([
        {
          id: 'm1',
          occurred_at: '2026-10-02T08:00:00Z',
          site_id: 's1',
          site_name: 'Boutique',
          article_id: 'a1',
          article_reference: 'LAIT-1',
          article_designation: 'Lait UHT',
          unit: 'brique',
          movement_type: 'ENTRY',
          quantity: '10.000',
          quantity_before: '0.000',
          quantity_after: '10.000',
          source_type: 'stock_entry',
          source_id: 'e1',
          document_number: 'ENT-000001',
          origin_movement_id: null,
          user_name: 'Awa',
          comment: null,
          lot_id: 'lot1',
          lot_number: 'L001',
          lot_expiry_date: '2026-10-15',
        },
      ]),
    );
    renderWithCapabilities(<MovementsPage />, { permissions: ['stock.movement.view'] });
    const cell = (await screen.findByText('LAIT-1 — Lait UHT')).closest('td') as HTMLElement;
    expect(within(cell).getByText(/Lot L001 · péremption/)).toBeTruthy();
  });

  it('erreur « solde de lot insuffisant » détaillée', () => {
    const error = new ApiError(422, 'insufficient_lot_stock', 'x', {
      lots: [{ lot_number: 'L1', available: '4.000' }],
    });
    expect(stockError(i18n.t.bind(i18n), error)).toBe(
      'Solde de lot insuffisant : lot L1 (solde 4).',
    );
  });
});
