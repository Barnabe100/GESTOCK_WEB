export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '/api/v1';

export async function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { Accept: 'application/json' },
    credentials: 'include',
    signal,
  });
  if (!response.ok) {
    throw new ApiError(response.status, `GET ${path} a échoué (${response.status})`);
  }
  return (await response.json()) as T;
}
