import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/core/api/client';
import type { Page } from '@/shared/lib/serverTable';

/** Caisse d'un site (jamais d'un utilisateur). Code CAI-001 attribué par le serveur. */
export interface CashRegister {
  id: string;
  code: string;
  name: string;
  description: string | null;
  site_id: string;
  site_name: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  current_session: {
    id: string;
    number: string;
    opened_at: string;
    opened_by_name: string | null;
    opening_float: string;
    theoretical_balance: string;
  } | null;
}

export type CashSessionStatus = 'OPEN' | 'CLOSED';
export const CASH_SESSION_STATUSES: readonly CashSessionStatus[] = ['OPEN', 'CLOSED'];

/** Session : montants calculés par le serveur (solde théorique, écart). */
export interface CashSession {
  id: string;
  number: string;
  cash_register_id: string;
  cash_register_code: string;
  cash_register_name: string;
  site_id: string;
  site_name: string;
  status: CashSessionStatus;
  opening_float: string;
  opened_at: string;
  opened_by_name: string | null;
  closed_at: string | null;
  closed_by_name: string | null;
  cash_in_total: string;
  cash_out_total: string;
  theoretical_balance: string;
  counted_balance: string | null;
  variance: string | null;
  closing_note: string | null;
  movement_count: number;
}

export type CashMovementType =
  'OPENING_FLOAT' | 'SALE_CASH_IN' | 'SALE_CASH_REVERSAL' | 'MANUAL_CASH_IN' | 'MANUAL_CASH_OUT';
export const CASH_MOVEMENT_TYPES: readonly CashMovementType[] = [
  'OPENING_FLOAT',
  'SALE_CASH_IN',
  'SALE_CASH_REVERSAL',
  'MANUAL_CASH_IN',
  'MANUAL_CASH_OUT',
];

export type CashMovementCategory =
  'CASH_ADDITION' | 'EXPENSE' | 'BANK_DEPOSIT' | 'WITHDRAWAL' | 'CORRECTION' | 'OTHER';
/** Natures proposées selon le sens (le serveur contrôle la cohérence). */
export const IN_CATEGORIES: readonly CashMovementCategory[] = [
  'CASH_ADDITION',
  'CORRECTION',
  'OTHER',
];
export const OUT_CATEGORIES: readonly CashMovementCategory[] = [
  'EXPENSE',
  'BANK_DEPOSIT',
  'WITHDRAWAL',
  'CORRECTION',
  'OTHER',
];

export interface CashMovement {
  id: string;
  cash_session_id: string;
  cash_session_number: string;
  cash_register_id: string;
  cash_register_code: string;
  cash_register_name: string;
  site_id: string;
  site_name: string;
  movement_type: CashMovementType;
  amount: string;
  signed_amount: string;
  category: CashMovementCategory | null;
  reason: string | null;
  reference: string | null;
  source_type: string | null;
  source_id: string | null;
  source_number: string | null;
  payment_id: string | null;
  occurred_at: string;
  created_by_name: string | null;
  balance_after: string;
}

export interface RegisterInput {
  site_id?: string | null;
  name: string;
  description: string | null;
}

export interface MovementInput {
  movement_type: 'MANUAL_CASH_IN' | 'MANUAL_CASH_OUT';
  amount: string;
  category: CashMovementCategory;
  reason: string;
  reference: string | null;
  idempotency_key: string;
}

export const cashKeys = { all: ['cash'] as const };

export function useCashRegisters(query: string, enabled = true) {
  return useQuery({
    queryKey: [...cashKeys.all, 'registers', query],
    queryFn: ({ signal }) => api.get<Page<CashRegister>>(`/cash/registers?${query}`, signal),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useCashSessions(query: string, enabled = true) {
  return useQuery({
    queryKey: [...cashKeys.all, 'sessions', query],
    queryFn: ({ signal }) => api.get<Page<CashSession>>(`/cash/sessions?${query}`, signal),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useCashSession(id: string | undefined) {
  return useQuery({
    queryKey: [...cashKeys.all, 'session', id],
    queryFn: ({ signal }) => api.get<CashSession>(`/cash/sessions/${id}`, signal),
    enabled: id !== undefined,
  });
}

/** Journal : d'une session (`sessionId`) ou de tous les mouvements visibles. */
export function useCashMovements(query: string, sessionId?: string) {
  const path = sessionId ? `/cash/sessions/${sessionId}/movements` : '/cash/movements';
  return useQuery({
    queryKey: [...cashKeys.all, 'movements', sessionId ?? 'all', query],
    queryFn: ({ signal }) => api.get<Page<CashMovement>>(`${path}?${query}`, signal),
    placeholderData: keepPreviousData,
  });
}

/** Toute écriture de caisse recharge caisses, sessions et journaux. */
export function useCashMutations() {
  const qc = useQueryClient();
  const onSuccess = () => void qc.invalidateQueries({ queryKey: cashKeys.all });
  return {
    saveRegister: useMutation({
      mutationFn: ({ id, input }: { id?: string; input: RegisterInput }) =>
        id
          ? api.patch<CashRegister>(`/cash/registers/${id}`, input)
          : api.post<CashRegister>('/cash/registers', input),
      onSuccess,
    }),
    setActive: useMutation({
      mutationFn: ({ id, active }: { id: string; active: boolean }) =>
        api.post<CashRegister>(`/cash/registers/${id}/${active ? 'activate' : 'deactivate'}`),
      onSuccess,
    }),
    open: useMutation({
      mutationFn: (input: { cash_register_id: string; opening_float: string }) =>
        api.post<CashSession>('/cash/sessions', input),
      onSuccess,
    }),
    close: useMutation({
      mutationFn: ({ id, counted, note }: { id: string; counted: string; note: string | null }) =>
        api.post<CashSession>(`/cash/sessions/${id}/close`, { counted_balance: counted, note }),
      onSuccess,
    }),
    move: useMutation({
      mutationFn: ({ sessionId, input }: { sessionId: string; input: MovementInput }) =>
        api.post<CashMovement>(`/cash/sessions/${sessionId}/movements`, input),
      onSuccess,
    }),
  };
}
