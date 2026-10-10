import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/core/api/client';
import type {
  CreditOverride,
  ExpiredLotOverride,
  ImmediatePayment,
  SalePaymentStatus,
} from '@/modules/sales/api';
import type { Page } from '@/shared/lib/serverTable';

/**
 * Commandes de restauration (palier R2, ADR-0049) : le serveur fait foi pour tout (prix figés,
 * états, protection, règlement) ; montants et quantités en chaînes décimales.
 */

export const ORDER_VIEW = 'restaurant.orders.order.view';
export const ORDER_CREATE = 'restaurant.orders.order.create';
export const ORDER_CLAIM = 'restaurant.orders.order.claim';
export const ORDER_PREPARE = 'restaurant.orders.order.prepare';
export const ORDER_SERVE = 'restaurant.orders.order.serve';
export const ORDER_CANCEL = 'restaurant.orders.order.cancel';
export const ORDER_CANCEL_PREPARED = 'restaurant.orders.order.cancel_prepared';
export const ORDER_REASSIGN = 'restaurant.orders.order.reassign';
export const SETTINGS_MANAGE = 'restaurant.orders.settings.manage';
/** Règlement : permissions EXISTANTES des ventes (aucune permission propre aux commandes). */
export const SETTLE_PERMISSIONS = ['sales.sale.create', 'sales.sale.validate'] as const;

/** Écran de suivi actualisé régulièrement (plusieurs postes travaillent en parallèle). */
export const ORDERS_REFRESH_MS = 15_000;

export type PaymentTiming = 'AT_END' | 'AT_ORDER';
export type ServiceMode = 'ON_SITE' | 'COUNTER' | 'TAKEAWAY';
export type OrderStatus = 'PENDING_CONFIRMATION' | 'OPEN' | 'CLOSED' | 'CANCELLED' | 'REJECTED';
export type SettlementStatus = 'UNSETTLED' | 'SETTLED';
export type LineStatus = 'RECEIVED' | 'IN_PREPARATION' | 'READY' | 'SERVED' | 'CANCELLED';
export type PrepStatus = 'RECEIVED' | 'IN_PREPARATION' | 'READY' | 'SERVED' | 'NONE';
export type OrderChannel = 'STAFF' | 'POS' | 'QR';
export type OrderStateFilter = 'active' | 'closed' | 'cancelled' | 'all';
export type TransitionAction = 'start' | 'ready' | 'revert' | 'serve';

export const SERVICE_MODES: readonly ServiceMode[] = ['ON_SITE', 'COUNTER', 'TAKEAWAY'];

export interface OrderSettings {
  site_id: string;
  payment_timing: PaymentTiming;
  claim_protection_minutes: number;
  claim_cooldown_minutes: number;
  qr_auto_accept: boolean;
  updated_at: string;
}

export interface OrderLine {
  id: string;
  line_no: number;
  menu_item_id: string;
  article_id: string;
  packaging_id: string | null;
  label: string;
  packaging_name: string | null;
  unit: string;
  conversion: string | null;
  /** Prix FIGÉ à la saisie de la ligne. */
  unit_price: string;
  quantity: string;
  base_quantity: string;
  line_total: string;
  note: string | null;
  status: LineStatus;
  created_at: string;
  prepared_at: string | null;
  ready_at: string | null;
  served_at: string | null;
  cancelled_at: string | null;
  cancel_reason: string | null;
}

export interface LineCounts {
  received: number;
  in_preparation: number;
  ready: number;
  served: number;
  cancelled: number;
}

export interface Order {
  id: string;
  site_id: string;
  site_name: string;
  business_date: string;
  daily_number: number;
  channel: OrderChannel;
  service_mode: ServiceMode;
  customer_id: string | null;
  customer_name: string | null;
  call_name: string | null;
  status: OrderStatus;
  prep_status: PrepStatus;
  settlement_status: SettlementStatus;
  payment_timing: PaymentTiming;
  assigned_user_id: string | null;
  assigned_name: string | null;
  assigned_at: string | null;
  created_by: string | null;
  created_by_name: string | null;
  total: string;
  /** État financier LU sur la vente active (P-11) ; détail des paiements : ventes. */
  sale_id: string | null;
  sale_number: string | null;
  payment_status: SalePaymentStatus | null;
  amount_due: string | null;
  line_counts: LineCounts;
  created_at: string;
  last_served_at: string | null;
  updated_at: string;
  closed_at: string | null;
  cancelled_at: string | null;
  cancel_reason: string | null;
  version: number;
  lines: OrderLine[] | null;
}

export interface OrderEvent {
  id: string;
  event_type: string;
  actor_kind: string;
  actor_user_id: string | null;
  actor_name: string | null;
  reason: string | null;
  line_ids: string[];
  data: Record<string, unknown>;
  occurred_at: string;
}

/** Ticket de retrait (D14, Q3) construit par le serveur : aucun prix, coût ni stock. */
export interface OrderTicket {
  company_name: string;
  site_name: string;
  business_date: string;
  daily_number: number;
  call_name: string | null;
  service_mode: ServiceMode;
  created_at: string;
  timezone: string;
  lines: {
    label: string;
    packaging_name: string | null;
    quantity: string;
    unit: string;
    note: string | null;
  }[];
}

export interface Assignee {
  user_id: string;
  full_name: string;
}

export interface OrderLineInput {
  menu_item_id: string;
  quantity: string;
  note: string | null;
}

export interface OrderCreateInput {
  site_id: string | null;
  service_mode: ServiceMode;
  customer_id: string | null;
  call_name: string | null;
  lines: OrderLineInput[];
  idempotency_key: string;
}

export interface SettleInput {
  payments: ImmediatePayment[];
  credit_override: CreditOverride | null;
  expired_lot_override: ExpiredLotOverride | null;
  idempotency_key: string;
}

export interface Settlement {
  order: Order;
  sale_id: string;
  replayed: boolean;
}

export const orderKeys = {
  all: ['restaurant.orders'] as const,
  list: ['restaurant.orders', 'list'] as const,
  detail: (id: string) => ['restaurant.orders', 'detail', id] as const,
  events: (id: string) => ['restaurant.orders', 'events', id] as const,
  settings: (siteId: string) => ['restaurant.orders', 'settings', siteId] as const,
};

export function useOrders(query: string, enabled = true) {
  return useQuery({
    queryKey: [...orderKeys.list, query],
    queryFn: ({ signal }) => api.get<Page<Order>>(`/restaurant/orders?${query}`, signal),
    placeholderData: keepPreviousData,
    refetchInterval: ORDERS_REFRESH_MS,
    enabled,
  });
}

export function useOrder(id: string | undefined) {
  return useQuery({
    queryKey: orderKeys.detail(id ?? ''),
    queryFn: ({ signal }) => api.get<Order>(`/restaurant/orders/${id ?? ''}`, signal),
    refetchInterval: ORDERS_REFRESH_MS,
    enabled: id !== undefined,
  });
}

export function useOrderEvents(id: string) {
  return useQuery({
    queryKey: orderKeys.events(id),
    queryFn: ({ signal }) => api.get<OrderEvent[]>(`/restaurant/orders/${id}/events`, signal),
  });
}

export function useOrderSettings(siteId: string | null) {
  return useQuery({
    queryKey: orderKeys.settings(siteId ?? ''),
    queryFn: ({ signal }) => api.get<OrderSettings>(`/restaurant/settings/${siteId ?? ''}`, signal),
    enabled: siteId !== null,
  });
}

export function useAssignees(orderId: string, enabled: boolean) {
  return useQuery({
    queryKey: [...orderKeys.detail(orderId), 'assignees'],
    queryFn: ({ signal }) => api.get<Assignee[]>(`/restaurant/orders/${orderId}/assignees`, signal),
    enabled,
  });
}

export function fetchTicket(orderId: string): Promise<OrderTicket> {
  return api.get<OrderTicket>(`/restaurant/orders/${orderId}/ticket`);
}

/** Écritures : le serveur fait foi ; commande, listes et historique rafraîchis. */
export function useOrderMutations() {
  const qc = useQueryClient();
  const onSuccess = () => void qc.invalidateQueries({ queryKey: orderKeys.all });
  const path = (id: string, action: string) => `/restaurant/orders/${id}/${action}`;
  return {
    create: useMutation({
      mutationFn: (input: OrderCreateInput) => api.post<Order>('/restaurant/orders', input),
      onSuccess,
    }),
    addLines: useMutation({
      mutationFn: ({
        id,
        lines,
        idempotencyKey,
      }: {
        id: string;
        lines: OrderLineInput[];
        idempotencyKey: string;
      }) => api.post<Order>(path(id, 'lines'), { lines, idempotency_key: idempotencyKey }),
      onSuccess,
    }),
    transition: useMutation({
      mutationFn: ({
        id,
        action,
        lineIds,
      }: {
        id: string;
        action: TransitionAction;
        lineIds?: string[];
      }) => api.post<Order>(path(id, action), lineIds ? { line_ids: lineIds } : undefined),
      onSuccess,
    }),
    cancelLines: useMutation({
      mutationFn: ({ id, lineIds, reason }: { id: string; lineIds: string[]; reason: string }) =>
        api.post<Order>(path(id, 'cancel-lines'), { line_ids: lineIds, reason }),
      onSuccess,
    }),
    cancel: useMutation({
      mutationFn: ({ id, reason }: { id: string; reason: string }) =>
        api.post<Order>(path(id, 'cancel'), { reason }),
      onSuccess,
    }),
    claim: useMutation({
      mutationFn: (id: string) => api.post<Order>(path(id, 'claim')),
      onSuccess,
    }),
    setCustomer: useMutation({
      mutationFn: ({
        id,
        customerId,
        reason,
      }: {
        id: string;
        customerId: string;
        reason: string | null;
      }) => api.put<Order>(path(id, 'customer'), { customer_id: customerId, reason }),
      onSuccess,
    }),
    reassign: useMutation({
      mutationFn: ({ id, assignee, reason }: { id: string; assignee: string; reason: string }) =>
        api.post<Order>(path(id, 'reassign'), { assignee_user_id: assignee, reason }),
      onSuccess,
    }),
    settle: useMutation({
      mutationFn: ({ id, input }: { id: string; input: SettleInput }) =>
        api.post<Settlement>(path(id, 'settle'), input),
      onSuccess: () => {
        onSuccess();
        // Ventes, stock, caisse et créances changent avec le règlement.
        void qc.invalidateQueries({ queryKey: ['sales'] });
      },
    }),
    updateSettings: useMutation({
      mutationFn: ({
        siteId,
        input,
      }: {
        siteId: string;
        input: Pick<
          OrderSettings,
          'payment_timing' | 'claim_protection_minutes' | 'claim_cooldown_minutes'
        >;
      }) => api.put<OrderSettings>(`/restaurant/settings/${siteId}`, input),
      onSuccess,
    }),
  };
}

/** Numéro affiché d'une commande : « n° 12 » — la référence pour le client et l'équipe. */
export function orderNumber(order: Pick<Order, 'daily_number'>): string {
  return `n° ${order.daily_number}`;
}

/** Nouvelle clé d'idempotence (une par saisie ; rejouée si la requête est renvoyée). */
export function newKey(): string {
  return crypto.randomUUID();
}
