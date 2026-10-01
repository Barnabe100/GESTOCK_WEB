// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { jsonResponse, pageOf, renderWithCapabilities } from '@/shared/testing';

import ArticleDetailPage from './ArticleDetailPage';
import ArticlesPage from './ArticlesPage';

/** Lot 3-B : quantités décimales et conditionnements sur la fiche article. */
const article = (over: Record<string, unknown> = {}) => ({
  id: 'a1',
  reference: 'COCA-33',
  designation: 'Coca-Cola',
  category_id: 'c1',
  category_name: 'Boissons',
  unit: 'pièce',
  main_supplier_id: null,
  main_supplier_name: null,
  sale_price: '500.00',
  min_stock: '0.000',
  max_stock: null,
  description: null,
  barcode: null,
  is_active: true,
  stock_managed: true,
  decimal_quantity_allowed: false,
  created_at: '2026-09-24T00:00:00Z',
  updated_at: '2026-09-24T00:00:00Z',
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
  created_at: '2026-09-24T00:00:00Z',
  updated_at: '2026-09-24T00:00:00Z',
  ...over,
});

const fetchMock = vi.fn<typeof fetch>();
const writes = (method: string) =>
  fetchMock.mock.calls
    .filter(([, init]) => init?.method === method)
    .map(([url, init]) => [String(url), JSON.parse(String(init?.body ?? '{}'))] as const);

let packagings = [packaging()];

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
  packagings = [packaging()];
  fetchMock.mockImplementation(async (url, init) => {
    const u = String(url);
    if (init?.method === 'POST' || init?.method === 'PATCH') return jsonResponse(packaging());
    if (u.includes('/packagings')) return pageOf(packagings);
    if (u.includes('/catalog/articles/a1')) return jsonResponse(article());
    if (u.includes('/catalog/articles')) return pageOf([article()]);
    if (u.includes('/catalog/categories')) {
      return pageOf([{ id: 'c1', name: 'Boissons', is_active: true }]);
    }
    return pageOf([]);
  });
});

afterEach(() => {
  cleanup();
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

const detail = (permissions: string[]) =>
  renderWithCapabilities(<ArticleDetailPage />, {
    permissions,
    path: '/catalog/articles/:id',
    route: '/catalog/articles/a1',
  });

const ADMIN = ['catalog.article.view', 'catalog.article.update', 'catalog.article.price_update'];

describe('quantités décimales et conditionnements (Lot 3-B)', () => {
  it('case « quantités décimales » : décochée par défaut, envoyée au serveur', async () => {
    renderWithCapabilities(<ArticlesPage />, {
      permissions: ['catalog.article.view', 'catalog.article.update'],
    });
    await screen.findByText('COCA-33');
    fireEvent.click(screen.getByRole('button', { name: 'Modifier' }));
    const dialog = await screen.findByRole('dialog');
    const decimal = within(dialog).getByLabelText(
      'Quantités décimales autorisées',
    ) as HTMLInputElement;
    expect(decimal.checked).toBe(false);
    fireEvent.click(decimal);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() => expect(writes('PATCH')).toHaveLength(1));
    expect(writes('PATCH')[0]?.[1].decimal_quantity_allowed).toBe(true);
  });

  it('fiche : unité de base, conditionnements, création (nom, conversion, prix)', async () => {
    packagings = [
      packaging(),
      packaging({ id: 'p6', name: 'Pack 6', conversion: '6.000', is_active: false }),
    ];
    detail(ADMIN);
    expect(await screen.findByRole('heading', { name: 'Conditionnements de vente' })).toBeTruthy();
    expect(await screen.findByText('Carton 24')).toBeTruthy();
    expect(screen.getByText('24 pièce')).toBeTruthy();
    expect(screen.getByText('Pack 6')).toBeTruthy();
    expect(screen.getByText(/L'unité de base \(pièce/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Nouveau conditionnement' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/Nombre entier d'unités de base/)).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText(/^Nom/), { target: { value: 'Pack 12' } });
    fireEvent.change(within(dialog).getByLabelText(/^Contient/), { target: { value: '12' } });
    fireEvent.change(within(dialog).getByLabelText(/^Prix de vente/), {
      target: { value: '5 600' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() => expect(writes('POST')).toHaveLength(1));
    expect(writes('POST')[0]).toEqual([
      expect.stringContaining('/catalog/articles/a1/packagings'),
      { name: 'Pack 12', conversion: '12', sale_price: '5600' },
    ]);
  });

  it('conditionnement utilisé : conversion figée ; prix seul envoyé sans droit général', async () => {
    packagings = [packaging({ in_use: true })];
    detail(['catalog.article.view', 'catalog.article.price_update']);
    expect(await screen.findByText('Utilisé en vente')).toBeTruthy();
    // Sans droit général : ni création ni désactivation proposées.
    expect(screen.queryByRole('button', { name: 'Nouveau conditionnement' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Désactiver' })).toBeNull();
    const row = screen.getByText('Carton 24').closest('tr') as HTMLElement;
    fireEvent.click(within(row).getByRole('button', { name: 'Modifier' }));
    const dialog = await screen.findByRole('dialog', { name: /Modifier le conditionnement/ });
    expect((within(dialog).getByLabelText(/^Contient/) as HTMLInputElement).readOnly).toBe(true);
    expect((within(dialog).getByLabelText(/^Nom/) as HTMLInputElement).readOnly).toBe(true);
    expect(within(dialog).getByText(/Conversion figée/)).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText(/^Prix de vente/), {
      target: { value: '11000' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() => expect(writes('PATCH')).toHaveLength(1));
    expect(writes('PATCH')[0]).toEqual([
      expect.stringContaining('/catalog/packagings/p24'),
      { sale_price: '11000' },
    ]);
  });

  it('droit général sans droit sur les prix : prix en lecture seule, jamais envoyé', async () => {
    detail(['catalog.article.view', 'catalog.article.update']);
    fireEvent.click(await screen.findByRole('button', { name: 'Nouveau conditionnement' }));
    const dialog = await screen.findByRole('dialog');
    expect((within(dialog).getByLabelText(/^Prix de vente/) as HTMLInputElement).readOnly).toBe(
      true,
    );
    fireEvent.change(within(dialog).getByLabelText(/^Nom/), { target: { value: 'Pack 6' } });
    fireEvent.change(within(dialog).getByLabelText(/^Contient/), { target: { value: '6' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() => expect(writes('POST')).toHaveLength(1));
    expect(writes('POST')[0]?.[1]).toEqual({ name: 'Pack 6', conversion: '6' });
  });

  it('prix non configuré : signalé, distinct de 0 ; un habilité le fixe', async () => {
    packagings = [
      packaging({ id: 'p6', name: 'Pack 6', conversion: '6.000', sale_price: null }),
      packaging({ id: 'p0', name: 'Offert', conversion: '1.000', sale_price: '0.00' }),
    ];
    detail(ADMIN);
    const unpriced = (await screen.findByText('Pack 6')).closest('tr') as HTMLElement;
    expect(within(unpriced).getByText('Prix non configuré')).toBeTruthy();
    // Prix configuré à 0 : affiché comme un prix, pas comme « non configuré ».
    const zero = screen.getByText('Offert').closest('tr') as HTMLElement;
    expect(within(zero).queryByText('Prix non configuré')).toBeNull();
    fireEvent.click(within(unpriced).getByRole('button', { name: 'Modifier' }));
    const dialog = await screen.findByRole('dialog', { name: /Modifier le conditionnement/ });
    const price = within(dialog).getByLabelText(/^Prix de vente/) as HTMLInputElement;
    expect(price.value).toBe('');
    // Sans prix saisi : rien n'est envoyé pour le prix (il reste non configuré).
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() => expect(writes('PATCH')).toHaveLength(1));
    expect('sale_price' in (writes('PATCH')[0]?.[1] ?? {})).toBe(false);
  });

  it('sans droit sur les prix : conditionnement créé au prix non configuré', async () => {
    packagings = [packaging({ sale_price: null })];
    detail(['catalog.article.view', 'catalog.article.update']);
    expect(await screen.findByText('Prix non configuré')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Nouveau conditionnement' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/restera invendable/)).toBeTruthy();
    expect((within(dialog).getByLabelText(/^Prix de vente/) as HTMLInputElement).value).toBe('');
  });

  it('désactivation confirmée ; réactivation directe', async () => {
    packagings = [packaging(), packaging({ id: 'p6', name: 'Pack 6', is_active: false })];
    detail(ADMIN);
    await screen.findByText('Carton 24');
    fireEvent.click(screen.getByRole('button', { name: 'Désactiver' }));
    const confirm = await screen.findByRole('dialog');
    expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith('/deactivate'))).toBe(false);
    expect(within(confirm).getByText(/les ventes passées restent inchangées/)).toBeTruthy();
    fireEvent.click(within(confirm).getByRole('button', { name: 'Désactiver' }));
    await waitFor(() =>
      expect(writes('POST').map(([u]) => u)).toEqual([
        expect.stringContaining('/catalog/packagings/p24/deactivate'),
      ]),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Réactiver' }));
    await waitFor(() => expect(writes('POST')).toHaveLength(2));
    expect(writes('POST')[1]?.[0]).toContain('/catalog/packagings/p6/activate');
  });
});
