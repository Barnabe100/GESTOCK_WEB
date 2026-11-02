import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/core/api/client';
import type { Order, OrderPriority } from '@/modules/restaurant_orders/api';
import type { Page } from '@/shared/lib/serverTable';

/**
 * Cuisine des sites de restauration (palier R6, ADR-0050) : postes de préparation, affectations
 * du menu, priorité et poste des lignes, écran cuisine. Le serveur fait foi : routage des lignes
 * (élément, puis section, sinon « Sans poste »), unicités, postes actifs du même site,
 * désactivations protégées, classement des tickets (priorité, PUIS ancienneté).
 */

export const STATION_VIEW = 'restaurant.kitchen.station.view';
export const STATION_MANAGE = 'restaurant.kitchen.station.manage';
export const LINE_ROUTE = 'restaurant.kitchen.line.route';
export const PRIORITY_SET = 'restaurant.kitchen.priority.set';
export const KITCHEN_MODULE = 'restaurant.kitchen';

/** Bornes du serveur (`restaurant_kitchen/schemas.py`). */
export const SORT_ORDER_MAX = 100_000;

/** Écran cuisine actualisé toutes les 10 s (aucun rechargement manuel nécessaire). */
export const KITCHEN_REFRESH_MS = 10_000;

export type Priority = OrderPriority;
export const PRIORITIES: readonly Priority[] = ['NORMAL', 'HIGH', 'URGENT'];

export interface Station {
  id: string;
  site_id: string;
  site_name: string;
  name: string;
  sort_order: number;
  is_active: boolean;
  /** Sections et éléments du menu affectés à ce poste. */
  route_count: number;
  created_at: string;
  updated_at: string;
}

export interface StationRoute {
  id: string;
  site_id: string;
  station_id: string;
  station_name: string;
  station_active: boolean;
  section_id: string | null;
  menu_item_id: string | null;
  updated_at: string;
}

/** Colonne d'un ticket : état de sa ligne la MOINS avancée (calculé par le serveur). */
export type TicketState = 'RECEIVED' | 'IN_PREPARATION' | 'READY';
export const TICKET_STATES: readonly TicketState[] = ['RECEIVED', 'IN_PREPARATION', 'READY'];

export interface BoardLine {
  id: string;
  line_no: number;
  label: string;
  packaging_name: string | null;
  quantity: string;
  unit: string;
  note: string | null;
  status: TicketState;
  received_at: string;
  prepared_at: string | null;
  ready_at: string | null;
  /** Palier R8 : présentation commandée (fiche technique de la recette). */
  article_id?: string | null;
  packaging_id?: string | null;
}

/** Une commande ouverte × un poste (`station_id` nul : « Sans poste »). */
export interface Ticket {
  order_id: string;
  daily_number: number;
  business_date: string;
  service_mode: 'ON_SITE' | 'COUNTER' | 'TAKEAWAY';
  table_label: string | null;
  call_name: string | null;
  priority: Priority;
  assigned_name: string | null;
  /** Paiement à la commande non réglé : préparation refusée par le serveur. */
  awaiting_payment: boolean;
  station_id: string | null;
  station_name: string | null;
  state: TicketState;
  /** Réception de la première ligne du ticket (ancienneté). */
  received_at: string;
  order_created_at: string;
  lines: BoardLine[];
}

export interface KitchenBoard {
  site_id: string;
  site_name: string;
  stations: { id: string; name: string; is_active: boolean }[];
  counts: { received: number; in_preparation: number; ready: number };
  /** Ordre du serveur : priorité décroissante, puis ancienneté. */
  tickets: Ticket[];
}

/** Filtre de l'écran : tous les postes, « Sans poste » ou un poste. */
export type StationFilter = 'all' | 'none' | string;

export interface StationInput {
  site_id?: string | null;
  name: string;
  sort_order: number;
}

export interface RouteInput {
  site_id: string;
  section_id?: string;
  menu_item_id?: string;
  station_id: string | null;
}

export const kitchenKeys = {
  all: ['restaurant.kitchen'] as const,
  stations: ['restaurant.kitchen', 'stations'] as const,
  routes: ['restaurant.kitchen', 'routes'] as const,
  board: ['restaurant.kitchen', 'board'] as const,
};

export function useStations(query: string, enabled = true) {
  return useQuery({
    queryKey: [...kitchenKeys.stations, query],
    queryFn: ({ signal }) =>
      api.get<Page<Station>>(`/restaurant/kitchen/stations?${query}`, signal),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useRoutes(siteId: string | null) {
  return useQuery({
    queryKey: [...kitchenKeys.routes, siteId],
    queryFn: ({ signal }) =>
      api.get<StationRoute[]>(`/restaurant/kitchen/routes?site_id=${siteId ?? ''}`, signal),
    enabled: siteId !== null,
  });
}

function boardQuery(siteId: string, filter: StationFilter): string {
  const query = new URLSearchParams({ site_id: siteId });
  if (filter === 'none') query.set('unrouted', 'true');
  else if (filter !== 'all') query.set('station_id', filter);
  return query.toString();
}

/** Écran cuisine d'UN site, actualisé périodiquement. */
export function useKitchenBoard(siteId: string | null, filter: StationFilter) {
  return useQuery({
    queryKey: [...kitchenKeys.board, siteId, filter],
    queryFn: ({ signal }) =>
      api.get<KitchenBoard>(
        `/restaurant/kitchen/board?${boardQuery(siteId ?? '', filter)}`,
        signal,
      ),
    refetchInterval: KITCHEN_REFRESH_MS,
    placeholderData: keepPreviousData,
    enabled: siteId !== null,
  });
}

/** Écritures : le serveur contrôle tout ; cuisine et commandes rafraîchies. */
export function useKitchenMutations() {
  const qc = useQueryClient();
  const onSuccess = () => {
    void qc.invalidateQueries({ queryKey: kitchenKeys.all });
    void qc.invalidateQueries({ queryKey: ['restaurant.orders'] });
  };
  return {
    createStation: useMutation({
      mutationFn: (input: StationInput) => api.post<Station>('/restaurant/kitchen/stations', input),
      onSuccess,
    }),
    updateStation: useMutation({
      mutationFn: ({ id, input }: { id: string; input: StationInput }) =>
        api.put<Station>(`/restaurant/kitchen/stations/${id}`, input),
      onSuccess,
    }),
    setStationActive: useMutation({
      mutationFn: ({ id, active }: { id: string; active: boolean }) =>
        api.post<Station>(
          `/restaurant/kitchen/stations/${id}/${active ? 'activate' : 'deactivate'}`,
        ),
      onSuccess,
    }),
    setRoute: useMutation({
      mutationFn: (input: RouteInput) =>
        api.put<StationRoute | null>('/restaurant/kitchen/routes', input),
      onSuccess,
    }),
    setPriority: useMutation({
      mutationFn: ({ id, priority, reason }: { id: string; priority: Priority; reason?: string }) =>
        api.put<Order>(`/restaurant/kitchen/orders/${id}/priority`, {
          priority,
          reason: reason ?? null,
        }),
      onSuccess,
    }),
    routeLines: useMutation({
      mutationFn: ({
        id,
        lineIds,
        stationId,
        reason,
      }: {
        id: string;
        lineIds: string[];
        stationId: string | null;
        reason?: string | null;
      }) =>
        api.put<Order>(`/restaurant/kitchen/orders/${id}/route`, {
          line_ids: lineIds,
          station_id: stationId,
          reason: reason ?? null,
        }),
      onSuccess,
    }),
  };
}
