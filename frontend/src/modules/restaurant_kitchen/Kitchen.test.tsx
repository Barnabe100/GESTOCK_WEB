// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { Toast } from 'primereact/toast';
import type { ReactNode, RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SITES, jsonResponse, pageOf, renderWithCapabilities } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import { KITCHEN_REFRESH_MS } from './api';
import KitchenPage from './KitchenPage';
import StationsPage from './StationsPage';

/** Palier R6 (ADR-0050) : écran cuisine, postes et affectations du menu. */

const VIEW = 'restaurant.kitchen.station.view';
const MANAGE = 'restaurant.kitchen.station.manage';
const PRIORITY = 'restaurant.kitchen.priority.set';
const PREPARE = 'restaurant.orders.order.prepare';
const ONE_SITE = [SITES[0]!];
const text = (el: Element | null) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim();

const boardLine = (over: Record<string, unknown> = {}) => ({
  id: 'l1',
  line_no: 1,
  label: 'Poulet braisé',
  packaging_name: null,
  quantity: '2.000',
  unit: 'portion',
  note: 'Bien cuit',
  status: 'RECEIVED',
  received_at: '2026-11-01T11:00:00Z',
  prepared_at: null,
  ready_at: null,
  ...over,
});

const ticket = (over: Record<string, unknown> = {}) => ({
  order_id: 'o1',
  daily_number: 12,
  business_date: '2026-11-01',
  service_mode: 'ON_SITE',
  table_label: 'T4',
  call_name: null,
  priority: 'NORMAL',
  assigned_name: 'Awa',
  awaiting_payment: false,
  station_id: 'st1',
  station_name: 'Grillades',
  state: 'RECEIVED',
  received_at: '2026-11-01T11:00:00Z',
  order_created_at: '2026-11-01T11:00:00Z',
  lines: [boardLine()],
  ...over,
});

/** Écran tel que renvoyé par le serveur : SON ordre (priorité, puis ancienneté) fait foi. */
const board = (over: Record<string, unknown> = {}) => ({
  site_id: 's1',
  site_name: 'Boutique',
  stations: [
    { id: 'st1', name: 'Grillades', is_active: true },
    { id: 'st2', name: 'Boissons', is_active: true },
    { id: 'st3', name: 'Entrées', is_active: false },
  ],
  counts: { received: 2, in_preparation: 1, ready: 1 },
  tickets: [
    ticket({ order_id: 'o3', daily_number: 15, priority: 'URGENT', table_label: null }),
    ticket(),
    ticket({
      order_id: 'o2',
      daily_number: 9,
      station_id: 'st2',
      station_name: 'Boissons',
      state: 'IN_PREPARATION',
      service_mode: 'TAKEAWAY',
      table_label: null,
      call_name: 'Moussa',
      lines: [
        boardLine({ id: 'l5', label: 'Bissap', status: 'IN_PREPARATION', note: null }),
        boardLine({ id: 'l6', label: 'Coca-Cola', status: 'READY', note: null }),
      ],
    }),
    ticket({
      order_id: 'o4',
      daily_number: 3,
      station_id: null,
      station_name: null,
      state: 'READY',
      priority: 'HIGH',
      lines: [boardLine({ id: 'l9', label: 'Pain', status: 'READY', note: null })],
    }),
  ],
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

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  cleanup();
  fetchMock.mockReset();
  show.mockReset();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  try {
    localStorage.clear();
  } catch {
    // Stockage indisponible : rien à nettoyer.
  }
});

const serveBoard = (body: unknown = board(), status = 200) =>
  fetchMock.mockImplementation(async (_url, init) => {
    if ((init?.method ?? 'GET') !== 'GET') return jsonResponse({});
    return jsonResponse(body, status);
  });
/** Choix d'une option d'un menu déroulant PrimeReact. */
const pick = async (label: string | RegExp, option: string) => {
  const input = screen.getByLabelText(label, { selector: 'input, select, span, div' });
  fireEvent.click(input.closest('.p-dropdown') ?? input);
  const options = await screen.findAllByRole('option', { name: option, hidden: true });
  fireEvent.click(options.at(-1)!);
};
const dropdownText = (label: string) =>
  text(
    screen.getByLabelText(label, { selector: 'input, select, span, div' }).closest('.p-dropdown'),
  );
const column = (name: string) => screen.getByRole('region', { name });
const tickets = (name: string) =>
  within(column(name))
    .queryAllByTestId('kitchen-ticket')
    .map((el) => el.getAttribute('aria-label'));

describe('écran cuisine (R6)', () => {
  it('trois colonnes dans l’ordre du serveur, poste, table, serveur et compteurs', async () => {
    serveBoard();
    renderWithCapabilities(withToast(<KitchenPage />), {
      permissions: [VIEW, PREPARE],
      sites: ONE_SITE,
    });
    expect(await screen.findAllByTestId('kitchen-ticket')).toHaveLength(4);
    expect(calls()[0]![0]).toBe('/api/v1/restaurant/kitchen/board?site_id=s1');
    // L'ordre du SERVEUR est conservé : la commande urgente (plus récente) passe d'abord.
    expect(tickets('En attente')).toEqual([
      'Commande n° 15 — Grillades',
      'Commande n° 12 — Grillades',
    ]);
    expect(tickets('En préparation')).toEqual(['Commande n° 9 — Boissons']);
    expect(tickets('Prêtes')).toEqual(['Commande n° 3 — Sans poste']);
    const urgent = screen.getByRole('listitem', { name: 'Commande n° 15 — Grillades' });
    expect(text(urgent)).toContain('Urgente');
    expect(urgent.className).toContain('sm-kitchen-priority-urgent');
    const normal = screen.getByRole('listitem', { name: 'Commande n° 12 — Grillades' });
    expect(text(normal)).toContain('Table T4');
    expect(text(normal)).toContain('Awa');
    expect(text(normal)).toContain('Reçue à 11:00');
    expect(text(normal)).toContain('2 ×Poulet braisé');
    expect(text(normal)).toContain('Bien cuit');
    expect(text(normal)).not.toContain('Normale');
    expect(text(screen.getByRole('listitem', { name: 'Commande n° 9 — Boissons' }))).toContain(
      'Moussa',
    );
    expect(text(screen.getByRole('status', { name: 'Résumé de la cuisine' }))).toBe(
      '2 en attente1 en préparation1 prêt',
    );
  });

  it('temps écoulé depuis la réception, mis à jour sans requête', async () => {
    vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] });
    vi.setSystemTime(new Date('2026-11-01T11:07:00Z'));
    serveBoard(board({ tickets: [ticket()] }));
    renderWithCapabilities(withToast(<KitchenPage />), { permissions: [VIEW], sites: ONE_SITE });
    expect(text(await screen.findByTestId('ticket-elapsed'))).toBe('depuis 7 min');
    act(() => {
      vi.setSystemTime(new Date('2026-11-01T11:20:00Z'));
      vi.advanceTimersByTime(30_000);
    });
    expect(text(screen.getByTestId('ticket-elapsed'))).toBe('depuis 20 min');
  });

  it('commencer et terminer ne portent que les lignes du ticket', async () => {
    serveBoard();
    renderWithCapabilities(withToast(<KitchenPage />), {
      permissions: [VIEW, PREPARE],
      sites: ONE_SITE,
    });
    const first = await screen.findByRole('listitem', { name: 'Commande n° 12 — Grillades' });
    fireEvent.click(within(first).getByRole('button', { name: 'Commencer' }));
    await waitFor(() =>
      expect(calls('POST')).toContainEqual([
        '/api/v1/restaurant/orders/o1/start',
        { line_ids: ['l1'] },
      ]),
    );
    const drinks = screen.getByRole('listitem', { name: 'Commande n° 9 — Boissons' });
    fireEvent.click(within(drinks).getByRole('button', { name: 'Prête' }));
    await waitFor(() =>
      expect(calls('POST')).toContainEqual([
        '/api/v1/restaurant/orders/o2/ready',
        { line_ids: ['l5'] },
      ]),
    );
    fireEvent.click(
      within(drinks).getByRole('button', { name: 'Remettre « Coca-Cola » en préparation' }),
    );
    await waitFor(() =>
      expect(calls('POST')).toContainEqual([
        '/api/v1/restaurant/orders/o2/revert',
        { line_ids: ['l6'] },
      ]),
    );
    // Servir reste au service (écran des commandes) : aucun bouton « Servir » en cuisine.
    expect(screen.queryByRole('button', { name: /Servir/ })).toBeNull();
  });

  it('sans la permission de préparer : écran en lecture seule', async () => {
    serveBoard();
    renderWithCapabilities(withToast(<KitchenPage />), { permissions: [VIEW], sites: ONE_SITE });
    await screen.findAllByTestId('kitchen-ticket');
    expect(screen.queryByRole('button', { name: 'Commencer' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Prête' })).toBeNull();
    expect(screen.queryByLabelText(/Priorité de la commande/)).toBeNull();
  });

  it('paiement à la commande non réglé : avertissement et aucune action de préparation', async () => {
    serveBoard(board({ tickets: [ticket({ awaiting_payment: true })] }));
    renderWithCapabilities(withToast(<KitchenPage />), {
      permissions: [VIEW, PREPARE],
      sites: ONE_SITE,
    });
    expect(
      await screen.findByText('Paiement à la commande : à régler avant la préparation.'),
    ).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Commencer' })).toBeNull();
  });

  it('filtre par poste (actif, désactivé, sans poste) envoyé au serveur et mémorisé', async () => {
    serveBoard();
    renderWithCapabilities(withToast(<KitchenPage />), { permissions: [VIEW], sites: ONE_SITE });
    await screen.findAllByTestId('kitchen-ticket');
    await pick('Poste', 'Boissons');
    expect(
      screen.getAllByRole('option', { name: 'Entrées (poste désactivé)', hidden: true }),
    ).toBeTruthy();
    await waitFor(() =>
      expect(calls().map(([u]) => u)).toContain(
        '/api/v1/restaurant/kitchen/board?site_id=s1&station_id=st2',
      ),
    );
    expect(localStorage.getItem('sm.kitchen.station')).toBe('st2');
    await pick('Poste', 'Sans poste');
    await waitFor(() =>
      expect(calls().map(([u]) => u)).toContain(
        '/api/v1/restaurant/kitchen/board?site_id=s1&unrouted=true',
      ),
    );
  });

  it('priorité modifiable avec la permission (classement laissé au serveur)', async () => {
    serveBoard();
    renderWithCapabilities(withToast(<KitchenPage />), {
      permissions: [VIEW, PRIORITY],
      sites: ONE_SITE,
    });
    await screen.findAllByTestId('kitchen-ticket');
    await pick('Priorité de la commande n° 12', 'Élevée');
    await waitFor(() =>
      expect(calls('PUT')).toEqual([
        ['/api/v1/restaurant/kitchen/orders/o1/priority', { priority: 'HIGH', reason: null }],
      ]),
    );
    await waitFor(() =>
      expect(show).toHaveBeenCalledWith(
        expect.objectContaining({ severity: 'success', summary: 'Priorité enregistrée.' }),
      ),
    );
  });

  it('actualisation périodique de l’écran', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'setTimeout', 'clearTimeout'] });
    serveBoard();
    renderWithCapabilities(withToast(<KitchenPage />), { permissions: [VIEW], sites: ONE_SITE });
    await vi.waitFor(() => expect(calls()).toHaveLength(1));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(KITCHEN_REFRESH_MS);
    });
    await vi.waitFor(() => expect(calls().length).toBeGreaterThanOrEqual(2));
  });

  it('plusieurs sites sans site actif : choisir un site d’abord', async () => {
    serveBoard();
    renderWithCapabilities(withToast(<KitchenPage />), { permissions: [VIEW] });
    expect(await screen.findByText('Choisissez un site pour afficher sa cuisine.')).toBeTruthy();
    expect(calls()).toHaveLength(0);
  });

  it('erreur du serveur traduite', async () => {
    serveBoard({ code: 'permission_denied', status: 403, title: 'x' }, 403);
    renderWithCapabilities(withToast(<KitchenPage />), { permissions: [VIEW], sites: ONE_SITE });
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(screen.queryAllByTestId('kitchen-ticket')).toHaveLength(0);
  });
});

const station = (over: Record<string, unknown> = {}) => ({
  id: 'st1',
  site_id: 's1',
  site_name: 'Boutique',
  name: 'Grillades',
  sort_order: 10,
  is_active: true,
  route_count: 2,
  created_at: '2026-11-01T08:00:00Z',
  updated_at: '2026-11-01T08:00:00Z',
  ...over,
});

const section = (over: Record<string, unknown> = {}) => ({
  id: 'sec1',
  site_id: 's1',
  site_name: 'Boutique',
  name: 'Grillades',
  sort_order: 0,
  is_active: true,
  item_count: 1,
  ...over,
});

const menuItem = (over: Record<string, unknown> = {}) => ({
  id: 'i1',
  site_id: 's1',
  section_id: 'sec1',
  section_name: 'Grillades',
  designation: 'Poulet braisé',
  display_name: null,
  packaging_name: null,
  ...over,
});

function serveStations(write: (url: string, body: unknown) => Response = () => jsonResponse({})) {
  fetchMock.mockImplementation(async (url, init) => {
    const u = String(url);
    if ((init?.method ?? 'GET') !== 'GET') {
      return write(u, init?.body ? JSON.parse(String(init.body)) : null);
    }
    if (u.includes('/kitchen/stations')) {
      return pageOf([station(), station({ id: 'st2', name: 'Boissons', route_count: 0 })]);
    }
    if (u.includes('/kitchen/routes')) {
      return jsonResponse([
        {
          id: 'r1',
          site_id: 's1',
          station_id: 'st1',
          station_name: 'Grillades',
          station_active: true,
          section_id: 'sec1',
          menu_item_id: null,
          updated_at: '2026-11-01T08:00:00Z',
        },
      ]);
    }
    if (u.includes('/menu/sections')) return pageOf([section()]);
    if (u.includes('/menu/items')) {
      return pageOf([menuItem(), menuItem({ id: 'i2', designation: 'Brochettes' })]);
    }
    return jsonResponse({});
  });
}

describe('postes de préparation (R6)', () => {
  it('liste, création et désactivation confirmée avec la permission de gérer', async () => {
    serveStations((u) =>
      u.endsWith('/deactivate')
        ? jsonResponse(
            {
              code: 'station_has_open_lines',
              status: 409,
              title: 'x',
              open_lines: 2,
            },
            409,
          )
        : jsonResponse(station({ id: 'st9', name: 'Entrées' }), 201),
    );
    renderWithCapabilities(withToast(<StationsPage />), {
      permissions: [VIEW, MANAGE],
      sites: ONE_SITE,
    });
    expect(await screen.findByText('Boissons')).toBeTruthy();
    fireEvent.click(screen.getAllByRole('button', { name: 'Nouveau poste' })[0]!);
    fireEvent.change(await screen.findByLabelText(/Nom du poste/), {
      target: { value: '  Entrées ' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() =>
      expect(calls('POST')).toContainEqual([
        '/api/v1/restaurant/kitchen/stations',
        expect.objectContaining({ name: 'Entrées' }),
      ]),
    );
    const row = screen.getByRole('row', { name: /Grillades/ });
    fireEvent.click(within(row).getByRole('button', { name: 'Désactiver' }));
    const confirm = (await screen.findAllByRole('dialog')).at(-1)!;
    expect(text(confirm)).toContain('Désactiver « Grillades » ?');
    fireEvent.click(within(confirm).getByRole('button', { name: 'Désactiver' }));
    await waitFor(() =>
      expect(calls('POST').map(([u]) => u)).toContain(
        '/api/v1/restaurant/kitchen/stations/st1/deactivate',
      ),
    );
    await waitFor(() =>
      expect(show).toHaveBeenCalledWith(
        expect.objectContaining({
          summary: expect.stringContaining('Des lignes en cours sont routées vers ce poste'),
        }),
      ),
    );
  });

  it('consultation seule : aucune action d’écriture', async () => {
    serveStations();
    renderWithCapabilities(withToast(<StationsPage />), { permissions: [VIEW], sites: ONE_SITE });
    expect(await screen.findByText('Boissons')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Nouveau poste' })).toBeNull();
    expect(screen.queryByRole('columnheader', { name: 'Actions' })).toBeNull();
  });

  it('affectations : section, élément hérité de sa section, changement envoyé au serveur', async () => {
    serveStations(() => jsonResponse(null));
    renderWithCapabilities(withToast(<StationsPage />), {
      permissions: [VIEW, MANAGE, 'restaurant.menu.view'],
      sites: ONE_SITE,
    });
    fireEvent.click(await screen.findByRole('tab', { name: 'Affectations du menu' }));
    await screen.findByRole('region', { name: 'Grillades' });
    expect(dropdownText('Poste de Grillades')).toContain('Grillades');
    expect(dropdownText('Poste de Brochettes')).toContain('Suit la section (Grillades)');
    await pick('Poste de Brochettes', 'Boissons');
    await waitFor(() =>
      expect(calls('PUT')).toEqual([
        [
          '/api/v1/restaurant/kitchen/routes',
          { site_id: 's1', station_id: 'st2', menu_item_id: 'i2' },
        ],
      ]),
    );
  });
});
