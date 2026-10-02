// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { Toast } from 'primereact/toast';
import type { ReactNode, RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import InventoryPage from '@/modules/inventory_count/InventoryPage';
import { ALL_PERMISSIONS, inventory, line, summary } from '@/modules/inventory_count/testData';
import type { PosArticle } from '@/modules/pos/api';
import PosPage from '@/modules/pos/PosPage';
import SalePage from '@/modules/sales/SalePage';
import { EntryPage } from '@/modules/stock/DocumentPage';
import TransferPage from '@/modules/stock/TransferPage';
import { jsonResponse, pageOf, renderWithCapabilities, SITES } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import { BarcodesSection } from './BarcodesSection';

/** Lot 3-D : codes-barres multiples, codes des conditionnements et scan des présentations. */
const article = (over: Record<string, unknown> = {}) => ({
  id: 'a1',
  reference: 'COCA-33',
  designation: 'Coca-Cola 33 cl',
  category_id: 'c1',
  category_name: 'Boissons',
  unit: 'bouteille',
  main_supplier_id: null,
  main_supplier_name: null,
  sale_price: '500.00',
  min_stock: '0.000',
  max_stock: null,
  description: null,
  barcode: '111',
  is_active: true,
  stock_managed: true,
  decimal_quantity_allowed: false,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
  ...over,
});

const packaging = (over: Record<string, unknown> = {}) => ({
  id: 'p24',
  article_id: 'a1',
  name: 'Carton 24',
  conversion: '24.000',
  sale_price: '10500.00',
  is_active: true,
  in_use: false,
  created_at: '',
  updated_at: '',
  ...over,
});

const barcode = (over: Record<string, unknown> = {}) => ({
  id: 'b1',
  article_id: 'a1',
  packaging_id: null,
  packaging_name: null,
  code: '222',
  kind: 'ADDITIONAL',
  is_active: true,
  created_at: '',
  ...over,
});

function withToast(element: ReactNode) {
  const ref = { current: { show: vi.fn() } as unknown as Toast } as RefObject<Toast | null>;
  return <ToastContext.Provider value={ref}>{element}</ToastContext.Provider>;
}

const fetchMock = vi.fn<typeof fetch>();
const calls = (method: string) =>
  fetchMock.mock.calls.filter(([, init]) => (init?.method ?? 'GET') === method);
const urls = (part: string) =>
  fetchMock.mock.calls.map(([url]) => String(url)).filter((u) => u.includes(part));

/** Résolution EXACTE simulée : 111 → bouteille, C-1 → carton, F-1 → fardeau sans prix. */
function resolve(url: string): Response {
  const code = new URL(url, 'http://x').searchParams.get('code');
  if (code === '111') return jsonResponse({ article: article(), packaging: null });
  if (code === 'C-1') return jsonResponse({ article: article(), packaging: packaging() });
  if (code === 'F-1') {
    return jsonResponse({
      article: article(),
      packaging: packaging({
        id: 'p12',
        name: 'Fardeau 12',
        conversion: '12.000',
        sale_price: null,
      }),
    });
  }
  return jsonResponse({ code: 'barcode_unknown', detail: 'Code-barres inconnu' }, 404);
}

const scan = (label: string, code: string) => {
  const input = screen.getByLabelText(label);
  fireEvent.change(input, { target: { value: code } });
  fireEvent.keyDown(input, { key: 'Enter' });
};

beforeEach(() => vi.stubGlobal('fetch', fetchMock));
afterEach(() => {
  cleanup();
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

describe('fiche article : codes-barres (Lot 3-D)', () => {
  const render = (permissions = ['catalog.article.view', 'catalog.article.update']) =>
    renderWithCapabilities(withToast(<BarcodesSection article={article() as never} />), {
      permissions,
    });

  it('code principal, codes supplémentaires et codes de chaque conditionnement', async () => {
    fetchMock.mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes('/barcodes')) {
        return pageOf([
          barcode({ id: 'b0', code: '111', kind: 'PRIMARY' }),
          barcode(),
          barcode({ id: 'b2', code: '333' }),
          barcode({
            id: 'b3',
            code: 'C-1',
            kind: 'PACKAGING',
            packaging_id: 'p24',
            packaging_name: 'Carton 24',
          }),
          barcode({
            id: 'b4',
            code: 'C-2',
            kind: 'PACKAGING',
            packaging_id: 'p24',
            packaging_name: 'Carton 24',
          }),
          barcode({
            id: 'b5',
            code: 'P-1',
            kind: 'PACKAGING',
            packaging_id: 'p6',
            packaging_name: 'Pack 6',
            is_active: false,
          }),
        ]);
      }
      return pageOf([
        packaging({ id: 'p6', name: 'Pack 6', conversion: '6.000', is_active: false }),
        packaging(),
      ]);
    });
    render();
    expect(await screen.findByText('333')).toBeTruthy();
    // Code principal : affiché (modifié sur l'article), jamais retirable ici.
    expect(screen.getByText('111')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Retirer le code-barres 111' })).toBeNull();
    const base = screen.getByRole('group', { name: "l'unité de base (bouteille)" });
    expect(within(base).getByText('222')).toBeTruthy();
    const carton = screen.getByRole('group', { name: 'Carton 24' });
    expect(within(carton).getByText('C-1')).toBeTruthy();
    expect(within(carton).getByText('C-2')).toBeTruthy();
    // Conditionnement inactif : ses codes restent affichés, « libérés ».
    const pack = screen.getByRole('group', { name: 'Pack 6' });
    expect(within(pack).getByText('P-1')).toBeTruthy();
    expect(within(pack).getByText('Libéré (élément inactif)')).toBeTruthy();
  });

  it('ajout (unité de base, conditionnement) et retrait confirmé', async () => {
    fetchMock.mockImplementation(async (url, init) => {
      const u = String(url);
      if (init?.method === 'POST') return jsonResponse(barcode({ code: 'NEW' }), 201);
      if (init?.method === 'DELETE') return new Response(null, { status: 204 });
      if (u.includes('/barcodes')) return pageOf([barcode()]);
      return pageOf([packaging()]);
    });
    render();
    await screen.findByText('222');
    fireEvent.change(
      screen.getByLabelText("Nouveau code-barres pour l'unité de base (bouteille)"),
      {
        target: { value: ' 444 ' },
      },
    );
    fireEvent.click(
      screen.getByRole('button', { name: "Ajouter le code-barres à l'unité de base (bouteille)" }),
    );
    await waitFor(() => expect(calls('POST')).toHaveLength(1));
    expect(String(calls('POST')[0]?.[0])).toContain('/catalog/articles/a1/barcodes');
    expect(JSON.parse(String(calls('POST')[0]?.[1]?.body))).toEqual({ code: '444' });
    // Code d'un conditionnement : route du conditionnement.
    const input = screen.getByLabelText('Nouveau code-barres pour Carton 24');
    fireEvent.change(input, { target: { value: 'C-9' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(calls('POST')).toHaveLength(2));
    expect(String(calls('POST')[1]?.[0])).toContain('/catalog/packagings/p24/barcodes');
    // Retrait : confirmation, puis suppression.
    fireEvent.click(screen.getByRole('button', { name: 'Retirer le code-barres 222' }));
    const dialog = await screen.findByRole('dialog', { name: 'Retirer le code-barres' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Retirer le code-barres' }));
    await waitFor(() => expect(calls('DELETE')).toHaveLength(1));
    expect(String(calls('DELETE')[0]?.[0])).toContain('/catalog/barcodes/b1');
  });

  it('sans catalog.article.update : consultation seule', async () => {
    fetchMock.mockImplementation(async (url) =>
      String(url).includes('/barcodes') ? pageOf([barcode()]) : pageOf([packaging()]),
    );
    render(['catalog.article.view']);
    expect(await screen.findByText('222')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Retirer le code-barres/ })).toBeNull();
    expect(screen.queryByLabelText(/Nouveau code-barres/)).toBeNull();
  });
});

describe('scan des écrans opérationnels (Lot 3-D)', () => {
  it('entrée : le scan présélectionne article + conditionnement, quantité à saisir', async () => {
    fetchMock.mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes('/barcodes/resolve')) return resolve(u);
      if (u.includes('/packagings')) return pageOf([packaging()]);
      return pageOf([]);
    });
    renderWithCapabilities(withToast(<EntryPage />), {
      permissions: ['stock.entry.view', 'stock.entry.create', 'catalog.article.view'],
      sites: [SITES[0] as (typeof SITES)[number]],
      path: '/stock/entries/new',
      route: '/stock/entries/new',
    });
    await screen.findByLabelText('Scanner un code-barres');
    scan('Scanner un code-barres', 'C-1');
    const quantity = (await screen.findByLabelText(/^Quantité \(Carton 24\)/)) as HTMLInputElement;
    expect(quantity.value).toBe(''); // jamais devinée
    expect(screen.getByLabelText(/^Coût unitaire \(par Carton 24\)/)).toBeTruthy();
    expect(screen.getByRole('status').textContent).toBe(
      'Identifié : COCA-33 — Coca-Cola 33 cl (Carton 24)',
    );
    // Second scan de la même présentation : aucune ligne en double.
    scan('Scanner un code-barres', 'C-1');
    await waitFor(() => expect(urls('/barcodes/resolve')).toHaveLength(2));
    expect(screen.getAllByLabelText(/^Quantité \(Carton 24\)/)).toHaveLength(1);
    // Code de l'article : unité de base.
    scan('Scanner un code-barres', '111');
    expect(await screen.findByLabelText(/^Quantité \(bouteille\)/)).toBeTruthy();
    // Valeur exacte transmise, une seule requête par scan, aucune recherche partielle.
    expect(
      urls('/barcodes/resolve').map((u) => new URL(u, 'http://x').searchParams.get('code')),
    ).toEqual(['C-1', 'C-1', '111']);
  });

  it('code inconnu : message explicite, aucune ligne ajoutée', async () => {
    fetchMock.mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes('/barcodes/resolve')) return resolve(u);
      return pageOf([]);
    });
    renderWithCapabilities(withToast(<TransferPage />), {
      features: ['stock.transfers'],
      permissions: ['stock.transfer.view', 'stock.transfer.create', 'catalog.article.view'],
      path: '/stock/transfers/new',
      route: '/stock/transfers/new',
    });
    await screen.findByLabelText('Scanner un code-barres');
    scan('Scanner un code-barres', 'C-12');
    expect((await screen.findByRole('alert')).textContent).toBe('Code-barres inconnu : C-12');
    expect(screen.getByText('Ajoutez au moins un article.')).toBeTruthy();
  });

  it('vente : conditionnement au prix non configuré refusé ; carton ajouté avec sa présentation', async () => {
    fetchMock.mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes('/barcodes/resolve')) return resolve(u);
      if (u.includes('/packagings')) return pageOf([packaging()]);
      return pageOf([]);
    });
    renderWithCapabilities(withToast(<SalePage />), {
      permissions: ['sales.sale.view', 'sales.sale.create', 'catalog.article.view'],
      path: '/sales/new',
      route: '/sales/new',
    });
    await screen.findByLabelText('Scanner un code-barres');
    scan('Scanner un code-barres', 'F-1');
    expect((await screen.findByRole('alert')).textContent).toContain(
      "Le prix de ce conditionnement n'est pas encore configuré (Fardeau 12)",
    );
    expect(screen.getByText('Ajoutez au moins un article.')).toBeTruthy();
    scan('Scanner un code-barres', 'C-1');
    const quantity = (await screen.findByLabelText(/Quantité/, {
      selector: 'input[id^="line-0-quantity"]',
    })) as HTMLInputElement;
    // Valeur posée par le formulaire après l'ajout de la ligne (mise à jour asynchrone).
    await waitFor(() => expect(quantity.value).toBe('1'));
    // Second scan : la même ligne passe à 2 (jamais 24 unités de base).
    scan('Scanner un code-barres', 'C-1');
    await waitFor(() => expect(quantity.value).toBe('2.000'));
  });

  it('inventaire : ligne exacte de l’article, présentation présélectionnée, quantité non devinée', async () => {
    const counting = line({
      quantity_physical: null,
      indicative_variance: null,
      quantity_variance: null,
      adjustment_value: null,
      unit: 'bouteille',
      reference: 'COCA-33',
      article_id: 'a1',
      packagings: [
        { id: 'p6', name: 'Pack 6', conversion: '6.000' },
        { id: 'p24', name: 'Carton 24', conversion: '24.000' },
      ],
    });
    fetchMock.mockImplementation(async (url, init) => {
      const u = String(url);
      if (u.includes('/barcodes/resolve')) return resolve(u);
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
    scan('Scanner un code-barres', 'C-1');
    const quantity = (await screen.findByLabelText(
      'Nombre de Carton 24 comptés pour COCA-33',
    )) as HTMLInputElement;
    expect(quantity.value).toBe('');
    await waitFor(() => expect(document.activeElement).toBe(quantity));
    expect(urls('/lines').at(-1)).toContain('article_id=a1');
    expect(screen.getByText('Scan : COCA-33 — Carton 24')).toBeTruthy();
    expect(calls('PATCH')).toHaveLength(0); // rien n'est compté par le scan
  });
});

describe('point de vente : scan d’un conditionnement (Lot 3-D)', () => {
  const COCA: PosArticle = {
    article_id: 'a1',
    reference: 'COCA-33',
    designation: 'Coca-Cola',
    unit: 'bouteille',
    category_name: 'Boissons',
    sale_price: '500.00',
    quantity: '100.000',
    is_active: true,
    stock_managed: true,
    decimal_quantity_allowed: false,
    packagings: [
      { id: 'p6', name: 'Pack 6', conversion: '6.000', sale_price: '2800.00' },
      { id: 'p24', name: 'Carton 24', conversion: '24.000', sale_price: '10500.00' },
    ],
  };

  it('code du carton : 1 Carton 24 au panier ; prix non configuré : message, rien ajouté', async () => {
    fetchMock.mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes('/pos/articles/by-barcode')) {
        const code = new URL(u, 'http://x').searchParams.get('barcode');
        if (code === 'C-1') return jsonResponse({ ...COCA, scanned_packaging_id: 'p24' });
        return jsonResponse(
          { code: 'packaging_price_not_set', detail: '…', packagings: ['Fardeau 12'] },
          422,
        );
      }
      if (u.includes('/pos/articles')) return jsonResponse([COCA]);
      return pageOf([]);
    });
    renderWithCapabilities(withToast(<PosPage />), {
      permissions: [
        'pos.terminal.use',
        'sales.sale.view',
        'sales.sale.create',
        'sales.sale.validate',
      ],
      sites: [SITES[0] as (typeof SITES)[number]],
      path: '/pos',
      route: '/pos',
    });
    await screen.findByRole('button', { name: 'Ajouter Coca-Cola au panier' });
    const search = screen.getByLabelText('Rechercher un article (F2)');
    fireEvent.change(search, { target: { value: 'F-1' } });
    fireEvent.keyDown(search, { key: 'Enter' });
    expect((await screen.findByRole('alert')).textContent).toContain('(Fardeau 12)');
    expect(screen.getByText('Panier vide : ajoutez des articles.')).toBeTruthy();
    fireEvent.change(search, { target: { value: 'C-1' } });
    fireEvent.keyDown(search, { key: 'Enter' });
    const quantity = (await screen.findByLabelText(
      'Quantité de Coca-Cola (Carton 24)',
    )) as HTMLInputElement;
    expect(quantity.value).toBe('1');
  });
});
