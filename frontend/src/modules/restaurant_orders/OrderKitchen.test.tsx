// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import i18next from 'i18next';
import type { Toast } from 'primereact/toast';
import type { ReactNode, RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { memberNotificationText } from '@/modules/organization/notifications/MemberNotificationsPanel';
import type { MemberNotification } from '@/modules/organization/notifications/api';
import { jsonResponse, pageOf, renderWithCapabilities, SITES } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import OrderPage from './OrderPage';

/** Palier R6 (ADR-0050) : la cuisine sur la fiche d'une commande — poste, priorité, historique. */

const VIEW = 'restaurant.orders.order.view';
const STATION_VIEW = 'restaurant.kitchen.station.view';
const LINE_ROUTE = 'restaurant.kitchen.line.route';
const PRIORITY_SET = 'restaurant.kitchen.priority.set';
const ONE_SITE = [SITES[0]!];
const text = (el: Element | null) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim();

const line = (over: Record<string, unknown> = {}) => ({
  id: 'l1',
  line_no: 1,
  menu_item_id: 'i1',
  article_id: 'a1',
  packaging_id: null,
  label: 'Poulet braisé',
  packaging_name: null,
  unit: 'portion',
  conversion: null,
  unit_price: '2500.00',
  quantity: '1.000',
  base_quantity: '1.000',
  line_total: '2500.00',
  note: null,
  status: 'RECEIVED',
  station_id: 'st1',
  station_name: 'Grillades',
  created_at: '2026-11-01T12:00:00Z',
  prepared_at: null,
  ready_at: null,
  served_at: null,
  cancelled_at: null,
  cancel_reason: null,
  ...over,
});

const order = (over: Record<string, unknown> = {}) => ({
  id: 'o1',
  site_id: 's1',
  site_name: 'Boutique',
  business_date: '2026-11-01',
  daily_number: 12,
  channel: 'STAFF',
  service_mode: 'ON_SITE',
  customer_id: null,
  customer_name: null,
  call_name: null,
  table_id: null,
  table: null,
  status: 'OPEN',
  prep_status: 'RECEIVED',
  settlement_status: 'UNSETTLED',
  payment_timing: 'AT_END',
  priority: 'URGENT',
  assigned_user_id: null,
  assigned_name: null,
  assigned_at: null,
  created_by: 'u-me',
  created_by_name: 'Moi',
  total: '3000.00',
  sale_id: null,
  sale_number: null,
  payment_status: null,
  amount_due: null,
  line_counts: { received: 2, in_preparation: 0, ready: 0, served: 0, cancelled: 0 },
  created_at: '2026-11-01T12:00:00Z',
  last_served_at: null,
  updated_at: '2026-11-01T12:00:00Z',
  closed_at: null,
  cancelled_at: null,
  cancel_reason: null,
  version: 1,
  lines: [
    line(),
    line({
      id: 'l2',
      line_no: 2,
      label: 'Bissap',
      unit_price: '500.00',
      line_total: '500.00',
      station_id: null,
      station_name: null,
    }),
  ],
  ...over,
});

const event = (over: Record<string, unknown>) => ({
  id: 'e',
  event_type: 'CREATED',
  actor_kind: 'STAFF',
  actor_user_id: 'u-me',
  actor_name: 'Moi',
  reason: null,
  line_ids: [],
  data: {},
  occurred_at: '2026-11-01T12:00:00Z',
  ...over,
});

const EVENTS = [
  event({ id: 'e1', line_ids: ['l1', 'l2'] }),
  event({
    id: 'e2',
    event_type: 'PRIORITY_CHANGED',
    reason: 'Client pressé',
    data: { previous: 'NORMAL', priority: 'URGENT' },
  }),
  event({
    id: 'e3',
    event_type: 'LINES_ROUTED',
    line_ids: ['l1'],
    data: { station_id: 'st1', station_name: 'Grillades', previous: { '1': null } },
  }),
];

const station = (id: string, name: string) => ({
  id,
  site_id: 's1',
  site_name: 'Boutique',
  name,
  sort_order: 0,
  is_active: true,
  route_count: 0,
  created_at: '2026-11-01T08:00:00Z',
  updated_at: '2026-11-01T08:00:00Z',
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

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockImplementation(async (url, init) => {
    const u = String(url);
    if ((init?.method ?? 'GET') !== 'GET') return jsonResponse(order());
    if (u.includes('/events')) return jsonResponse(EVENTS);
    if (u.includes('/kitchen/stations')) {
      return pageOf([station('st1', 'Grillades'), station('st2', 'Boissons')]);
    }
    return jsonResponse(order());
  });
});

afterEach(() => {
  cleanup();
  fetchMock.mockReset();
  show.mockReset();
  vi.unstubAllGlobals();
});

const renderDetail = (permissions: string[], absentModules: string[] = []) =>
  renderWithCapabilities(withToast(<OrderPage />), {
    permissions,
    path: '/restaurant/orders/:id',
    route: '/restaurant/orders/o1',
    sites: ONE_SITE,
    absentModules,
  });

const pick = async (container: HTMLElement, label: RegExp, option: string) => {
  const input = within(container).getByLabelText(label, { selector: 'input, select, span, div' });
  fireEvent.click(input.closest('.p-dropdown') ?? input);
  const options = await screen.findAllByRole('option', { name: option, hidden: true });
  fireEvent.click(options.at(-1)!);
};

describe('cuisine sur la fiche d’une commande (R6)', () => {
  it('poste de chaque ligne, priorité et historique (valeurs enregistrées)', async () => {
    renderDetail([VIEW]);
    const priority = await screen.findByTestId('order-priority');
    expect(text(priority)).toBe('Urgente');
    expect(screen.getByRole('columnheader', { name: 'Poste' })).toBeTruthy();
    expect(text(screen.getByText('Poulet braisé').closest('tr'))).toContain('Grillades');
    expect(text(screen.getByText('Bissap').closest('tr'))).toContain('Sans poste');
    const history = await screen.findAllByTestId('order-event');
    expect(text(history[1]!)).toContain('Priorité modifiée — Normale → Urgente');
    expect(text(history[1]!)).toContain('Client pressé');
    expect(text(history[2]!)).toContain('Poste de préparation modifié — lignes 1 — vers Grillades');
    // Sans les permissions de la cuisine : aucune action de correction.
    expect(screen.queryByRole('button', { name: 'Changer la priorité' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Changer de poste' })).toBeNull();
  });

  it('module Cuisine non effectif : ni priorité ni colonne « Poste » sans poste enregistré', async () => {
    fetchMock.mockImplementation(async (url) =>
      String(url).includes('/events')
        ? jsonResponse([])
        : jsonResponse(
            order({
              lines: [line({ station_id: null, station_name: null })],
            }),
          ),
    );
    renderDetail([VIEW, LINE_ROUTE, STATION_VIEW, PRIORITY_SET], ['restaurant.kitchen']);
    await screen.findByText('Poulet braisé');
    expect(screen.queryByTestId('order-priority')).toBeNull();
    expect(screen.queryByRole('columnheader', { name: 'Poste' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Changer de poste' })).toBeNull();
  });

  it('changer le poste d’une ligne : postes actifs du site, motif facultatif', async () => {
    renderDetail([VIEW, STATION_VIEW, LINE_ROUTE]);
    const row = (await screen.findByText('Bissap')).closest('tr') as HTMLElement;
    fireEvent.click(within(row).getByRole('button', { name: 'Changer de poste' }));
    const dialog = await screen.findByRole('dialog');
    expect(text(dialog)).toContain('Poste de « Bissap »');
    await waitFor(() =>
      expect(calls().map(([u]) => u)).toContain(
        '/api/v1/restaurant/kitchen/stations?site_id=s1&status=active&sort=position&limit=100',
      ),
    );
    await pick(dialog, /Nouveau poste/, 'Boissons');
    fireEvent.change(within(dialog).getByLabelText(/Motif/), { target: { value: ' Bar ' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Changer de poste' }));
    await waitFor(() =>
      expect(calls('PUT')).toEqual([
        [
          '/api/v1/restaurant/kitchen/orders/o1/route',
          { line_ids: ['l2'], station_id: 'st2', reason: 'Bar' },
        ],
      ]),
    );
    await waitFor(() =>
      expect(show).toHaveBeenCalledWith(
        expect.objectContaining({ severity: 'success', summary: 'Poste de préparation modifié.' }),
      ),
    );
  });

  it('remettre une ligne « Sans poste »', async () => {
    renderDetail([VIEW, STATION_VIEW, LINE_ROUTE]);
    const row = (await screen.findByText('Poulet braisé')).closest('tr') as HTMLElement;
    fireEvent.click(within(row).getByRole('button', { name: 'Changer de poste' }));
    const dialog = await screen.findByRole('dialog');
    // Poste actuel présélectionné une fois les postes du site chargés.
    await waitFor(() => expect(text(dialog)).toContain('Grillades'));
    await pick(dialog, /Nouveau poste/, 'Sans poste');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Changer de poste' }));
    await waitFor(() =>
      expect(calls('PUT')).toEqual([
        [
          '/api/v1/restaurant/kitchen/orders/o1/route',
          { line_ids: ['l1'], station_id: null, reason: null },
        ],
      ]),
    );
  });

  it('changer la priorité avec la permission ; erreur du serveur traduite', async () => {
    renderDetail([VIEW, PRIORITY_SET]);
    fireEvent.click(await screen.findByRole('button', { name: 'Changer la priorité' }));
    const dialog = await screen.findByRole('dialog');
    await pick(dialog, /Priorité/, 'Élevée');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() =>
      expect(calls('PUT')).toEqual([
        ['/api/v1/restaurant/kitchen/orders/o1/priority', { priority: 'HIGH', reason: null }],
      ]),
    );
    fetchMock.mockImplementation(async (url, init) =>
      (init?.method ?? 'GET') !== 'GET'
        ? jsonResponse({ code: 'order_closed', status: 409, title: 'x' }, 409)
        : jsonResponse(String(url).includes('/events') ? [] : order()),
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Changer la priorité' }));
    const again = await screen.findByRole('dialog');
    fireEvent.click(within(again).getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() =>
      expect(show).toHaveBeenCalledWith(
        expect.objectContaining({ severity: 'error', summary: i18next.t('errors:order_closed') }),
      ),
    );
  });

  it('commande close : priorité affichée sans action', async () => {
    fetchMock.mockImplementation(async (url) =>
      jsonResponse(String(url).includes('/events') ? [] : order({ status: 'CLOSED' })),
    );
    renderDetail([VIEW, PRIORITY_SET, STATION_VIEW, LINE_ROUTE]);
    expect(text(await screen.findByTestId('order-priority'))).toBe('Urgente');
    expect(screen.queryByRole('button', { name: 'Changer la priorité' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Changer de poste' })).toBeNull();
  });
});

describe('notification « prête » par poste (R6)', () => {
  const notification = (payload: Record<string, unknown>) =>
    ({
      id: 'n1',
      kind: 'restaurant.order.ready',
      entity_type: 'restaurant.order',
      entity_id: 'o1',
      site: { id: 's1', name: 'Boutique' },
      payload,
      read_at: null,
      handled: false,
      created_at: '2026-11-01T12:00:00Z',
    }) as unknown as MemberNotification;
  const t = i18next.t.bind(i18next) as (key: string, options?: Record<string, unknown>) => string;

  it('nomme le poste pour une partie de la commande, sinon texte d’origine', () => {
    expect(
      memberNotificationText(
        t,
        notification({ daily_number: 12, line_count: 2, part: true, station_name: 'Grillades' }),
      ),
    ).toBe('Commande n° 12, poste Grillades prêt : 2 articles à servir (Boutique)');
    expect(memberNotificationText(t, notification({ daily_number: 12, line_count: 1 }))).toBe(
      'Commande n° 12 prête : 1 article à servir (Boutique)',
    );
  });
});
