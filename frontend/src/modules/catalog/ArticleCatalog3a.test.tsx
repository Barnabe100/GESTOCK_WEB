// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { jsonResponse, pageOf, renderWithCapabilities } from '@/shared/testing';

import ArticleDetailPage from './ArticleDetailPage';
import ArticlesPage from './ArticlesPage';

/** Lot 3-A : permissions prix / informations générales / coûts, géré en stock, historique. */
const article = (over: Record<string, unknown> = {}) => ({
  id: 'a1',
  reference: 'VIS-001',
  designation: 'Vis à bois',
  category_id: 'c1',
  category_name: 'Visserie',
  unit: 'boîte',
  main_supplier_id: null,
  main_supplier_name: null,
  sale_price: '2000.00',
  min_stock: '0.000',
  max_stock: null,
  description: null,
  barcode: null,
  is_active: true,
  stock_managed: true,
  created_at: '2026-09-24T00:00:00Z',
  updated_at: '2026-09-24T00:00:00Z',
  ...over,
});

const HISTORY = [
  {
    id: 'h2',
    occurred_at: '2026-09-30T10:00:00Z',
    user_name: 'Awa',
    sale_price_before: '1800.00',
    sale_price_after: '2000.00',
    purchase_price_before: '1400.00',
    purchase_price_after: '1500.00',
  },
  {
    id: 'h1',
    occurred_at: '2026-09-24T00:00:00Z',
    user_name: 'Moussa',
    sale_price_before: null,
    sale_price_after: '1800.00',
    purchase_price_before: null,
    purchase_price_after: '1400.00',
  },
];

const fetchMock = vi.fn<typeof fetch>();
const patches = () =>
  fetchMock.mock.calls
    .filter(([, init]) => init?.method === 'PATCH')
    .map(([, init]) => JSON.parse(String(init?.body)) as Record<string, unknown>);

let current = article();

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
  current = article();
  fetchMock.mockImplementation(async (url, init) => {
    const u = String(url);
    if (init?.method === 'PATCH') return jsonResponse(current);
    if (u.includes('/price-history')) return pageOf(HISTORY);
    if (u.includes('/catalog/articles/a1')) return jsonResponse(current);
    if (u.includes('/catalog/articles')) return pageOf([current]);
    if (u.includes('/catalog/categories')) {
      return pageOf([{ id: 'c1', name: 'Visserie', is_active: true }]);
    }
    return pageOf([]);
  });
});

afterEach(() => {
  cleanup();
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

const openEdit = async (permissions: string[]) => {
  renderWithCapabilities(<ArticlesPage />, { permissions });
  await screen.findByText('VIS-001');
  fireEvent.click(screen.getByRole('button', { name: 'Modifier' }));
  return screen.findByRole('dialog');
};

describe('fiche et formulaire article (Lot 3-A)', () => {
  it('informations générales sans droit sur les prix : prix en lecture seule, jamais envoyés', async () => {
    const dialog = await openEdit(['catalog.article.view', 'catalog.article.update']);
    const sale = within(dialog).getByLabelText(/^Prix de vente/) as HTMLInputElement;
    expect(sale.readOnly).toBe(true);
    expect(within(dialog).getByText(/la modification des prix exige un droit dédié/)).toBeTruthy();
    // Sans cost_view : aucun champ de prix d'achat.
    expect(within(dialog).queryByLabelText(/^Prix d'achat/)).toBeNull();
    fireEvent.change(within(dialog).getByLabelText(/^Désignation/), {
      target: { value: 'Vis inox' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() => expect(patches()).toHaveLength(1));
    const body = patches()[0] ?? {};
    expect(body.designation).toBe('Vis inox');
    expect('sale_price' in body).toBe(false);
    expect('purchase_price' in body).toBe(false);
  });

  it('droit sur les prix seulement : informations générales en lecture seule', async () => {
    current = article({ purchase_price: '1500.00' });
    const dialog = await openEdit([
      'catalog.article.view',
      'catalog.article.price_update',
      'catalog.article.cost_view',
    ]);
    expect((within(dialog).getByLabelText(/^Désignation/) as HTMLInputElement).readOnly).toBe(true);
    fireEvent.change(within(dialog).getByLabelText(/^Prix de vente/), {
      target: { value: '2 500' },
    });
    fireEvent.change(within(dialog).getByLabelText(/^Prix d'achat/), {
      target: { value: '1600' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() => expect(patches()).toHaveLength(1));
    expect(patches()[0]).toEqual({ sale_price: '2500', purchase_price: '1600' });
  });

  it('géré en stock : case à cocher, seuils masqués pour un article non géré', async () => {
    const dialog = await openEdit(['catalog.article.view', 'catalog.article.update']);
    const managed = within(dialog).getByLabelText('Géré en stock') as HTMLInputElement;
    expect(managed.checked).toBe(true);
    expect(within(dialog).getByLabelText(/^Stock minimum/)).toBeTruthy();
    fireEvent.click(managed);
    expect(within(dialog).queryByLabelText(/^Stock minimum/)).toBeNull();
    expect(within(dialog).getByText(/vendu sans mouvement ni contrôle de stock/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() => expect(patches()).toHaveLength(1));
    expect(patches()[0]?.stock_managed).toBe(false);
  });

  it('liste : article non géré signalé', async () => {
    current = article({ stock_managed: false });
    renderWithCapabilities(<ArticlesPage />, { permissions: ['catalog.article.view'] });
    expect(await screen.findByText('Non géré en stock')).toBeTruthy();
    // Sans droit de modification (ni général ni prix) : pas d'action Modifier.
    expect(screen.queryByRole('button', { name: 'Modifier' })).toBeNull();
  });

  it('fiche : coûts et historique des prix selon les permissions', async () => {
    current = article({ purchase_price: '1500.00' });
    const view = renderWithCapabilities(<ArticleDetailPage />, {
      permissions: [
        'catalog.article.view',
        'catalog.article.price_update',
        'catalog.article.cost_view',
      ],
      path: '/catalog/articles/:id',
      route: '/catalog/articles/a1',
    });
    expect(await screen.findByRole('heading', { name: 'Historique des prix' })).toBeTruthy();
    expect(await screen.findByText('Awa')).toBeTruthy();
    expect(screen.getByText('Moussa')).toBeTruthy();
    expect(screen.getAllByText("Prix d'achat").length).toBeGreaterThan(0);
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/a1/price-history?'))).toBe(true);
    view.unmount();

    // Consultation simple : ni historique (non demandé au serveur) ni coût.
    fetchMock.mockClear();
    current = article();
    renderWithCapabilities(<ArticleDetailPage />, {
      permissions: ['catalog.article.view'],
      path: '/catalog/articles/:id',
      route: '/catalog/articles/a1',
    });
    expect(await screen.findByRole('heading', { name: 'Vis à bois' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'Historique des prix' })).toBeNull();
    expect(screen.queryByText("Prix d'achat")).toBeNull();
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes('price-history'))).toBe(false);
  });
});
