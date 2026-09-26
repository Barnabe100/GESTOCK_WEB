import { Card } from 'primereact/card';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatDate } from '@/shared/lib/format';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { LoadingState } from '@/shared/ui/LoadingState';
import { PageHeader } from '@/shared/ui/PageHeader';
import { LicenseStateBadge, SubscriptionStatusBadge, StatusBadge } from '@/shared/ui/StatusBadge';

import { useSubscriptions, type LicenseSummary, type SubscriptionDetails } from './api';
import { SubscriptionPaymentsSection } from './PaymentsSection';

/** Licence du site (lecture seule) : numéro, état, validité, postes autorisés. Les jours de
 * validité sont des dates du fuseau de l'entreprise, affichées telles quelles. */
function SiteLicense({ license, locale }: { license: LicenseSummary | null; locale: string }) {
  const { t } = useTranslation();
  if (!license) {
    return (
      <p className="sm-muted" data-testid="license-none">
        {t('subscriptionPage.noLicense')}
      </p>
    );
  }
  const day = (value: string) => formatDate(value, locale, 'UTC');
  return (
    <div data-testid="license">
      <p className="sm-strong">
        {t('subscriptionPage.licenseNumber', { number: license.license_number })}{' '}
        <LicenseStateBadge state={license.state} />
      </p>
      <p>
        {t('subscriptionPage.licenseValidity', {
          start: day(license.valid_from),
          end: day(license.valid_until),
        })}
      </p>
      <p data-testid="license-activations">
        {t('subscriptionPage.licenseActivations', { count: license.max_activations })}
      </p>
    </div>
  );
}

/** Abonnement d'un site : offre, statut, période, licence, utilisation et accès autorisés. */
function SiteSubscription({ subscription: s }: { subscription: SubscriptionDetails }) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const locale = capabilities.tenant.locale;
  const code = s.site?.code ?? 'pending';
  return (
    <Card
      title={s.site ? s.site.name : t('subscriptionPage.unattached')}
      className="sm-block"
      data-testid={`subscription-${code}`}
    >
      {!s.site && <p className="sm-help">{t('subscriptionPage.unattachedHelp')}</p>}
      <div className="sm-grid">
        <div>
          <h3>{t('subscriptionPage.plan')}</h3>
          <p className="sm-strong">{s.plan_name}</p>
          <p>{t(`billingPeriod.${s.billing_period}`)}</p>
          <SubscriptionStatusBadge status={s.effective_status} />
        </div>
        <div>
          <h3>{t('subscriptionPage.period')}</h3>
          <p>
            {t('subscriptionPage.periodValue', {
              start: formatDate(s.current_period_start, locale),
              end: formatDate(s.current_period_end, locale),
            })}
          </p>
          <p className="sm-muted">{t('subscriptionPage.grace', { count: s.grace_days })}</p>
          <p className="sm-muted">
            {t('subscriptionPage.requestedActivations', { count: s.requested_activations })}
          </p>
        </div>
        <div>
          <h3>{t('subscriptionPage.license')}</h3>
          <SiteLicense license={s.license} locale={locale} />
        </div>
        <div>
          <h3>{t('subscriptionPage.usage')}</h3>
          {Object.entries(s.limits).map(([limit, usage]) => (
            <p key={limit}>
              {t(`subscriptionPage.limitNames.${limit}`, limit)} : {usage.used} /{' '}
              {usage.limit ?? t('subscriptionPage.unlimited')}
            </p>
          ))}
        </div>
      </div>
      <div className="sm-tags" aria-label={t('subscriptionPage.allowed')}>
        {s.allowed_access.map((access) => (
          <StatusBadge key={access} tone="info" label={t(`access.${access}`)} />
        ))}
      </div>
    </Card>
  );
}

/**
 * Abonnements de l'entreprise : **un par site** (1 site = 1 abonnement, ADR-0033), chacun avec
 * son offre, son statut et sa période ; paiements déclarés à TechNova.
 */
export default function SubscriptionPage() {
  const { t } = useTranslation();
  const subscriptions = useSubscriptions();

  if (subscriptions.isPending) return <LoadingState />;
  if (subscriptions.isError) {
    return (
      <ErrorMessage error={subscriptions.error} onRetry={() => void subscriptions.refetch()} />
    );
  }
  const list = subscriptions.data;
  return (
    <>
      <PageHeader
        title={t('subscriptionPage.title')}
        description={t('subscriptionPage.subtitle')}
      />
      {list.map((s) => (
        <SiteSubscription key={s.id} subscription={s} />
      ))}
      <p className="sm-muted">{t('subscriptionPage.renewInfo')}</p>
      {list.length > 0 && <SubscriptionPaymentsSection subscriptions={list} />}
    </>
  );
}
