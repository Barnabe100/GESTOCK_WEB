// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SITES, jsonResponse, renderWithCapabilities } from '@/shared/testing';

import { BOARD_REFRESH_MS } from './api';
import TablesPage from './TablesPage';

/** Palier R5-C2 (ADR-0049) : plan de salle — états calculés par le serveur, droits, erreurs. */

const VIEW = 'restaurant.tables.table.view';
const ORDER_VIEW = 'restaurant.orders.order.view';
const ONE_SITE = [SITES[0]!];

const order = (over: Record<string, unknown> = {}) => ({
  id: 'o1',
  business_date: '2026-10-31',
  daily_number: 12,
  call_name: 'Awa',
  status: 'OPEN',
  prep_status: 'RECEIVED',
  settlement_status: 'UNSETTLED',
  to_settle: false,
  created_at: '2026-10-31T11:05:00Z',
  ...over,
});

const boardTable = (over: Record<string, unknown> = {}) => ({
  id: 't1',
  label: 'T1',
  seats: 4,
  zone_id: 'z1',
  zone_name: 'Terrasse',
  is_active: true,
  state: 'FREE',
  order_count: 0,
  occupied_since: null,
  orders: [],
  ...over,
});

/** Plan tel que renvoyé par le serveur : zones dans son ordre, sans zone en dernier. */
const board = (over: Record<string, unknown> = {}) => ({
  site_id: 's1',
  site_name: 'Boutique',
  orders_visible: true,
  counts: { free: 1, occupied: 2, to_settle: 1 },
  tables: [
    boardTable(),
    boardTable({
      id: 't2',
      label: 'T2',
      seats: null,
      state: 'OCCUPIED',
      order_count: 2,
      occupied_since: '2026-10-31T10:30:00Z',
      // Toutes « à régler » côté commande, mais le SERVEUR dit « Occupée » : affiché tel quel.
      orders: [
        order({ id: 'o2', daily_number: 7, call_name: null, to_settle: true }),
        order({ id: 'o3', daily_number: 9, prep_status: 'READY' }),
      ],
    }),
    boardTable({
      id: 't3',
      label: 'S1',
      zone_id: 'z2',
      zone_name: 'Salle',
      state: 'TO_SETTLE',
      order_count: 1,
      occupied_since: '2026-10-31T11:05:00Z',
      orders: [order({ prep_status: 'SERVED', to_settle: true })],
    }),
    boardTable({
      id: 't4',
      label: 'Bar',
      zone_id: null,
      zone_name: null,
      is_active: false,
      state: 'OCCUPIED',
      order_count: 1,
      occupied_since: '2026-10-31T09:00:00Z',
      orders: [order({ id: 'o4', daily_number: 3, status: 'PENDING_CONFIRMATION' })],
    }),
  ],
  ...over,
});

describe('plan de salle (palier R5-C2)', () => {
  const fetchMock = vi.fn<typeof fetch>();
  const boardCalls = () =>
    fetchMock.mock.calls.map(([u]) => String(u)).filter((u) => u.includes('/tables/board'));

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  const serve = (body: unknown = board(), status = 200) =>
    fetchMock.mockImplementation(async () => jsonResponse(body, status));
  const card = (label: string) => screen.getByRole('listitem', { name: label });

  it('onglet par défaut : cartes par zone dans l’ordre du serveur, états, compteurs', async () => {
    serve();
    renderWithCapabilities(<TablesPage />, { permissions: [VIEW, ORDER_VIEW], sites: ONE_SITE });
    expect(
      (await screen.findAllByTestId('floor-table')).map((c) => c.getAttribute('aria-label')),
    ).toEqual(['T1', 'T2', 'S1', 'Bar']);
    expect(boardCalls()[0]).toBe('/api/v1/restaurant/tables/board?site_id=s1');
    expect(screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)).toEqual([
      'Terrasse',
      'Salle',
      'Sans zone',
    ]);
    const counts = screen.getByRole('status', { name: 'Résumé du plan de salle' });
    expect(within(counts).getByText('1 libre')).toBeTruthy();
    expect(within(counts).getByText('2 occupées')).toBeTruthy();
    expect(within(counts).getByText('1 à régler')).toBeTruthy();
    expect(within(card('T1')).getByText('Libre')).toBeTruthy();
    expect(within(card('T1')).getByText('4 places')).toBeTruthy();
    // État du SERVEUR, jamais recalculé (T2 : commandes « à régler » mais table « Occupée »).
    expect(within(card('T2')).getByText('Occupée')).toBeTruthy();
    expect(within(card('S1')).getByText('À régler')).toBeTruthy();
  });

  it('commandes multiples, ancienneté, table inactive occupée, liens vers les commandes', async () => {
    serve();
    renderWithCapabilities(<TablesPage />, { permissions: [VIEW, ORDER_VIEW], sites: ONE_SITE });
    await screen.findAllByTestId('floor-table');
    const t2 = card('T2');
    expect(within(t2).getByText(/2 commandes en cours · depuis le/)).toBeTruthy();
    expect(within(t2).getByRole('note').textContent).toBe(
      'Plusieurs commandes sont en cours sur cette table.',
    );
    expect(within(card('S1')).queryByRole('note')).toBeNull();
    const orders = within(t2).getByRole('list', { name: 'Commandes de la table T2' });
    expect(within(orders).getByRole('link', { name: 'n° 7' }).getAttribute('href')).toBe(
      '/restaurant/orders/o2',
    );
    expect(within(orders).getByText('Tout servi, à régler')).toBeTruthy();
    expect(within(orders).getByText('Prête')).toBeTruthy();
    expect(
      within(t2).getByRole('link', { name: 'Voir les commandes de la table' }).getAttribute('href'),
    ).toBe('/restaurant/orders?table_id=t2');
    // Aucune commande : pas de lien vers la liste filtrée.
    expect(within(card('T1')).queryByRole('link')).toBeNull();
    const bar = card('Bar');
    expect(within(bar).getByText('Table désactivée encore occupée')).toBeTruthy();
    expect(within(bar).getByText('À confirmer')).toBeTruthy();
  });

  it('détail des commandes masqué sans orders_visible ; liens absents sans order.view', async () => {
    serve(
      board({
        orders_visible: false,
        tables: board().tables.map((t) => ({ ...t, orders: null })),
      }),
    );
    renderWithCapabilities(<TablesPage />, { permissions: [VIEW], sites: ONE_SITE });
    await screen.findAllByTestId('floor-table');
    expect(
      screen.getByText(
        "Le détail des commandes n'est visible qu'avec le droit de consulter les commandes.",
      ),
    ).toBeTruthy();
    const t2 = card('T2');
    // Nombre et ancienneté restent visibles ; aucun détail individuel ni lien.
    expect(within(t2).getByText(/2 commandes en cours/)).toBeTruthy();
    expect(within(t2).queryByRole('list')).toBeNull();
    expect(within(t2).queryByRole('link')).toBeNull();
  });

  it('plusieurs sites sans sélection : site à choisir avant tout appel', async () => {
    serve(board({ site_id: 's2' }));
    renderWithCapabilities(<TablesPage />, { permissions: [VIEW] });
    expect(
      await screen.findByText('Choisissez un site pour afficher son plan de salle.'),
    ).toBeTruthy();
    expect(boardCalls()).toEqual([]);
    const site = screen.getByLabelText(/Site/, { selector: 'input, select, span, div' });
    fireEvent.click(site.closest('.p-dropdown') ?? site);
    fireEvent.click(screen.getAllByRole('option', { name: 'Dépôt', hidden: true }).at(-1)!);
    await waitFor(() =>
      expect(boardCalls()).toEqual(['/api/v1/restaurant/tables/board?site_id=s2']),
    );
    expect(await screen.findAllByTestId('floor-table')).toHaveLength(4);
  });

  it('erreur du serveur traduite ; plan vide', async () => {
    serve({ status: 403, title: 'Refus', code: 'site_access_denied' }, 403);
    renderWithCapabilities(<TablesPage />, { permissions: [VIEW], sites: ONE_SITE });
    expect(await screen.findByText('Accès à ce site refusé.')).toBeTruthy();
    cleanup();
    serve(board({ tables: [], counts: { free: 0, occupied: 0, to_settle: 0 } }));
    renderWithCapabilities(<TablesPage />, { permissions: [VIEW], sites: ONE_SITE });
    expect(await screen.findByText('Aucune table active sur ce site.')).toBeTruthy();
  });

  it('actualisé automatiquement toutes les 15 s', async () => {
    expect(BOARD_REFRESH_MS).toBe(15_000);
    vi.useFakeTimers({ shouldAdvanceTime: true });
    serve();
    renderWithCapabilities(<TablesPage />, { permissions: [VIEW], sites: ONE_SITE });
    await screen.findAllByTestId('floor-table');
    const before = boardCalls().length;
    await vi.advanceTimersByTimeAsync(BOARD_REFRESH_MS + 100);
    await waitFor(() => expect(boardCalls().length).toBeGreaterThan(before));
  });
});
