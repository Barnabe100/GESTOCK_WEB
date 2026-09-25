import { useQueryClient } from '@tanstack/react-query';
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import { consoleRequest, setConsoleUnauthenticatedHandler } from './api';
import type { PlatformAdmin } from './types';

type Status = 'loading' | 'anonymous' | 'authenticated';

interface ConsoleAuthValue {
  status: Status;
  admin: PlatformAdmin | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const ConsoleAuthContext = createContext<ConsoleAuthValue | null>(null);

export function useConsoleAuth(): ConsoleAuthValue {
  const value = useContext(ConsoleAuthContext);
  if (!value) throw new Error('useConsoleAuth hors de ConsoleAuthProvider');
  return value;
}

/**
 * Session de la console TechNova : le serveur est seul juge (cookie HttpOnly, contrôle
 * `is_platform_admin`). L'interface ne fait que refléter l'état renvoyé par `/me`.
 */
export function ConsoleAuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<Status>('loading');
  const [admin, setAdmin] = useState<PlatformAdmin | null>(null);

  const reset = useCallback(() => {
    queryClient.clear();
    setAdmin(null);
    setStatus('anonymous');
  }, [queryClient]);

  useEffect(() => {
    setConsoleUnauthenticatedHandler(reset);
    let cancelled = false;
    consoleRequest<PlatformAdmin>('/me', { silent401: true })
      .then((me) => {
        if (cancelled) return;
        setAdmin(me);
        setStatus('authenticated');
      })
      .catch(() => !cancelled && setStatus('anonymous'));
    return () => {
      cancelled = true;
      setConsoleUnauthenticatedHandler(null);
    };
  }, [reset]);

  const value = useMemo<ConsoleAuthValue>(
    () => ({
      status,
      admin,
      async login(email, password) {
        const me = await consoleRequest<PlatformAdmin>('/auth/login', {
          method: 'POST',
          body: { email, password },
          silent401: true,
        });
        queryClient.clear();
        setAdmin(me);
        setStatus('authenticated');
      },
      async logout() {
        try {
          await consoleRequest('/auth/logout', { method: 'POST', silent401: true });
        } finally {
          reset();
        }
      },
    }),
    [status, admin, queryClient, reset],
  );

  return <ConsoleAuthContext.Provider value={value}>{children}</ConsoleAuthContext.Provider>;
}
