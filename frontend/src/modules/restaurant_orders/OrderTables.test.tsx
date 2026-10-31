// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { Toast } from 'primereact/toast';
import type { ReactNode, RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SITES, jsonResponse, pageOf, renderWithCapabilities } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import OrderCreatePage from './OrderCreatePage';
import OrderPage from './OrderPage';
import OrdersPage from './OrdersPage';

/** Palier R5-C3 (ADR-0049) : la table dans les commandes — saisie, fiche, historique, listes. */

const VIEW = 'restaurant.orders.order.view';
const CREATE = 'restaurant.orders.order.create';
const MENU_VIEW = 'restaurant.menu.view';
const TABLE_VIEW = 'restaurant.tables.table.view';
const ONE_SITE = [SITES[0]!];

const T1 = { id: 't1', label: 'T1', zone_name: 'Salle', is_active: true };

const order = (over: Record<string, unknown> = {}) => ({
  id: 'o1',
  site_id: 's1',
  site_name: 'Boutique',
  business_date: '2026-10-31',
  daily_number: 12,
  channel: 'STAFF',
  service_mode: 'ON_SITE',
  customer_id: null,
  customer_name: null,
  call_name: null,
  table_id: 't1',
  table: T1,
  status: 'OPEN',
  prep_status: 'RECEIVED',
  settlement_status: 'UNSETTLED',
  payment_timing: 'AT_END',
  assigned_user_id: null,
  assigned_name: null,
  assigned_at: null,
  created_by: 'u-me',
  created_by_name: 'Moi',
  total: '2500.00',
  sale_id: null,
  sale_number: null,
  payment_status: null,
  amount_due: null,
  line_counts: { received: 1, in_preparation: 0, ready: 0, served: 0, cancelled: 0 },
  created_at: '2026-10-31T12:00:00Z',
  last_served_at: null,
  updated_at: '2026-10-31T12:00:00Z',
  closed_at: null,
  cancelled_at: null,
  cancel_reason: null,
  version: 1,
  lines: [],
  ...over,
});

const table = (over: Record<string, unknown> = {}) => ({
  id: 't1',
  site_id: 's1',
  site_name: 'Boutique',
  zone_id: 'z1',
  zone_name: 'Salle',
  zone_active: true,
  label: 'T1',
  seats: 4,
  sort_order: 1,
  is_active: true,
  created_at: '2026-10-20T08:00:00Z',
  updated_at: '2026-10-20T08:00:00Z',
  ...over,
});
const TABLES = [table(), table({ id: 't2', label: 'T2', zone_id: null, zone_name: null })];

/** Plan de salle : T1 a déjà deux commandes en cours (avertissement A1, non bloquant). */
const board = {
  site_id: 's1',
  site_name: 'Boutique',
  orders_visible: true,
  counts: { free: 1, occupied: 1, to_settle: 0 },
  tables: [
    {
      id: 't1',
      label: 'T1',
      seats: 4,
      zone_id: 'z1',
      zone_name: 'Salle',
      is_active: true,
      state: 'OCCUPIED',
      order_count: 2,
      occupied_since: '2026-10-31T11:00:00Z',
      orders: null,
    },
    {
      id: 't2',
      label: 'T2',
      seats: null,
      zone_id: null,
      zone_name: null,
      is_active: true,
      state: 'FREE',
      order_count: 0,
      occupied_since: null,
      orders: [],
    },
  ],
};

const menuItem = {
  id: 'i1',
  site_id: 's1',
  site_name: 'Boutique',
  section_id: 'sec1',
  section_name: 'Grillades',
  section_active: true,
  article_id: 'a1',
  reference: 'POULET',
  designation: 'Poulet braisé',
  unit: 'portion',
  packaging_id: null,
  packaging_name: null,
  conversion: null,
  display_name: null,
  description: null,
  sort_order: 0,
  is_active: true,
  available: true,
  unavailable_reason: null,
  price: '2500.00',
  orderable: true,
  blockers: [],
  created_at: '2026-10-09T08:00:00Z',
  updated_at: '2026-10-09T08:00:00Z',
};

const event = (over: Record<string, unknown>) => ({
  id: 'e',
  event_type: 'CREATED',
  actor_kind: 'STAFF',
  actor_user_id: 'u-me',
  actor_name: 'Moi',
  reason: null,
  line_ids: [],
  data: {},
  occurred_at: '2026-10-31T12:00:00Z',
  ...over,
});

const fetchMock = vi.fn<typeof fetch>();
const show = vi.fn();
const calls = (method = 'GET') =>
  fetchMock.mock.calls
    .filter(([, init]) => (init?.method ?? 'GET') === method)
    .map(([u, init]) => [String(u), init?.body ? JSON.parse(String(init.body)) : null] as const);
const withToast = (node: ReactNode) => {
  const toast = { current: { show } as unknown as Toast } as RefObject<Toast | null>;
  return <ToastContext.Provider value={toast}>{node}</ToastContext.Provider>;
};
/** Choix d'une option d'un menu déroulant PrimeReact (options chargées par le serveur). */
const pick = async (container: HTMLElement, label: RegExp, option: string) => {
  const input = within(container).getByLabelText(label, { selector: 'input, select, span, div' });
  fireEvent.click(input.closest('.p-dropdown') ?? input);
  const options = await screen.findAllByRole('option', { name: option, hidden: true });
  fireEvent.click(options.at(-1)!);
};

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  cleanup();
  fetchMock.mockReset();
  show.mockReset();
  vi.unstubAllGlobals();
});

/** Réponses du serveur : lectures communes, écritures paramétrables. */
function serve(
  current: ReturnType<typeof order> = order(),
  {
    write = () => jsonResponse(current),
    events = [event({})],
  }: { write?: (url: string) => Response; events?: unknown[] } = {},
) {
  fetchMock.mockImplementation(async (url, init) => {
    const u = String(url);
    if ((init?.method ?? 'GET') !== 'GET') return write(u);
    if (u.includes('/restaurant/tables/board')) return jsonResponse(board);
    if (u.includes('/restaurant/tables?')) return pageOf(TABLES);
    if (u.includes('/restaurant/menu/items')) return pageOf([menuItem]);
    if (u.includes('/events')) return jsonResponse(events);
    if (u.includes('/restaurant/orders?')) return pageOf([current]);
    return jsonResponse(current);
  });
}

describe('la table dans les commandes (palier R5-C3)', () => {
  const renderCreate = (permissions = [VIEW, CREATE, MENU_VIEW, TABLE_VIEW]) =>
    renderWithCapabilities(withToast(<OrderCreatePage />), {
      permissions,
      sites: ONE_SITE,
      path: '/restaurant/orders/new',
      route: '/restaurant/orders/new',
      extraRoutes: [{ path: '/restaurant/orders/:id', element: <p>fiche de la commande</p> }],
    });
  const renderDetail = (permissions: string[]) =>
    renderWithCapabilities(withToast(<OrderPage />), {
      permissions,
      sites: ONE_SITE,
      path: '/restaurant/orders/:id',
      route: '/restaurant/orders/o1',
    });
  const submitOrder = async () => {
    fireEvent.click(await screen.findByRole('button', { name: 'Ajouter Poulet braisé' }));
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer la commande' }));
    expect(await screen.findByText('fiche de la commande')).toBeTruthy();
    return calls('POST')[0]![1] as Record<string, unknown>;
  };

  it('création sur place avec une table active du site ; avertissement si déjà occupée', async () => {
    serve();
    renderCreate();
    const form = document.body;
    await waitFor(() =>
      expect(
        calls().some(([u]) => u.includes('/restaurant/tables?') && u.includes('site_id=s1')),
      ).toBe(true),
    );
    expect(calls().find(([u]) => u.includes('/restaurant/tables?'))?.[0]).toMatch(/status=active/);
    await pick(form, /^Table/, 'T1 · Salle');
    // A1 : plusieurs commandes peuvent partager une table — avertissement, jamais un blocage.
    expect(
      await screen.findByText(
        "La table T1 a déjà 2 commandes en cours : la nouvelle commande s'y ajoutera.",
      ),
    ).toBeTruthy();
    const body = await submitOrder();
    expect(body).toMatchObject({ service_mode: 'ON_SITE', table_id: 't1' });
  });

  it('autre mode de service : table effacée et jamais envoyée', async () => {
    serve();
    renderCreate();
    await screen.findByRole('button', { name: 'Ajouter Poulet braisé' });
    await pick(document.body, /^Table/, 'T2');
    fireEvent.click(screen.getByText('À emporter'));
    expect(screen.queryByLabelText(/^Table/, { selector: 'input' })).toBeNull();
    // Retour « sur place » : la table choisie avant n'est pas restaurée.
    fireEvent.click(screen.getByText('Sur place'));
    expect((screen.getByLabelText(/^Table/, { selector: 'input' }) as HTMLInputElement).value).toBe(
      '',
    );
    fireEvent.click(screen.getByText('Comptoir'));
    const body = await submitOrder();
    expect(body).toMatchObject({ service_mode: 'COUNTER', table_id: null });
  });

  it('sans la consultation des tables : aucune table proposée, commande sans table', async () => {
    serve();
    renderCreate([VIEW, CREATE, MENU_VIEW]);
    await screen.findByRole('button', { name: 'Ajouter Poulet braisé' });
    expect(screen.queryByLabelText(/^Table/, { selector: 'input' })).toBeNull();
    expect(calls().some(([u]) => u.includes('/restaurant/tables'))).toBe(false);
    const body = await submitOrder();
    expect(body).toMatchObject({ service_mode: 'ON_SITE', table_id: null });
  });

  it('fiche : table actuelle et zone, changement motivé, retrait', async () => {
    serve(order({ table: { ...T1, is_active: false } }));
    renderDetail([VIEW, CREATE, TABLE_VIEW]);
    const cell = await screen.findByTestId('order-table');
    expect(cell.textContent).toContain('T1 · Salle');
    expect(within(cell).getByText('Table désactivée')).toBeTruthy();
    fireEvent.click(within(cell).getByRole('button', { name: 'Changer de table' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Table actuelle : T1 · Salle')).toBeTruthy();
    // La table actuelle n'est pas proposée ; rien n'est envoyé sans nouvelle table.
    expect(within(dialog).getByRole('button', { name: 'Enregistrer' })).toHaveProperty(
      'disabled',
      true,
    );
    await pick(dialog, /Nouvelle table/, 'T2');
    expect(screen.queryAllByRole('option', { name: 'T1 · Salle', hidden: true })).toHaveLength(0);
    fireEvent.change(within(dialog).getByLabelText(/Motif/), {
      target: { value: 'Client déplacé' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() =>
      expect(calls('PUT')).toEqual([
        ['/api/v1/restaurant/orders/o1/table', { table_id: 't2', reason: 'Client déplacé' }],
      ]),
    );
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    fireEvent.click(
      within(screen.getByTestId('order-table')).getByRole('button', {
        name: 'Changer de table',
      }),
    );
    fireEvent.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Retirer la table' }),
    );
    await waitFor(() =>
      expect(calls('PUT').at(-1)).toEqual([
        '/api/v1/restaurant/orders/o1/table',
        { table_id: null, reason: null },
      ]),
    );
  });

  it('sans la consultation des tables (module non effectif) : le retrait reste possible (B4)', async () => {
    serve();
    renderDetail([VIEW, CREATE]);
    fireEvent.click(
      within(await screen.findByTestId('order-table')).getByRole('button', {
        name: 'Changer de table',
      }),
    );
    const dialog = await screen.findByRole('dialog');
    expect(
      within(dialog).getByText(
        'Les tables ne sont pas disponibles ici : seul le retrait de la table actuelle est possible.',
      ),
    ).toBeTruthy();
    expect(within(dialog).queryByRole('button', { name: 'Enregistrer' })).toBeNull();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Retirer la table' }));
    await waitFor(() =>
      expect(calls('PUT')).toEqual([
        ['/api/v1/restaurant/orders/o1/table', { table_id: null, reason: null }],
      ]),
    );
  });

  it('commandes finales, autres modes et absence de order.create : aucune action', async () => {
    for (const current of [
      order({ status: 'CLOSED', settlement_status: 'SETTLED' }),
      order({ status: 'CANCELLED', cancel_reason: 'Parti' }),
    ]) {
      serve(current);
      renderDetail([VIEW, CREATE, TABLE_VIEW]);
      const cell = await screen.findByTestId('order-table');
      // Libellé COURANT affiché, même sur une commande close (B6).
      expect(cell.textContent).toContain('T1 · Salle');
      expect(within(cell).queryByRole('button')).toBeNull();
      cleanup();
    }
    serve(order({ service_mode: 'COUNTER', table_id: null, table: null }));
    renderDetail([VIEW, CREATE, TABLE_VIEW]);
    await screen.findByText('Comptoir');
    expect(screen.queryByTestId('order-table')).toBeNull();
    cleanup();
    serve();
    renderDetail([VIEW, TABLE_VIEW]);
    const cell = await screen.findByTestId('order-table');
    expect(within(cell).queryByRole('button')).toBeNull();
    cleanup();
    // Sur place sans table : association proposée.
    serve(order({ table_id: null, table: null }));
    renderDetail([VIEW, CREATE, TABLE_VIEW]);
    const empty = await screen.findByTestId('order-table');
    expect(empty.textContent).toContain('sans table');
    expect(within(empty).getByRole('button', { name: 'Associer une table' })).toBeTruthy();
  });

  it('refus métier du serveur traduit dans le dialogue, qui reste ouvert', async () => {
    serve(order(), {
      write: () => jsonResponse({ status: 422, title: 'Refus', code: 'table_inactive' }, 422),
    });
    renderDetail([VIEW, CREATE, TABLE_VIEW]);
    fireEvent.click(
      within(await screen.findByTestId('order-table')).getByRole('button', {
        name: 'Changer de table',
      }),
    );
    const dialog = await screen.findByRole('dialog');
    await pick(dialog, /Nouvelle table/, 'T2');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer' }));
    expect(
      await within(dialog).findByText('Cette table est désactivée : choisissez une table active.'),
    ).toBeTruthy();
    expect(screen.getByRole('dialog')).toBeTruthy();
  });

  it('historique : table initiale, changements et retrait avec les libellés d’alors', async () => {
    serve(order({ table: { ...T1, label: 'T1-bis' } }), {
      events: [
        event({
          id: 'e1',
          data: { channel: 'STAFF', table_id: 't1', table_label: 'T1', zone_name: 'Salle' },
        }),
        event({
          id: 'e2',
          event_type: 'TABLE_CHANGED',
          reason: 'Client déplacé',
          data: {
            table_id: 't2',
            table_label: 'T2',
            zone_name: null,
            previous_table_id: 't1',
            previous_table_label: 'T1',
            previous_zone_name: 'Salle',
          },
        }),
        event({
          id: 'e3',
          event_type: 'TABLE_CHANGED',
          data: {
            table_id: null,
            table_label: null,
            zone_name: null,
            previous_table_id: 't2',
            previous_table_label: 'T2',
            previous_zone_name: null,
          },
        }),
      ],
    });
    renderDetail([VIEW]);
    const items = await screen.findAllByTestId('order-event');
    expect(items[0]!.textContent).toContain('Commande créée — table T1 (Salle)');
    expect(items[1]!.textContent).toContain('Table modifiée — T1 (Salle) → T2');
    expect(items[1]!.textContent).toContain('Client déplacé');
    expect(items[2]!.textContent).toContain('Table modifiée — T2 → sans table');
    // Libellé courant de la commande (renommé) ≠ libellés historiques.
    expect(screen.getByTestId('order-table').textContent).toContain('T1-bis · Salle');
  });

  it('listes : colonne Table et filtre par table (URL du plan de salle comprise)', async () => {
    serve();
    renderWithCapabilities(<OrdersPage />, {
      permissions: [VIEW, CREATE, TABLE_VIEW],
      sites: ONE_SITE,
      path: '/restaurant/orders',
      route: '/restaurant/orders?table_id=t1',
    });
    // Lien du plan de salle : les colonnes du suivi sont filtrées sur la table.
    await waitFor(() => {
      const orderCalls = calls().filter(([u]) => u.includes('/restaurant/orders?'));
      expect(orderCalls.length).toBeGreaterThanOrEqual(3);
      expect(orderCalls.every(([u]) => u.includes('table_id=t1'))).toBe(true);
    });
    expect((await screen.findAllByText('table T1 · Salle')).length).toBeGreaterThan(0);
    // Onglet « À régler » : colonne Table, filtre conservé puis effacé.
    fireEvent.click(screen.getByRole('tab', { name: 'À régler' }));
    const row = (await screen.findByText('n° 12')).closest('tr') as HTMLElement;
    expect(within(row).getByText('T1 · Salle')).toBeTruthy();
    expect(calls().at(-1)?.[0]).toMatch(/table_id=t1/);
    fireEvent.click(screen.getByRole('button', { name: /Réinitialiser/ }));
    await waitFor(() => expect(calls().at(-1)?.[0]).not.toMatch(/table_id/));
    // Choix d'une table dans le filtre.
    await pick(screen.getByRole('search'), /^Table/, 'T2');
    await waitFor(() => expect(calls().at(-1)?.[0]).toMatch(/table_id=t2/));
  });
});
