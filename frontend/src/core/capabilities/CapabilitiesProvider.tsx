import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ProgressSpinner } from 'primereact/progressspinner';
import { useCallback, useMemo, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { api, ApiError, apiSession } from '@/core/api/client';
import type { Capabilities } from '@/core/api/types';
import { tabStorage } from '@/core/auth/storage';
import { applyTerminology } from '@/core/i18n/terminology';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';

import { CapabilitiesContext, type CapabilitiesContextValue } from './CapabilitiesContext';

export const capabilitiesKey = (tenantId: string, siteId: string | null) =>
  ['capabilities', tenantId, siteId ?? 'all'] as const;

export function CapabilitiesProvider({
  tenantId,
  children,
}: {
  tenantId: string;
  children: ReactNode;
}) {
  const { i18n } = useTranslation();
  const queryClient = useQueryClient();
  const [siteId, setSiteIdState] = useState<string | null>(() => tabStorage.getSiteId(tenantId));

  const query = useQuery({
    queryKey: capabilitiesKey(tenantId, siteId),
    queryFn: async ({ signal }) => {
      let capabilities: Capabilities;
      try {
        capabilities = await api.get<Capabilities>('/me/capabilities', signal);
      } catch (error) {
        // Site mémorisé devenu inaccessible : repli sur « tous les sites ».
        if (!(siteId && error instanceof ApiError && error.code === 'site_access_denied')) {
          throw error;
        }
        tabStorage.setSiteId(tenantId, null);
        apiSession.setSiteId(null);
        capabilities = await api.get<Capabilities>('/me/capabilities', signal);
      }
      // Terminologie du profil appliquée avant tout rendu qui l'utilise.
      applyTerminology(i18n, capabilities.terminology);
      return capabilities;
    },
    staleTime: 60_000,
    retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
  });

  const setSiteId = useCallback(
    (next: string | null) => {
      tabStorage.setSiteId(tenantId, next);
      apiSession.setSiteId(next);
      setSiteIdState(next);
      // Les données dépendent du site : tout est rechargé.
      void queryClient.invalidateQueries();
    },
    [queryClient, tenantId],
  );

  const capabilities = query.data;

  const value = useMemo<CapabilitiesContextValue | null>(() => {
    if (!capabilities) return null;
    const permissions = new Set(capabilities.permissions);
    const restricted = new Set(capabilities.restricted_permissions);
    const modules = new Set(capabilities.modules.map((m) => m.code));
    return {
      capabilities,
      can: (permission) => permissions.has(permission),
      isRestricted: (permission) => restricted.has(permission),
      hasModule: (code) => modules.has(code),
      // Site effectivement appliqué par le backend (source de vérité).
      siteId: capabilities.site?.id ?? null,
      setSiteId,
    };
  }, [capabilities, setSiteId]);

  if (query.isPending) {
    return (
      <div className="sm-center">
        <ProgressSpinner />
      </div>
    );
  }
  if (!value) {
    return (
      <div className="sm-center">
        <ErrorMessage error={query.error} onRetry={() => void query.refetch()} />
      </div>
    );
  }
  return <CapabilitiesContext.Provider value={value}>{children}</CapabilitiesContext.Provider>;
}
