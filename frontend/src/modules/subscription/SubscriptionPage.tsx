import { Card } from 'primereact/card';
import { Tag } from 'primereact/tag';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatDate } from '@/shared/lib/format';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { PageHeader } from '@/shared/ui/PageHeader';
import { SubscriptionStatusTag } from '@/shared/ui/StatusTag';

import { useSubscription } from './api';

export default function SubscriptionPage() {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const subscription = useSubscription();
  const locale = capabilities.tenant.locale;

  if (subscription.isError) {
    return <ErrorMessage error={subscription.error} onRetry={() => void subscription.refetch()} />;
  }
  const s = subscription.data;
  if (!s) return null;

  return (
    <>
      <PageHeader title={t('subscriptionPage.title')} />
      <div className="sm-grid">
        <Card title={t('subscriptionPage.plan')}>
          <p className="sm-strong">{s.plan_name}</p>
          <p>{t(`billingPeriod.${s.billing_period}`)}</p>
          <SubscriptionStatusTag status={s.effective_status} />
        </Card>
        <Card title={t('subscriptionPage.period')}>
          <p>
            {t('subscriptionPage.periodValue', {
              start: formatDate(s.current_period_start, locale),
              end: formatDate(s.current_period_end, locale),
            })}
          </p>
          <p className="sm-muted">{t('subscriptionPage.grace', { count: s.grace_days })}</p>
        </Card>
        <Card title={t('subscriptionPage.usage')}>
          {Object.entries(s.limits).map(([code, usage]) => (
            <p key={code}>
              {t(`subscriptionPage.limitNames.${code}`, code)} : {usage.used} /{' '}
              {usage.limit ?? t('subscriptionPage.unlimited')}
            </p>
          ))}
        </Card>
      </div>
      <Card title={t('subscriptionPage.allowed')} className="sm-block">
        <div className="sm-tags">
          {s.allowed_access.map((access) => (
            <Tag key={access} value={t(`access.${access}`)} />
          ))}
        </div>
        <p className="sm-muted">{t('subscriptionPage.renewInfo')}</p>
      </Card>
    </>
  );
}
