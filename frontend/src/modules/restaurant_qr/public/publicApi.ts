import { parseError } from '@/core/api/client';

/**
 * Client des routes PUBLIQUES du QR Code (R7, ADR-0052) : aucun jeton de session, aucun cookie
 * (`credentials: 'omit'`), aucun site sélectionné. Le secret (jeton du point ou secret de
 * suivi) est envoyé dans un EN-TÊTE, jamais dans l'adresse de la requête.
 */

const BASE = `${import.meta.env.VITE_API_BASE_URL ?? '/api/v1'}/public/restaurant/qr`;

export type ContactFieldMode = 'HIDDEN' | 'OPTIONAL' | 'REQUIRED';

export interface PublicMenuItem {
  id: string;
  name: string;
  packaging_name: string | null;
  description: string | null;
  price: string;
  available: boolean;
}

export interface PublicMenu {
  establishment: string;
  site_name: string;
  point: { kind: 'TABLE' | 'COUNTER'; label: string };
  currency: string;
  sections: { id: string; name: string; items: PublicMenuItem[] }[];
  ordering: {
    max_lines: number;
    max_quantity_per_line: number;
    customer_name_mode: ContactFieldMode;
    customer_phone_mode: ContactFieldMode;
  };
}

export interface PublicOrderInput {
  client_key: string;
  lines: { menu_item_id: string; quantity: number; note?: string | null }[];
  customer_name?: string | null;
  customer_phone?: string | null;
}

export interface PublicOrderPlaced {
  daily_number: number;
  status: string;
  tracking_url: string;
  replayed: boolean;
}

export interface PublicTracking {
  establishment: string;
  site_name: string;
  point: { kind: 'TABLE' | 'COUNTER'; label: string } | null;
  currency: string;
  daily_number: number;
  status: 'PENDING_CONFIRMATION' | 'OPEN' | 'CLOSED' | 'CANCELLED' | 'REJECTED';
  prep_status: string;
  lines: { name: string; packaging_name: string | null; quantity: string; status: string }[];
  total: string;
  remaining_due: string;
  rejection_reason: string | null;
  available_until: string | null;
}

async function request<T>(
  path: string,
  header: 'X-QR-Token' | 'X-QR-Tracking',
  secret: string,
  init: { method?: string; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    method: init.method ?? 'GET',
    credentials: 'omit',
    cache: 'no-store',
    referrerPolicy: 'no-referrer',
    signal: init.signal,
    headers: {
      [header]: secret,
      ...(init.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    },
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
  if (!response.ok) throw await parseError(response);
  return (await response.json()) as T;
}

export const publicQrApi = {
  menu: (token: string, signal?: AbortSignal) =>
    request<PublicMenu>('/menu', 'X-QR-Token', token, { signal }),
  order: (token: string, input: PublicOrderInput) =>
    request<PublicOrderPlaced>('/orders', 'X-QR-Token', token, { method: 'POST', body: input }),
  track: (secret: string, signal?: AbortSignal) =>
    request<PublicTracking>('/track', 'X-QR-Tracking', secret, { signal }),
};
