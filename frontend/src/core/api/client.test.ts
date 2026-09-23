import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, api, apiSession } from './client';

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('client API', () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    apiSession.setAccessToken('ancien');
    apiSession.setSiteId('site-1');
  });

  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
    apiSession.setRefreshHandler(null);
    apiSession.setUnauthenticatedHandler(null);
  });

  it('envoie le jeton et le site actif', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { ok: true }));
    await api.get('/sites');
    const headers = fetchMock.mock.calls[0]?.[1]?.headers as Record<string, string>;
    expect(headers.Authorization).toBe('Bearer ancien');
    expect(headers['X-Site-Id']).toBe('site-1');
  });

  it('transforme une erreur Problem Details en ApiError avec son code', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(403, {
        code: 'permission_denied',
        detail: 'Permission insuffisante',
        status: 403,
      }),
    );
    const error = await api.get('/sites').catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBe('permission_denied');
    expect((error as ApiError).extra).toEqual({ status: 403 });
  });

  it('rafraîchit une seule fois le jeton puis rejoue les requêtes', async () => {
    const refresh = vi.fn(async () => {
      apiSession.setAccessToken('nouveau');
      return 'nouveau';
    });
    apiSession.setRefreshHandler(refresh);
    fetchMock.mockImplementation(async (_url, init) => {
      const auth = (init?.headers as Record<string, string>).Authorization;
      return auth === 'Bearer nouveau'
        ? jsonResponse(200, { ok: true })
        : jsonResponse(401, { code: 'invalid_token' });
    });

    await expect(Promise.all([api.get('/a'), api.get('/b')])).resolves.toEqual([
      { ok: true },
      { ok: true },
    ]);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it('signale la perte de session si le rafraîchissement échoue', async () => {
    const onUnauthenticated = vi.fn();
    apiSession.setRefreshHandler(async () => null);
    apiSession.setUnauthenticatedHandler(onUnauthenticated);
    fetchMock.mockResolvedValue(jsonResponse(401, { code: 'session_expired' }));
    await expect(api.get('/a')).rejects.toMatchObject({ code: 'session_expired' });
    expect(onUnauthenticated).toHaveBeenCalled();
  });

  it('distingue une erreur réseau', async () => {
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    await expect(api.get('/a')).rejects.toMatchObject({ code: 'network_error', status: 0 });
  });
});
