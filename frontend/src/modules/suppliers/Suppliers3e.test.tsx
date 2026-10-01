// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { EntriesPage } from '@/modules/stock/DocumentsPage';
import ArticlesPage from '@/modules/catalog/ArticlesPage';
import { jsonResponse, renderWithCapabilities } from '@/shared/testing';

import SupplierDetailPage from './SupplierDetailPage';
import SuppliersPage from './SuppliersPage';

const SUPPLIER = {
  id: 's1',
  name: 'Faso Import',
  contact_name: 'Awa Traoré',
  phone: '70 11 22 33',
  email: null,
  address: null,
  city: 'Ouagadougou',
  country: null,
  notes: 'Livraison le mardi',
  is_active: true,
  created_at: '2026-09-24T08:00:00Z',
  updated_at: '2026-09-25T09:30:00Z',
};

const page = <T,>(items: T[]) => ({ items, total: items.length, limit: 25, offset: 0 });

const entry = (number: string, status: string, total: string | undefined) => ({
  id: `e-${number}`,
  number,
  site_id: 'site-1',
  site_name: 'Boutique',
  status,
  operation_date: '2026-09-28',
  comment: null,
  line_count: 1,
  created_at: '2026-09-28T08:00:00Z',
  created_by_name: 'Moi',
  validated_at: null,
  validated_by_name: null,
  cancelled_at: null,
  cancelled_by_name: null,
  cancellation_reason: null,
  kind: 'PURCHASE',
  supplier_id: 's1',
  supplier_name: 'Faso Import',
  document_reference: 'BL-77',
  lines: [],
  ...(total === undefined ? {} : { total_amount: total }),
});

const RECEIVED = {
  article_id: 'a1',
  article_reference: 'VIS-001',
  article_designation: 'Vis à bois',
  unit: 'u',
  article_active: true,
  receipt_count: 2,
  received_base_quantity: '14.000',
  last_received_on: '2026-09-26',
  last_entry_id: 'e-ENT-000002',
  last_entry_number: 'ENT-000002',
  last_unit_cost: '150.0000',
};

const HISTORY = [
  {
    id: 'h1',
    occurred_at: '2026-09-24T08:00:00Z',
    action: 'supplier.created',
    user_name: 'Moi',
    data: { name: 'Faso Import' },
  },
  {
    id: 'h2',
    occurred_at: '2026-09-25T09:30:00Z',
    action: 'supplier.updated',
    user_name: 'Moi',
    data: { phone: { before: null, after: '70 11 22 33' }, city: { before: null, after: 'X' } },
  },
];

/** Réponses du serveur ; `costs = false` : comme le serveur sans cost_view (champs absents). */
function serve(costs: boolean) {
  return async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/stock/suppliers/s1/summary')) {
      return jsonResponse({
        supplier_id: 's1',
        validated_count: 2,
        last_received_on: '2026-09-26',
        ...(costs ? { received_total: '2600.00' } : {}),
      });
    }
    if (url.includes('/stock/suppliers/s1/articles')) {
      const { last_unit_cost: cost, ...rest } = RECEIVED;
      return jsonResponse(page([costs ? { ...RECEIVED, last_unit_cost: cost } : rest]));
    }
    if (url.includes('/stock/entries')) {
      return jsonResponse(
        page([
          entry('ENT-000002', 'VALIDATED', costs ? '600.00' : undefined),
          entry('ENT-000004', 'CANCELLED', costs ? '3219.00' : undefined),
        ]),
      );
    }
    if (url.includes('/suppliers/s1/history')) return jsonResponse(HISTORY);
    if (url.includes('/catalog/articles')) {
      return jsonResponse(
        page([
          {
            id: 'a9',
            reference: 'CLOU-9',
            designation: 'Clous',
            category_name: 'Quincaillerie',
            is_active: true,
          },
        ]),
      );
    }
    if (url.includes('/suppliers/s1')) return jsonResponse(SUPPLIER);
    if (url.includes('/suppliers')) return jsonResponse(page([SUPPLIER]));
    return jsonResponse(page([]));
  };
}

const ALL = [
  'suppliers.supplier.view',
  'stock.entry.view',
  'catalog.article.view',
  'catalog.article.cost_view',
  'audit.log.view',
];

describe('fiche fournisseur (Lot 3-E)', () => {
  const fetchMock = vi.fn<typeof fetch>();
  const calls = () => fetchMock.mock.calls.map(([u]) => String(u));

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockImplementation(serve(true));
  });

  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  const renderDetail = (permissions: string[]) =>
    renderWithCapabilities(<SupplierDetailPage />, {
      permissions,
      path: '/suppliers/:id',
      route: '/suppliers/s1',
    });

  it('affiche la fiche, la synthèse et les réceptions (annulées comprises)', async () => {
    renderDetail(ALL);
    expect(await screen.findByRole('heading', { name: 'Faso Import' })).toBeTruthy();
    expect(screen.getByText('Awa Traoré')).toBeTruthy();
    expect(screen.getByText('Livraison le mardi')).toBeTruthy();
    const metrics = screen.getByRole('group', { name: 'Réceptions validées' });
    await waitFor(() => expect(within(metrics).getByText('2')).toBeTruthy());
    expect(within(metrics).getByText(/2\s600\sF\s?CFA/)).toBeTruthy();
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      'Réceptions',
      'Articles reçus',
      'Fournisseur principal',
      'Chronologie',
    ]);
    const cancelled = (await screen.findByText('ENT-000004')).closest('tr') as HTMLElement;
    expect(within(cancelled).getByText('Annulé')).toBeTruthy();
    expect(calls().some((u) => u.includes('supplier_id=s1') && u.includes('kind=PURCHASE'))).toBe(
      true,
    );
  });

  it('articles reçus : quantité en unité de base et dernier coût avec cost_view', async () => {
    renderDetail(ALL);
    fireEvent.click(await screen.findByRole('tab', { name: 'Articles reçus' }));
    const row = (await screen.findByText('Vis à bois')).closest('tr') as HTMLElement;
    expect(within(row).getByText('14 u')).toBeTruthy();
    expect(within(row).getByText('ENT-000002')).toBeTruthy();
    expect(within(row).getByText(/150\sF\s?CFA \/ u/)).toBeTruthy();
  });

  it('sans cost_view : ni total reçu, ni dernier coût, ni montants', async () => {
    fetchMock.mockImplementation(serve(false));
    renderDetail(ALL.filter((p) => p !== 'catalog.article.cost_view'));
    await screen.findByText('ENT-000002');
    expect(screen.queryByText('Total reçu')).toBeNull();
    expect(screen.queryByText(/F\s?CFA/)).toBeNull();
    fireEvent.click(screen.getByRole('tab', { name: 'Articles reçus' }));
    await screen.findByText('Vis à bois');
    expect(screen.queryByText('Dernier coût')).toBeNull();
    expect(screen.queryByText(/F\s?CFA/)).toBeNull();
  });

  it('fournisseur principal et chronologie réelle', async () => {
    renderDetail(ALL);
    fireEvent.click(await screen.findByRole('tab', { name: 'Fournisseur principal' }));
    expect(await screen.findByText('Clous')).toBeTruthy();
    expect(
      calls().some((u) => u.includes('/catalog/articles?') && u.includes('supplier_id=s1')),
    ).toBe(true);
    fireEvent.click(screen.getByRole('tab', { name: 'Chronologie' }));
    expect(await screen.findByText('Fournisseur créé')).toBeTruthy();
    expect(screen.getByText('Fournisseur modifié')).toBeTruthy();
    expect(screen.getByText('Champs modifiés : Téléphone, Ville')).toBeTruthy();
  });

  it('sans droits complémentaires : fiche seule, aucune requête de stock ni d’audit', async () => {
    renderDetail(['suppliers.supplier.view']);
    expect(await screen.findByRole('heading', { name: 'Faso Import' })).toBeTruthy();
    expect(screen.queryByRole('tab')).toBeNull();
    expect(screen.queryByText('Réceptions validées')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Modifier' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Désactiver' })).toBeNull();
    expect(calls().filter((u) => u.includes('/stock/') || u.includes('/history'))).toEqual([]);
  });

  it('erreur de chargement : message et nouvel essai', async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse({ code: 'supplier_not_found', title: 'Fournisseur introuvable' }, 404),
    );
    renderDetail(ALL);
    expect(await screen.findByRole('button', { name: 'Réessayer' })).toBeTruthy();
  });

  it('liste : une ligne ouvre la fiche', async () => {
    renderWithCapabilities(<SuppliersPage />, {
      permissions: ['suppliers.supplier.view'],
      path: '/suppliers',
      route: '/suppliers',
      extraRoutes: [{ path: '/suppliers/:id', element: <p>fiche ouverte</p> }],
    });
    fireEvent.click(await screen.findByText('Faso Import'));
    expect(await screen.findByText('fiche ouverte')).toBeTruthy();
  });

  it('filtre fournisseur des entrées et des articles, appliqué par le serveur', async () => {
    renderWithCapabilities(<EntriesPage />, {
      permissions: ['stock.entry.view', 'suppliers.supplier.view'],
    });
    await screen.findByText('ENT-000002');
    const filter = screen.getByLabelText('Fournisseur', { selector: 'input, select, span, div' });
    fireEvent.click(filter.closest('.p-dropdown') ?? filter);
    fireEvent.click(
      screen.getAllByRole('option', { name: 'Faso Import', hidden: true }).at(-1) as Element,
    );
    await waitFor(() =>
      expect(
        calls().some((u) => u.includes('/stock/entries?') && u.includes('supplier_id=s1')),
      ).toBe(true),
    );
    cleanup();
    fetchMock.mockClear();
    renderWithCapabilities(<ArticlesPage />, {
      permissions: ['catalog.article.view', 'suppliers.supplier.view'],
    });
    await screen.findByText('Clous');
    const articles = screen.getByLabelText('Fournisseur principal', {
      selector: 'input, select, span, div',
    });
    fireEvent.click(articles.closest('.p-dropdown') ?? articles);
    fireEvent.click(
      screen.getAllByRole('option', { name: 'Faso Import', hidden: true }).at(-1) as Element,
    );
    await waitFor(() =>
      expect(
        calls().some((u) => u.includes('/catalog/articles?') && u.includes('supplier_id=s1')),
      ).toBe(true),
    );
  });

  it('sans le droit fournisseurs : aucun filtre fournisseur', async () => {
    renderWithCapabilities(<EntriesPage />, { permissions: ['stock.entry.view'] });
    await screen.findByText('ENT-000002');
    expect(
      screen.queryByLabelText('Fournisseur', { selector: 'input, select, span, div' }),
    ).toBeNull();
    expect(calls().some((u) => u.includes('/suppliers'))).toBe(false);
  });
});
