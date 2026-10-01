// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import ArticleDetailPage from '@/modules/catalog/ArticleDetailPage';
import type { InventoryLine } from '@/modules/inventory_count/api';
import InventoryPage from '@/modules/inventory_count/InventoryPage';
import { ALL_PERMISSIONS, inventory, line, summary } from '@/modules/inventory_count/testData';
import { jsonResponse, pageOf, renderWithCapabilities } from '@/shared/testing';

import { EntryPage } from './DocumentPage';
import LocationsPage from './LocationsPage';
import StockLevelsPage from './StockLevelsPage';

/** Lot 3-F : emplacements par site (page, niveaux, inventaire, documents, fiche article). */

const location = (over: Record<string, unknown> = {}) => ({
  id: 'l1',
  site_id: 's1',
  site_name: 'Boutique',
  name: 'Rayon A',
  is_active: true,
  article_count: 2,
  created_at: '2026-10-01T08:00:00Z',
  updated_at: '2026-10-01T08:00:00Z',
  ...over,
});

const level = (over: Record<string, unknown> = {}) => ({
  site_id: 's1',
  site_name: 'Boutique',
  article_id: 'a1',
  reference: 'VIS-001',
  designation: 'Vis à bois',
  unit: 'boîte',
  category_name: 'Visserie',
  article_active: true,
  quantity: '10.000',
  min_stock: '0.000',
  max_stock: null,
  min_override: null,
  max_override: null,
  state: 'ok',
  location_id: 'l1',
  location_name: 'Rayon A',
  location_active: true,
  ...over,
});

const MANAGE = ['stock.level.view', 'stock.location.manage'];

describe('emplacements par site (Lot 3-F)', () => {
  const fetchMock = vi.fn<typeof fetch>();
  const calls = (method = 'GET') =>
    fetchMock.mock.calls
      .filter(([, init]) => (init?.method ?? 'GET') === method)
      .map(([u, init]) => [String(u), init?.body ? JSON.parse(String(init.body)) : null]);

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it('page Emplacements : liste par site et création avec choix obligatoire du site', async () => {
    fetchMock.mockImplementation(async (_url, init) => {
      if ((init?.method ?? 'GET') !== 'GET') return jsonResponse(location(), 201);
      return pageOf([
        location(),
        location({ id: 'l2', name: 'Réserve', is_active: false, article_count: 0 }),
      ]);
    });
    renderWithCapabilities(<LocationsPage />, { permissions: MANAGE });
    const row = (await screen.findByText('Rayon A')).closest('tr') as HTMLElement;
    expect(within(row).getByText('2')).toBeTruthy();
    expect(within(row).getByText('Boutique')).toBeTruthy(); // plusieurs sites : colonne Site
    // Création : aucun site sélectionné et plusieurs sites → choix obligatoire.
    fireEvent.click(screen.getByRole('button', { name: 'Nouvel emplacement' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/Nom de l'emplacement/), {
      target: { value: 'Allée 2' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer' }));
    expect(await within(dialog).findByText('Champ obligatoire')).toBeTruthy();
    expect(calls('POST')).toEqual([]);
    const site = within(dialog).getByLabelText(/Site/, { selector: 'input, select, span, div' });
    fireEvent.click(site.closest('.p-dropdown') ?? site);
    fireEvent.click(
      screen.getAllByRole('option', { name: 'Dépôt', hidden: true }).at(-1) as Element,
    );
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() =>
      expect(calls('POST')).toEqual([
        ['/api/v1/stock/locations', { site_id: 's2', name: 'Allée 2' }],
      ]),
    );
  });

  it('sans la permission : consultation seule', async () => {
    fetchMock.mockImplementation(async () => pageOf([location()]));
    renderWithCapabilities(<LocationsPage />, { permissions: ['stock.level.view'] });
    await screen.findByText('Rayon A');
    expect(screen.queryByRole('button', { name: 'Nouvel emplacement' })).toBeNull();
    expect(screen.queryByRole('columnheader', { name: 'Actions' })).toBeNull();
  });

  it('niveaux : colonne emplacement, non rangé, filtre et affectation au site de la ligne', async () => {
    fetchMock.mockImplementation(async (url, init) => {
      const u = String(url);
      if ((init?.method ?? 'GET') === 'PUT')
        return jsonResponse(level({ location_name: 'Réserve' }));
      if (u.includes('/stock/locations')) {
        return pageOf([location(), location({ id: 'l3', name: 'Réserve', article_count: 0 })]);
      }
      return pageOf([
        level(),
        level({ article_id: 'a2', reference: 'CLOU-1', location_id: null, location_name: null }),
        level({
          article_id: 'a3',
          reference: 'COLLE-1',
          location_id: 'l9',
          location_name: 'Ancien rayon',
          location_active: false,
        }),
      ]);
    });
    renderWithCapabilities(<StockLevelsPage />, { permissions: MANAGE });
    const placed = (await screen.findByText('VIS-001')).closest('tr') as HTMLElement;
    expect(within(placed).getByText('Rayon A')).toBeTruthy();
    const unplaced = screen.getByText('CLOU-1').closest('tr') as HTMLElement;
    expect(within(unplaced).getByText('Non rangé')).toBeTruthy();
    const old = screen.getByText('COLLE-1').closest('tr') as HTMLElement;
    expect(within(old).getByText('Ancien rayon')).toBeTruthy();
    expect(within(old).getByText('Inactif')).toBeTruthy();
    // Filtre « Non rangés » : appliqué par le serveur.
    const filter = screen.getByLabelText('Emplacement', { selector: 'input, select, span, div' });
    fireEvent.click(filter.closest('.p-dropdown') ?? filter);
    fireEvent.click(
      screen.getAllByRole('option', { name: 'Non rangés', hidden: true }).at(-1) as Element,
    );
    await waitFor(() =>
      expect(
        calls().some(
          ([u]) => String(u).includes('/stock/levels?') && String(u).includes('unlocated=true'),
        ),
      ).toBe(true),
    );
    // Affectation : emplacements ACTIFS du site de la ligne seulement.
    fireEvent.click(within(placed).getByRole('button', { name: 'Emplacement' }));
    const dialog = await screen.findByRole('dialog');
    await waitFor(() =>
      expect(
        calls().some(
          ([u]) => String(u).includes('site_id=s1') && String(u).includes('status=active'),
        ),
      ).toBe(true),
    );
    const field = within(dialog).getByLabelText('Emplacement', {
      selector: 'input, select, span, div',
    });
    fireEvent.click(field.closest('.p-dropdown') ?? field);
    fireEvent.click(
      screen.getAllByRole('option', { name: 'Réserve', hidden: true }).at(-1) as Element,
    );
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() =>
      expect(calls('PUT')).toEqual([
        ['/api/v1/stock/levels/s1/a1/location', { location_id: 'l3' }],
      ]),
    );
  });

  it('niveaux sans stock.location.manage : aucune affectation', async () => {
    fetchMock.mockImplementation(async (url) =>
      String(url).includes('/stock/locations') ? pageOf([]) : pageOf([level()]),
    );
    renderWithCapabilities(<StockLevelsPage />, { permissions: ['stock.level.view'] });
    await screen.findByText('VIS-001');
    expect(screen.queryByRole('button', { name: 'Emplacement' })).toBeNull();
  });

  it('inventaire : emplacement courant et tri par emplacement', async () => {
    const lines: InventoryLine[] = [
      line({ location_name: 'Rayon A' }),
      line({ id: 'il2', article_id: 'a2', reference: 'CLOU-1', location_name: null }),
    ];
    fetchMock.mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes('/lines')) return pageOf(lines);
      if (u.includes('/summary')) return jsonResponse(summary());
      return jsonResponse(inventory());
    });
    renderWithCapabilities(<InventoryPage />, {
      permissions: ALL_PERMISSIONS,
      path: '/inventories/:id',
      route: '/inventories/i1',
    });
    const header = await screen.findByRole('columnheader', { name: /Emplacement/ });
    // Colonne (grand écran) et rappel sous l'article (petit écran, CSS non appliqué ici).
    expect((await screen.findAllByText('Rayon A')).length).toBe(2);
    const unplaced = screen.getByText('CLOU-1').closest('tr') as HTMLElement;
    expect(within(unplaced).getAllByText('Non rangé')).toHaveLength(2);
    fireEvent.click(header);
    await waitFor(() =>
      expect(
        calls().some(([u]) => String(u).includes('/lines?') && String(u).includes('sort=location')),
      ).toBe(true),
    );
  });

  it('entrée : emplacement courant de chaque ligne, à titre indicatif', async () => {
    const entry = {
      id: 'e1',
      number: 'ENT-000001',
      site_id: 's1',
      site_name: 'Boutique',
      status: 'VALIDATED',
      operation_date: '2026-09-20',
      comment: null,
      line_count: 2,
      created_at: '2026-09-20T08:00:00Z',
      created_by_name: 'Awa',
      validated_at: '2026-09-20T08:05:00Z',
      validated_by_name: 'Awa',
      cancelled_at: null,
      cancelled_by_name: null,
      cancellation_reason: null,
      kind: 'PURCHASE',
      supplier_id: 'f1',
      supplier_name: 'Faso Import',
      document_reference: null,
      lines: [
        {
          id: 'x1',
          line_no: 1,
          article_id: 'a1',
          article_reference: 'VIS-001',
          article_designation: 'Vis',
          unit: 'boîte',
          quantity: '10.000',
          location_name: 'Rayon A',
        },
        {
          id: 'x2',
          line_no: 2,
          article_id: 'a2',
          article_reference: 'CLOU-1',
          article_designation: 'Clous',
          unit: 'boîte',
          quantity: '5.000',
          location_name: null,
        },
      ],
    };
    fetchMock.mockImplementation(async () => jsonResponse(entry));
    renderWithCapabilities(<EntryPage />, {
      permissions: ['stock.entry.view'],
      path: '/stock/entries/:id',
      route: '/stock/entries/e1',
    });
    const first = (await screen.findByText('VIS-001 — Vis')).closest('tr') as HTMLElement;
    expect(within(first).getByText('Rayon A')).toBeTruthy();
    const second = screen.getByText('CLOU-1 — Clous').closest('tr') as HTMLElement;
    expect(within(second).getByText('Non rangé')).toBeTruthy();
  });

  it('fiche article : stock et emplacement par site visible', async () => {
    const article = {
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
    };
    fetchMock.mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes('/stock/levels')) {
        return pageOf([
          level(),
          level({
            site_id: 's2',
            site_name: 'Dépôt',
            quantity: '4.000',
            location_id: 'l7',
            location_name: 'Zone palettes',
          }),
        ]);
      }
      if (u.includes('/catalog/articles/a1?') || u.endsWith('/catalog/articles/a1'))
        return jsonResponse(article);
      return pageOf([]);
    });
    renderWithCapabilities(<ArticleDetailPage />, {
      permissions: ['catalog.article.view', ...MANAGE],
      path: '/catalog/articles/:id',
      route: '/catalog/articles/a1',
    });
    const section = await screen.findByRole('region', { name: 'Stock et emplacement par site' });
    const depot = (await within(section).findByText('Dépôt')).closest('tr') as HTMLElement;
    expect(within(depot).getByText('Zone palettes')).toBeTruthy();
    const shop = within(section).getByText('Boutique').closest('tr') as HTMLElement;
    expect(within(shop).getByText('Rayon A')).toBeTruthy();
    expect(
      calls().some(
        ([u]) => String(u).includes('/stock/levels?') && String(u).includes('article_id=a1'),
      ),
    ).toBe(true);
  });

  it('fiche article sans stock.level.view : aucune vue par site', async () => {
    fetchMock.mockImplementation(async (url) =>
      String(url).includes('/catalog/articles/a1')
        ? jsonResponse({
            ...level(),
            id: 'a1',
            designation: 'Vis à bois',
            stock_managed: true,
            is_active: true,
            category_id: 'c1',
            sale_price: '1',
            barcode: null,
            description: null,
            main_supplier_id: null,
            main_supplier_name: null,
            created_at: '2026-09-24T00:00:00Z',
            updated_at: '2026-09-24T00:00:00Z',
          })
        : pageOf([]),
    );
    renderWithCapabilities(<ArticleDetailPage />, {
      permissions: ['catalog.article.view'],
      path: '/catalog/articles/:id',
      route: '/catalog/articles/a1',
    });
    await screen.findByRole('heading', { name: 'Vis à bois' });
    expect(screen.queryByText('Stock et emplacement par site')).toBeNull();
    expect(calls().some(([u]) => String(u).includes('/stock/'))).toBe(false);
  });
});
