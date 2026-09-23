/**
 * Client HTTP de l'API.
 *
 * - Jeton d'accès conservé en mémoire uniquement (jamais en localStorage).
 * - Jeton de rafraîchissement : cookie HttpOnly posé par le backend (inaccessible au JS).
 * - En cas de 401, un seul rafraîchissement est tenté (mutualisé entre requêtes concurrentes).
 * - Les erreurs suivent le format Problem Details ; `code` est traduit par l'interface.
 */

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '/api/v1';

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: string;
  readonly extra: Record<string, unknown>;

  constructor(status: number, code: string, detail: string, extra: Record<string, unknown> = {}) {
    super(detail);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.detail = detail;
    this.extra = extra;
  }
}

interface ClientState {
  accessToken: string | null;
  siteId: string | null;
  refresh: (() => Promise<string | null>) | null;
  onUnauthenticated: (() => void) | null;
}

const state: ClientState = {
  accessToken: null,
  siteId: null,
  refresh: null,
  onUnauthenticated: null,
};

let refreshInFlight: Promise<string | null> | null = null;

export const apiSession = {
  setAccessToken(token: string | null) {
    state.accessToken = token;
  },
  getAccessToken() {
    return state.accessToken;
  },
  setSiteId(siteId: string | null) {
    state.siteId = siteId;
  },
  getSiteId() {
    return state.siteId;
  },
  /** Fonction appelée pour obtenir un nouveau jeton d'accès après un 401. */
  setRefreshHandler(handler: (() => Promise<string | null>) | null) {
    state.refresh = handler;
  },
  setUnauthenticatedHandler(handler: (() => void) | null) {
    state.onUnauthenticated = handler;
  },
};

export async function parseError(response: Response): Promise<ApiError> {
  let body: Record<string, unknown> = {};
  try {
    body = (await response.json()) as Record<string, unknown>;
  } catch {
    // corps absent ou non JSON
  }
  const { code, detail, ...extra } = body;
  return new ApiError(
    response.status,
    typeof code === 'string' ? code : 'unknown',
    typeof detail === 'string' ? detail : response.statusText,
    extra,
  );
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
  signal?: AbortSignal;
  /** Désactive la tentative de rafraîchissement (endpoints d'authentification). */
  skipRefresh?: boolean;
}

async function send(path: string, options: RequestOptions): Promise<Response> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  if (state.accessToken) headers.Authorization = `Bearer ${state.accessToken}`;
  if (state.siteId) headers['X-Site-Id'] = state.siteId;
  try {
    return await fetch(`${API_BASE}${path}`, {
      method: options.method ?? 'GET',
      headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      credentials: 'include',
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError(0, 'network_error', 'Serveur injoignable');
  }
}

async function refreshOnce(): Promise<string | null> {
  if (!state.refresh) return null;
  refreshInFlight ??= state.refresh().finally(() => {
    refreshInFlight = null;
  });
  return refreshInFlight;
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  let response = await send(path, options);
  if (response.status === 401 && !options.skipRefresh && state.accessToken) {
    const token = await refreshOnce();
    if (token) {
      response = await send(path, options);
    }
  }
  if (response.status === 401 && !options.skipRefresh) {
    state.onUnauthenticated?.();
  }
  if (!response.ok) {
    throw await parseError(response);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => apiRequest<T>(path, { signal }),
  post: <T>(path: string, body?: unknown) => apiRequest<T>(path, { method: 'POST', body }),
  put: <T>(path: string, body?: unknown) => apiRequest<T>(path, { method: 'PUT', body }),
  patch: <T>(path: string, body?: unknown) => apiRequest<T>(path, { method: 'PATCH', body }),
  delete: <T>(path: string) => apiRequest<T>(path, { method: 'DELETE' }),
};
