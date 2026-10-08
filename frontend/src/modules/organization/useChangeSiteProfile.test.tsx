// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { jsonResponse } from '@/shared/testing';

import { orgKeys, useChangeSiteProfile } from './api';

describe('changement de profil : invalidation après succès (palier E)', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('capacités, sites et modules sont rechargés ; rien après un refus', async () => {
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => useChangeSiteProfile(), { wrapper });
    const body = { siteId: 's1', profile_code: 'restaurant.maquis', preview_fingerprint: 'f' };

    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ code: 'profile_preview_outdated', detail: 'x' }, 409)),
    );
    await act(async () => {
      await result.current.mutateAsync(body).catch(() => undefined);
    });
    expect(invalidate).not.toHaveBeenCalled();

    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ site_id: 's1' })),
    );
    await act(async () => {
      await result.current.mutateAsync(body);
    });
    const keys = invalidate.mock.calls.map(([filters]) => filters?.queryKey);
    expect(keys).toEqual(
      expect.arrayContaining([orgKeys.sites, orgKeys.modules, ['capabilities']]),
    );
  });
});
