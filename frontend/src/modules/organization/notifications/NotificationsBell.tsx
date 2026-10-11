import { useQueries } from '@tanstack/react-query';
import { Badge } from 'primereact/badge';
import { Button } from 'primereact/button';
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import { api } from '@/core/api/client';
import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { buildNotificationSources } from '@/core/modules/registry';
import type { FrontendModule, NotificationSource } from '@/core/modules/types';

/**
 * Indicateur UNIQUE de la barre supérieure : somme des non lues de chaque source autorisée
 * (rappels d'échéance, notifications des membres…), chacune actualisée à son rythme ; lien
 * vers le centre `/notifications`. Aucune source autorisée : rien n'est affiché ni demandé.
 */
export function NotificationsBell({ modules }: { modules: readonly FrontendModule[] }) {
  const { capabilities, can } = useCapabilities();
  const sources = useMemo(
    () => buildNotificationSources(modules, capabilities, can),
    [modules, capabilities, can],
  );
  return sources.length === 0 ? null : <Bell sources={sources} />;
}

function Bell({ sources }: { sources: NotificationSource[] }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const counts = useQueries({
    queries: sources.map((source) => ({
      queryKey: [...source.queryKey, 'unread'],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        api.get<{ unread: number }>(source.unreadPath, signal),
      refetchInterval: source.refetchInterval,
    })),
  });
  const count = counts.reduce((sum, query) => sum + (query.data?.unread ?? 0), 0);
  const label = t('notifications.bell', { count });
  return (
    <Button
      text
      rounded
      icon="pi pi-bell"
      className="sm-notifications-bell p-overlay-badge"
      aria-label={label}
      tooltip={label}
      tooltipOptions={{ position: 'bottom' }}
      data-testid="notifications-bell"
      onClick={() => navigate('/notifications')}
    >
      {count > 0 && (
        <Badge value={count > 99 ? '99+' : count} severity="danger" data-testid="unread-count" />
      )}
    </Button>
  );
}
