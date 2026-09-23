import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';

import { ApiError, apiRequest, apiSession } from '@/core/api/client';
import type { SessionResponse } from '@/core/api/types';

import { AuthContext, type AuthContextValue, type AuthState } from './AuthContext';
import { tabStorage } from './storage';

const ANONYMOUS: AuthState = { status: 'anonymous', user: null, tenantId: null, memberships: [] };

function stateFrom(session: SessionResponse): AuthState {
  return {
    status: 'authenticated',
    user: session.user,
    tenantId: session.tenant_id,
    memberships: session.memberships,
  };
}

async function requestRefresh(tenantId: string | null): Promise<SessionResponse> {
  return apiRequest<SessionResponse>('/auth/refresh', {
    method: 'POST',
    body: tenantId ? { tenant_id: tenantId } : {},
    skipRefresh: true,
  });
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [state, setState] = useState<AuthState>({ ...ANONYMOUS, status: 'loading' });

  const applySession = useCallback((session: SessionResponse) => {
    apiSession.setAccessToken(session.access_token);
    tabStorage.setTenantId(session.tenant_id);
    apiSession.setSiteId(session.tenant_id ? tabStorage.getSiteId(session.tenant_id) : null);
    setState(stateFrom(session));
  }, []);

  const reset = useCallback(() => {
    apiSession.setAccessToken(null);
    apiSession.setSiteId(null);
    queryClient.clear();
    setState(ANONYMOUS);
  }, [queryClient]);

  /** Rafraîchit la session pour l'entreprise de l'onglet ; retombe sans entreprise si refusée. */
  const refreshSession = useCallback(async (): Promise<SessionResponse> => {
    const tenantId = tabStorage.getTenantId();
    try {
      return await requestRefresh(tenantId);
    } catch (error) {
      if (tenantId && error instanceof ApiError && error.status === 403) {
        tabStorage.setTenantId(null);
        return requestRefresh(null);
      }
      throw error;
    }
  }, []);

  useEffect(() => {
    apiSession.setRefreshHandler(async () => {
      try {
        const session = await refreshSession();
        applySession(session);
        return session.access_token;
      } catch {
        return null;
      }
    });
    apiSession.setUnauthenticatedHandler(reset);

    let cancelled = false;
    refreshSession()
      .then((session) => !cancelled && applySession(session))
      .catch(() => !cancelled && setState(ANONYMOUS));
    return () => {
      cancelled = true;
      apiSession.setRefreshHandler(null);
      apiSession.setUnauthenticatedHandler(null);
    };
  }, [applySession, refreshSession, reset]);

  const value = useMemo<AuthContextValue>(
    () => ({
      ...state,
      async login(email, password) {
        const session = await apiRequest<SessionResponse>('/auth/login', {
          method: 'POST',
          body: { email, password, tenant_id: tabStorage.getTenantId() ?? undefined },
          skipRefresh: true,
        }).catch(async (error: unknown) => {
          // Entreprise mémorisée devenue inaccessible : nouvelle tentative sans elle.
          if (error instanceof ApiError && error.code === 'tenant_access_denied') {
            tabStorage.setTenantId(null);
            return apiRequest<SessionResponse>('/auth/login', {
              method: 'POST',
              body: { email, password },
              skipRefresh: true,
            });
          }
          throw error;
        });
        queryClient.clear();
        applySession(session);
      },
      async logout() {
        try {
          await apiRequest('/auth/logout', { method: 'POST', skipRefresh: true });
        } finally {
          tabStorage.setTenantId(null);
          reset();
        }
      },
      async selectTenant(tenantId) {
        const session = await requestRefresh(tenantId);
        queryClient.clear();
        applySession(session);
      },
      async changePassword(currentPassword, newPassword) {
        await apiRequest('/me/password', {
          method: 'POST',
          body: { current_password: currentPassword, new_password: newPassword },
        });
        const session = await refreshSession();
        applySession(session);
      },
    }),
    [state, applySession, queryClient, refreshSession, reset],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
