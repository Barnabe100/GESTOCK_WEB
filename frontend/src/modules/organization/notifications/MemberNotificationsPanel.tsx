import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { SelectButton } from 'primereact/selectbutton';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatDateTime } from '@/shared/lib/format';
import { INITIAL_TABLE, toQueryString, type TableState } from '@/shared/lib/serverTable';
import { ListEmpty } from '@/shared/ui/EmptyState';
import { ServerTable } from '@/shared/ui/ServerTable';
import { StatusBadge } from '@/shared/ui/StatusBadge';

import {
  useMarkMemberNotificationsRead,
  useMemberNotifications,
  type MemberNotification,
} from './api';

type Filter = 'all' | 'unread';

/**
 * Lien vers l'entité concernée, si l'écran est accessible au membre (ergonomie ; le serveur
 * revérifie l'accès). Présentation seulement : aucune règle métier.
 */
const ENTITY_LINKS: Record<string, { permission: string; path: (id: string) => string }> = {
  restaurant_order: {
    permission: 'restaurant.orders.order.view',
    path: (id) => `/restaurant/orders/${encodeURIComponent(id)}`,
  },
};

/** Texte d'une notification : type traduit, contexte fourni par le serveur. */
export function memberNotificationText(
  t: (key: string, options?: Record<string, unknown>) => string,
  n: MemberNotification,
): string {
  return t(`memberNotifications.kinds.${n.kind}`, {
    number: n.payload.daily_number,
    count: n.payload.line_count ?? 0,
    site: n.site.name,
    defaultValue: t('memberNotifications.unknownKind', { site: n.site.name }),
  });
}

/** Mes notifications (ADR-0049 D8) : lu / non lu, « traitée » calculée par le serveur. */
export default function MemberNotificationsPanel() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { capabilities, can } = useCapabilities();
  const { locale, timezone } = capabilities.tenant;
  const [table, setTable] = useState<TableState>(INITIAL_TABLE);
  const [filter, setFilter] = useState<Filter>('all');
  const notifications = useMemberNotifications(
    toQueryString(table, { unread: filter === 'unread' ? 'true' : null }),
  );
  const mark = useMarkMemberNotificationsRead();
  const open = (n: MemberNotification) => {
    const link = ENTITY_LINKS[n.entity_type];
    if (!link) return;
    if (!n.read_at) mark.mutate(n.id);
    void navigate(link.path(n.entity_id));
  };
  return (
    <>
      <div className="sm-toolbar">
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
        <Button
          icon="pi pi-check"
          label={t('notifications.markAllRead')}
          outlined
          loading={mark.isPending && mark.variables === null}
          onClick={() => mark.mutate(null)}
        />
      </div>
      <ServerTable
        query={notifications}
        table={table}
        onTableChange={setTable}
        minWidth="40rem"
        empty={
          <ListEmpty
            filtered={filter === 'unread'}
            icon="pi pi-bell"
            title={t('memberNotifications.empty')}
          />
        }
      >
        <Column
          header={t('notifications.date')}
          bodyClassName="sm-nowrap"
          body={(n: MemberNotification) => formatDateTime(n.created_at, locale, timezone)}
        />
        <Column
          header={t('notifications.message')}
          body={(n: MemberNotification) => (
            <div data-testid="member-notification">
              {n.resolved !== null && (
                <>
                  <StatusBadge
                    tone={n.resolved ? 'success' : 'warning'}
                    label={t(
                      n.resolved ? 'memberNotifications.resolved' : 'memberNotifications.pending',
                    )}
                  />{' '}
                </>
              )}
              <span className={n.read_at ? undefined : 'sm-strong'}>
                {memberNotificationText(t, n)}
              </span>
            </div>
          )}
        />
        <Column
          header={t('notifications.state')}
          body={(n: MemberNotification) =>
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
        <Column
          body={(n: MemberNotification) => {
            const link = ENTITY_LINKS[n.entity_type];
            return link && can(link.permission) ? (
              <Button
                text
                size="small"
                icon="pi pi-arrow-right"
                label={t('memberNotifications.open')}
                onClick={() => open(n)}
              />
            ) : null;
          }}
        />
      </ServerTable>
    </>
  );
}
