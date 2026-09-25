import { Button } from 'primereact/button';
import { SelectButton } from 'primereact/selectbutton';
import { useTranslation } from 'react-i18next';

import { formatMoney } from '@/shared/lib/decimal';
import { EmptyState } from '@/shared/ui/EmptyState';
import { StatusBadge } from '@/shared/ui/StatusBadge';

import type { PublicPlan } from './api';

type Period = 'monthly' | 'annual';

/** Prix d'une période tel que publié par TechNova (jamais calculé ni inventé ici). */
export function PeriodPrice({ plan, period }: { plan: PublicPlan; period: Period }) {
  const { t } = useTranslation();
  const entry = plan.periods.find((p) => p.billing_period === period);
  if (!entry || entry.price === null || !plan.currency) return <>{t('signup.priceOnRequest')}</>;
  return (
    <>{t(`signup.pricePer.${period}`, { price: formatMoney(entry.price, plan.currency, 'fr') })}</>
  );
}

/**
 * Offres publiées : prix affichés seulement si TechNova les publie ; offres sur contact
 * commercial non souscriptibles ici (« Contacter TechNova »).
 */
export function PlanChoice({
  plans,
  contactEmail,
  value,
  period,
  onChange,
}: {
  plans: PublicPlan[];
  contactEmail: string | null;
  value: string;
  period: Period;
  onChange: (plan: string, period: Period) => void;
}) {
  const { t } = useTranslation();
  if (plans.length === 0) {
    return (
      <EmptyState
        icon="pi pi-inbox"
        title={t('signup.noPlan')}
        description={contactEmail ? t('signup.noPlanContact', { email: contactEmail }) : undefined}
      />
    );
  }
  return (
    <div className="sm-plan-grid" role="radiogroup" aria-label={t('signup.steps.plan')}>
      {plans.map((plan) => {
        const selected = value === plan.code;
        const periods = plan.periods.map((p) => p.billing_period);
        return (
          <section
            key={plan.code}
            className={`sm-plan-card${selected ? ' sm-plan-card--selected' : ''}`}
            aria-labelledby={`plan-${plan.code}`}
          >
            <header className="sm-plan-card-header">
              <h3 id={`plan-${plan.code}`}>{plan.name}</h3>
              {plan.trial_days > 0 && (
                <StatusBadge tone="info" label={t('signup.trial', { count: plan.trial_days })} />
              )}
            </header>
            {plan.description && <p className="sm-muted">{plan.description}</p>}
            <ul className="sm-plan-prices">
              {periods.length === 0 ? (
                <li>{t('signup.priceOnRequest')}</li>
              ) : (
                periods.map((p) => (
                  <li key={p}>
                    <PeriodPrice plan={plan} period={p} />
                  </li>
                ))
              )}
            </ul>
            <ul className="sm-plan-features">
              {Object.entries(plan.limits).map(([code, limit]) => (
                <li key={code}>
                  {limit === null
                    ? t(`signup.limits.${code}Unlimited`)
                    : t(`signup.limits.${code}`, { count: limit })}
                </li>
              ))}
            </ul>
            {plan.self_service ? (
              <div className="sm-plan-actions">
                {periods.length > 1 && (
                  <SelectButton
                    value={selected ? period : periods[0]}
                    options={periods.map((p) => ({ label: t(`billingPeriod.${p}`), value: p }))}
                    allowEmpty={false}
                    aria-label={t('signup.period', { plan: plan.name })}
                    onChange={(e) => onChange(plan.code, e.value as Period)}
                  />
                )}
                <Button
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  icon={selected ? 'pi pi-check' : undefined}
                  label={selected ? t('signup.planSelected') : t('signup.choosePlan')}
                  outlined={!selected}
                  onClick={() => onChange(plan.code, selected ? period : (periods[0] as Period))}
                />
              </div>
            ) : (
              <div className="sm-plan-actions">
                <p className="sm-muted">{t('signup.contactRequired')}</p>
                {contactEmail && (
                  <a
                    className="p-button p-button-outlined"
                    href={`mailto:${contactEmail}?subject=${encodeURIComponent(plan.name)}`}
                  >
                    {t('signup.contactTechnova')}
                  </a>
                )}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
