import { Card } from 'primereact/card';
import { Message } from 'primereact/message';
import { Tag } from 'primereact/tag';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatDate } from '@/shared/lib/format';
import { PageHeader } from '@/shared/ui/PageHeader';
import { SubscriptionStatusTag } from '@/shared/ui/StatusTag';

export default function DashboardPage() {
  const { t } = useTranslation();
  const { capabilities: caps } = useCapabilities();
  const offer = caps.modules.filter((m) => !m.core);

  return (
    <>
      <PageHeader title={t('dashboard.welcome', { name: caps.user.full_name })} />
      {caps.restricted_permissions.length > 0 && (
        <Message severity="warn" text={t('dashboard.restricted')} className="sm-block" />
      )}
      <div className="sm-grid">
        <Card title={t('dashboard.profile')}>
          <p className="sm-strong">{caps.profile.name}</p>
          <p className="sm-muted">{caps.tenant.name}</p>
        </Card>
        <Card title={t('dashboard.subscription')}>
          <p className="sm-strong">
            {caps.plan.name} · {t(`billingPeriod.${caps.subscription.billing_period}`)}
          </p>
          <SubscriptionStatusTag status={caps.subscription.status} />
          <p className="sm-muted">
            {t('dashboard.validUntil', {
              date: formatDate(caps.subscription.current_period_end, caps.tenant.locale),
            })}
          </p>
        </Card>
        <Card title={t('dashboard.currentSite')}>
          <p className="sm-strong">{caps.site?.name ?? t('layout.allSites')}</p>
        </Card>
      </div>
      <Card title={t('dashboard.offer')} className="sm-block">
        <ul className="sm-chips" aria-label={t('dashboard.offer')}>
          {offer.map((module) => (
            <li key={module.code}>
              <span>{t(`modules.${module.code}`)}</span>
              {module.status === 'planned' && (
                <Tag value={t('common.comingSoon')} severity="secondary" />
              )}
            </li>
          ))}
        </ul>
      </Card>
    </>
  );
}
