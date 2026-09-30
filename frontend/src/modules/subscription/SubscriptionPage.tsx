import { Button } from 'primereact/button';
import { Card } from 'primereact/card';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatMoney } from '@/shared/lib/decimal';
import { formatDate } from '@/shared/lib/format';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { LoadingState } from '@/shared/ui/LoadingState';
import { PageHeader } from '@/shared/ui/PageHeader';
import { LicenseStateBadge, SubscriptionStatusBadge, StatusBadge } from '@/shared/ui/StatusBadge';

import { useSubscriptions, type LicenseSummary, type SubscriptionDetails } from './api';
import { ExpiryReminders } from './notificationDisplay';
import { DeclarePaymentDialog, SubscriptionPaymentsSection } from './PaymentsSection';
import { SitePostes } from './SitePostes';

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
    </div>
  );
}

/**
 * Prochaine période du site (calculée par le serveur) et action « Renouveler ». Les droits
 * restent ceux de la licence en vigueur jusqu'à la licence suivante (R4).
 */
function NextPeriod({
  subscription: s,
  onRenew,
}: {
  subscription: SubscriptionDetails;
  onRenew: () => void;
}) {
  const { t } = useTranslation();
  const { can, capabilities } = useCapabilities();
  const locale = capabilities.tenant.locale;
  const quote = s.renewal;
  const day = (value: string) => formatDate(value, locale, 'UTC');
  const canDeclare = can('subscription.payment.declare') && s.site !== null;
  const renewable = quote.kind === 'renewal' ? quote.renewal_due : s.status !== 'cancelled';
  return (
    <div className="sm-block" data-testid="next-period">
      <h3>{t('renewal.title')}</h3>
      <p>
        {t('renewal.nextValue', {
          start: day(quote.valid_from),
          end: day(quote.valid_until),
          postes: t('renewal.postesValue', { count: quote.activations }),
        })}
        {quote.amount !== null && quote.currency && (
          <> · {formatMoney(quote.amount, quote.currency, locale)}</>
        )}
      </p>
      {quote.grace_continuity && <p className="sm-help">{t('renewal.graceContinuity')}</p>}
      {canDeclare && renewable && (
        <Button
          icon="pi pi-refresh"
          label={t(quote.kind === 'renewal' ? 'renewal.renew' : 'renewal.payFirst')}
          onClick={onRenew}
          data-testid="renew"
        />
      )}
    </div>
  );
}

/** Abonnement d'un site : offre, statut, période, licence, utilisation et accès autorisés. */
function SiteSubscription({
  subscription: s,
  onRenew,
}: {
  subscription: SubscriptionDetails;
  onRenew: () => void;
}) {
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
          <h3>{t('subscriptionPage.effectivePlan')}</h3>
          <p className="sm-strong" data-testid="effective-plan">
            {s.effective_plan.name}
          </p>
          {s.next_plan && (
            <p className="sm-help" data-testid="next-plan">
              {t('subscriptionPage.nextPlan', { plan: s.next_plan.name })}
            </p>
          )}
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
      {s.site && s.license && <SitePostes siteId={s.site.id} license={s.license} locale={locale} />}
      {s.site && <NextPeriod subscription={s} onRenew={onRenew} />}
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
  const [renewing, setRenewing] = useState<string | null>(null);

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
      <ExpiryReminders />
      {list.map((s) => (
        <SiteSubscription key={s.id} subscription={s} onRenew={() => setRenewing(s.id)} />
      ))}
      <p className="sm-muted">{t('subscriptionPage.renewInfo')}</p>
      {list.length > 0 && <SubscriptionPaymentsSection subscriptions={list} />}
      {renewing && (
        <DeclarePaymentDialog
          subscriptions={list}
          initialSubscriptionId={renewing}
          onClose={() => setRenewing(null)}
        />
      )}
    </>
  );
}
