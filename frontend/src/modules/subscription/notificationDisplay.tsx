import { Badge } from 'primereact/badge';
import { Button } from 'primereact/button';
import { Message } from 'primereact/message';
import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatDate } from '@/shared/lib/format';

import {
  useMarkNotificationsRead,
  useNotifications,
  useUnreadNotifications,
  type AppNotification,
} from './api';

const VIEW = 'subscription.subscription.view';

/** Lendemain d'un jour ``AAAA-MM-JJ`` (premier jour non couvert), sans conversion de fuseau. */
function nextDay(value: string): string {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + 1);
  return date.toISOString().slice(0, 10);
}

/**
 * Texte d'un rappel d'échéance : J-n « expire dans n jours », J0 « expire aujourd'hui »,
 * J+n « n'est plus couvert depuis le … ». L'étape et l'échéance viennent du serveur.
 */
export function notificationText(t: TFunction, n: AppNotification, locale: string): string {
  const site = n.site?.name ?? t('notifications.company');
  const day = (value: string) => formatDate(value, locale, 'UTC');
  const scope = n.data.trial ? 'trial' : 'license';
  if (n.step > 0) {
    return t(`notifications.${scope}.before`, {
      site,
      count: n.step,
      date: day(n.reference_date),
    });
  }
  if (n.step === 0) return t(`notifications.${scope}.today`, { site });
  return t(`notifications.${scope}.after`, { site, date: day(nextDay(n.reference_date)) });
}

export function notificationTone(step: number): 'info' | 'warning' | 'danger' {
  if (step < 0) return 'danger';
  return step <= 5 ? 'warning' : 'info';
}

/** Indicateur de la barre supérieure : nombre de rappels non lus, lien vers le centre. */
export function NotificationsBell() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { can } = useCapabilities();
  const unread = useUnreadNotifications(can(VIEW));
  const count = unread.data?.unread ?? 0;
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

/** Rappels non lus en tête de la page Abonnement (les sites visibles du membre). */
export function ExpiryReminders() {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const locale = capabilities.tenant.locale;
  const unread = useNotifications('unread=true&limit=5');
  const mark = useMarkNotificationsRead();
  const items = unread.data?.items ?? [];
  if (items.length === 0) return null;
  return (
    <section className="sm-block" aria-label={t('notifications.remindersTitle')}>
      {items.map((n) => (
        <Message
          key={n.id}
          severity={n.step < 0 ? 'error' : 'warn'}
          className="sm-block"
          data-testid="expiry-reminder"
          content={
            <div className="sm-section-header">
              <span>{notificationText(t, n, locale)}</span>
              <Button
                text
                size="small"
                label={t('notifications.markRead')}
                loading={mark.isPending && mark.variables === n.id}
                onClick={() => mark.mutate(n.id)}
              />
            </div>
          }
        />
      ))}
      <Link to="/notifications">{t('notifications.history')}</Link>
    </section>
  );
}
