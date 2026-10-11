import { Card } from 'primereact/card';
import { Suspense, useMemo } from 'react';
import { useTranslation } from 'react-i18next';

import { FRONTEND_MODULES } from '@/app/modules';
import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { buildNotificationSources } from '@/core/modules/registry';
import type { FrontendModule } from '@/core/modules/types';
import { EmptyState } from '@/shared/ui/EmptyState';
import { PageHeader } from '@/shared/ui/PageHeader';
import { LoadingState } from '@/shared/ui/LoadingState';

/**
 * Centre de notifications UNIQUE : une section par source autorisée (notifications des
 * membres, rappels d'échéance…), chacune avec son historique et son « lu / non lu ».
 */
export default function NotificationsCenterPage({
  modules = FRONTEND_MODULES,
}: {
  modules?: readonly FrontendModule[];
}) {
  const { t } = useTranslation();
  const { capabilities, can } = useCapabilities();
  const sources = useMemo(
    () => buildNotificationSources(modules, capabilities, can),
    [modules, capabilities, can],
  );
  return (
    <>
      <PageHeader
        title={t('notifications.title')}
        description={t('notifications.centerSubtitle')}
      />
      {sources.length === 0 && <EmptyState icon="pi pi-bell" title={t('notifications.empty')} />}
      {sources.map(({ key, labelKey, panel: Panel }) => (
        <section key={key} className="sm-block" data-testid={`notifications-${key}`}>
          <Card title={t(labelKey)}>
            <Suspense fallback={<LoadingState />}>
              <Panel />
            </Suspense>
          </Card>
        </section>
      ))}
    </>
  );
}
