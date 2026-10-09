// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { Toast } from 'primereact/toast';
import type { RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { jsonResponse, pageOf, renderWithCapabilities } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import { MENU_REFRESH_MS } from './api';
import MenuPage from './MenuPage';

/** Palier R1 (ADR-0049) : menu des sites — affichage, permissions, « épuisé », erreurs. */

const item = (over: Record<string, unknown> = {}) => ({
  id: 'i1',
  site_id: 's1',
  site_name: 'Boutique',
  section_id: 'sec1',
  section_name: 'Boissons',
  section_active: true,
  article_id: 'a1',
  reference: 'COCA',
  designation: 'Coca-Cola 33 cl',
  unit: 'bouteille',
  packaging_id: null,
  packaging_name: null,
  conversion: null,
  display_name: null,
  description: null,
  sort_order: 0,
  is_active: true,
  available: true,
  unavailable_reason: null,
  price: '500.00',
  orderable: true,
  blockers: [],
  created_at: '2026-10-09T08:00:00Z',
  updated_at: '2026-10-09T08:00:00Z',
  ...over,
});

const section = (over: Record<string, unknown> = {}) => ({
  id: 'sec1',
  site_id: 's1',
  site_name: 'Boutique',
  name: 'Boissons',
  sort_order: 0,
  is_active: true,
  item_count: 2,
  created_at: '2026-10-09T08:00:00Z',
  updated_at: '2026-10-09T08:00:00Z',
  ...over,
});

const VIEW = 'restaurant.menu.view';
const MANAGE = [VIEW, 'restaurant.menu.manage', 'restaurant.menu.availability'];

describe('menu des sites (palier R1)', () => {
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
    vi.useRealTimers();
  });

  const items = () => [
    item(),
    item({
      id: 'i2',
      packaging_id: 'p1',
      packaging_name: 'Casier 24',
      conversion: '24.000',
      price: '11000.00',
      available: false,
      unavailable_reason: 'Rupture fournisseur',
      orderable: false,
      blockers: ['unavailable'],
    }),
    item({
      id: 'i3',
      article_id: 'a2',
      reference: 'PLAT',
      designation: 'Poulet braisé',
      display_name: 'Poulet du chef',
      price: null,
      orderable: false,
      blockers: ['article_not_in_site_assortment', 'packaging_price_not_set'],
    }),
  ];

  const serve = () =>
    fetchMock.mockImplementation(async (url, init) => {
      const u = String(url);
      if ((init?.method ?? 'GET') !== 'GET') return jsonResponse(item({ available: false }));
      if (u.includes('/restaurant/menu/sections')) return pageOf([section()]);
      return pageOf(items());
    });

  it('liste : présentation, prix du catalogue, épuisé et motifs « non commandable »', async () => {
    serve();
    renderWithCapabilities(<MenuPage />, { permissions: MANAGE });
    const coca = (await screen.findByText('Coca-Cola 33 cl')).closest('tr') as HTMLElement;
    expect(within(coca).getByText('Commandable')).toBeTruthy();
    expect(within(coca).getByText('Disponible')).toBeTruthy();
    const crate = screen.getByText('Coca-Cola 33 cl — Casier 24').closest('tr') as HTMLElement;
    expect(within(crate).getByText('Épuisé')).toBeTruthy();
    expect(within(crate).getByText('Rupture fournisseur')).toBeTruthy();
    expect(within(crate).getByText(/11\s?000/)).toBeTruthy();
    const plat = screen.getByText('Poulet du chef').closest('tr') as HTMLElement;
    expect(within(plat).getByText('Non commandable')).toBeTruthy();
    expect(
      within(plat).getByText('hors assortiment du site · prix du conditionnement non configuré'),
    ).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Ajouter au menu' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Nouvelle section' })).toBeTruthy();
  });

  it('vendeur : « épuisé » avec motif, sans configuration du menu', async () => {
    serve();
    renderWithCapabilities(<MenuPage />, {
      permissions: [VIEW, 'restaurant.menu.availability'],
    });
    const coca = (await screen.findByText('Coca-Cola 33 cl')).closest('tr') as HTMLElement;
    expect(screen.queryByRole('button', { name: 'Ajouter au menu' })).toBeNull();
    expect(within(coca).queryByRole('button', { name: 'Modifier' })).toBeNull();
    fireEvent.click(within(coca).getByRole('button', { name: 'Marquer épuisé' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/Motif/), {
      target: { value: 'Plus de glace' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Marquer épuisé' }));
    await waitFor(() =>
      expect(calls('PUT')).toEqual([
        [
          '/api/v1/restaurant/menu/items/i1/availability',
          { available: false, reason: 'Plus de glace' },
        ],
      ]),
    );
    // Remettre disponible : direct, sans motif.
    const crate = screen.getByText('Coca-Cola 33 cl — Casier 24').closest('tr') as HTMLElement;
    fireEvent.click(within(crate).getByRole('button', { name: 'Remettre disponible' }));
    await waitFor(() =>
      expect(calls('PUT').at(-1)).toEqual([
        '/api/v1/restaurant/menu/items/i2/availability',
        { available: true, reason: null },
      ]),
    );
  });

  it('consultant : lecture seule (aucune action)', async () => {
    serve();
    renderWithCapabilities(<MenuPage />, { permissions: [VIEW] });
    await screen.findByText('Coca-Cola 33 cl');
    expect(screen.queryByRole('columnheader', { name: 'Actions' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Nouvelle section' })).toBeNull();
  });

  it('« épuisé » actualisé en 15 s au plus (critère R1)', async () => {
    expect(MENU_REFRESH_MS).toBeLessThanOrEqual(15_000);
    vi.useFakeTimers({ shouldAdvanceTime: true });
    serve();
    renderWithCapabilities(<MenuPage />, { permissions: [VIEW] });
    await screen.findByText('Coca-Cola 33 cl');
    const itemCalls = () =>
      calls().filter(([u]) => String(u).includes('/restaurant/menu/items')).length;
    const before = itemCalls();
    await vi.advanceTimersByTimeAsync(MENU_REFRESH_MS + 100);
    await waitFor(() => expect(itemCalls()).toBeGreaterThan(before));
  });

  it('section : site obligatoire sans site sélectionné, refus traduit du serveur', async () => {
    fetchMock.mockImplementation(async (url, init) => {
      if ((init?.method ?? 'GET') === 'POST') {
        return jsonResponse(
          { status: 409, title: 'Conflit', code: 'menu_section_name_taken' },
          409,
        );
      }
      return String(url).includes('/sections') ? pageOf([section()]) : pageOf([]);
    });
    const show = vi.fn();
    const toast = { current: { show } as unknown as Toast } as RefObject<Toast | null>;
    renderWithCapabilities(
      <ToastContext.Provider value={toast}>
        <MenuPage />
      </ToastContext.Provider>,
      { permissions: MANAGE },
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Nouvelle section' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/Nom de la section/), {
      target: { value: 'Boissons' },
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
        ['/api/v1/restaurant/menu/sections', { site_id: 's2', name: 'Boissons', sort_order: 0 }],
      ]),
    );
    await waitFor(() =>
      expect(show).toHaveBeenCalledWith(
        expect.objectContaining({
          severity: 'error',
          summary: 'Une section de ce nom existe déjà sur ce site.',
        }),
      ),
    );
  });
});
