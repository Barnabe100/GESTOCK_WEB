/**
 * Client HTTP de la console TechNova (ADR-0031) : API distincte de celle des entreprises.
 *
 * - Session : cookie HttpOnly posé par la console (jamais lisible par le JavaScript) ;
 *   aucun jeton en mémoire, aucun `localStorage`.
 * - En-tête `X-TechNova-Console` sur chaque requête (anti-CSRF exigé par le serveur).
 * - Erreurs au format Problem Details, `code` traduit par l'interface (`errors.json`).
 */
import { ApiError, parseError } from '@/core/api/client';

export const CONSOLE_API_BASE = import.meta.env.VITE_PLATFORM_API_BASE_URL ?? '/platform-api/v1';

let onUnauthenticated: (() => void) | null = null;

export function setConsoleUnauthenticatedHandler(handler: (() => void) | null) {
  onUnauthenticated = handler;
}

export async function consoleRequest<T>(
  path: string,
  options: { method?: 'GET' | 'POST' | 'PATCH'; body?: unknown; silent401?: boolean } = {},
): Promise<T> {
  const headers: Record<string, string> = {
    Accept: 'application/json',
    'X-TechNova-Console': '1',
  };
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  let response: Response;
  try {
    response = await fetch(`${CONSOLE_API_BASE}${path}`, {
      method: options.method ?? 'GET',
      headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      credentials: 'same-origin',
    });
  } catch {
    throw new ApiError(0, 'network_error', 'Serveur injoignable');
  }
  if (response.status === 401 && !options.silent401) onUnauthenticated?.();
  if (!response.ok) throw await parseError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
