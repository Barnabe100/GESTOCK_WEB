import { Button } from 'primereact/button';
import { Card } from 'primereact/card';
import { Column } from 'primereact/column';
import { SelectButton } from 'primereact/selectbutton';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatDateTime } from '@/shared/lib/format';
import { INITIAL_TABLE, toQueryString, type TableState } from '@/shared/lib/serverTable';
import { ListEmpty } from '@/shared/ui/EmptyState';
import { PageHeader } from '@/shared/ui/PageHeader';
import { ServerTable } from '@/shared/ui/ServerTable';
import { StatusBadge } from '@/shared/ui/StatusBadge';

import { useMarkNotificationsRead, useNotifications, type AppNotification } from './api';
import { notificationText, notificationTone } from './notificationDisplay';

type Filter = 'all' | 'unread';

/** Centre des notifications : historique conservé, lu / non lu propre au membre. */
export default function NotificationsPage() {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const { locale, timezone } = capabilities.tenant;
  const [table, setTable] = useState<TableState>(INITIAL_TABLE);
  const [filter, setFilter] = useState<Filter>('all');
  const notifications = useNotifications(
    toQueryString(table, { unread: filter === 'unread' ? 'true' : null }),
  );
  const mark = useMarkNotificationsRead();
  return (
    <>
      <PageHeader
        title={t('notifications.title')}
        description={t('notifications.subtitle')}
        actions={
          <Button
            icon="pi pi-check"
            label={t('notifications.markAllRead')}
            outlined
            loading={mark.isPending && mark.variables === null}
            onClick={() => mark.mutate(null)}
          />
        }
      />
      <Card>
        <SelectButton
          value={filter}
          allowEmpty={false}
          aria-label={t('notifications.filter')}
          options={[
            { value: 'all', label: t('notifications.all') },
            { value: 'unread', label: t('notifications.unreadOnly') },
          ]}
          onChange={(e) => {
            setFilter(e.value as Filter);
            setTable((s) => ({ ...s, first: 0 }));
          }}
        />
        <ServerTable
          query={notifications}
          table={table}
          onTableChange={setTable}
          minWidth="40rem"
          empty={
            <ListEmpty
              filtered={filter === 'unread'}
              icon="pi pi-bell"
              title={t('notifications.empty')}
            />
          }
        >
          <Column
            header={t('notifications.date')}
            bodyClassName="sm-nowrap"
            body={(n: AppNotification) => formatDateTime(n.created_at, locale, timezone)}
          />
          <Column
            header={t('notifications.message')}
            body={(n: AppNotification) => (
              <div data-testid="notification">
                <StatusBadge
                  tone={notificationTone(n.step)}
                  label={t('notifications.step', { step: n.step })}
                />{' '}
                <span className={n.read_at ? undefined : 'sm-strong'}>
                  {notificationText(t, n, locale)}
                </span>
              </div>
            )}
          />
          <Column
            header={t('notifications.state')}
            body={(n: AppNotification) =>
              n.read_at ? (
                <span className="sm-muted">{t('notifications.read')}</span>
              ) : (
                <Button
                  text
                  size="small"
                  label={t('notifications.markRead')}
                  onClick={() => mark.mutate(n.id)}
                />
              )
            }
          />
        </ServerTable>
      </Card>
      <p className="sm-muted">
        <Link to="/subscription">{t('notifications.toSubscription')}</Link>
      </p>
    </>
  );
}
