import { Button } from 'primereact/button';
import { Card } from 'primereact/card';
import { Message } from 'primereact/message';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { profileLabel, sectorLabel } from '@/core/capabilities/profile';
import { formatDate } from '@/shared/lib/format';
import { PageHeader } from '@/shared/ui/PageHeader';
import { StatusBadge, SubscriptionStatusBadge } from '@/shared/ui/StatusBadge';

import { DASHBOARD_SHORTCUTS, DASHBOARD_WIDGETS, selectDashboardItems } from './widgets';

/**
 * Tableau de bord configurable : widgets et raccourcis déclarés par le profil UX du tenant
 * (`caps.ux.dashboard`), affichés selon les permissions et fonctionnalités effectives
 * (jamais selon le plan, le rôle ou le secteur).
 */
export default function DashboardPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { can, capabilities: caps } = useCapabilities();
  const { locale } = caps.tenant;

  const widgets = selectDashboardItems(
    DASHBOARD_WIDGETS,
    caps.ux?.dashboard.widgets,
    can,
    caps.features,
  );
  const metrics = widgets.filter((w) => w.kind === 'metric');
  const panels = widgets.filter((w) => w.kind === 'panel');
  const shortcuts = selectDashboardItems(
    DASHBOARD_SHORTCUTS,
    caps.ux?.dashboard.shortcuts,
    can,
    caps.features,
  );

  // Offre : modules utilisables. À venir : modules planifiés du profil (et de l'offre),
  // annoncés comme tels — information, jamais un accès.
  const offer = caps.modules.filter((m) => !m.core && m.status === 'available');
  const upcoming = [
    ...new Set([
      ...(caps.ux?.upcoming ?? []),
      ...caps.modules.filter((m) => !m.core && m.status === 'planned').map((m) => m.code),
    ]),
  ];

  return (
    <>
      <PageHeader
        title={t('dashboard.welcome', { name: caps.user.full_name })}
        description={t('dashboard.subtitle', {
          tenant: caps.tenant.name,
          site: caps.site?.name ?? t('layout.allSites'),
        })}
      />
      {caps.restricted_permissions.length > 0 && (
        <Message severity="warn" text={t('dashboard.restricted')} className="sm-block" />
      )}
      {metrics.length > 0 && (
        <section className="sm-metrics" aria-label={t('dashboard.indicators')}>
          {metrics.map(({ id, component: Widget }) => (
            <Widget key={id} />
          ))}
        </section>
      )}
      <div className="sm-dashboard-grid">
        <div className="sm-stack">
          {panels.map(({ id, component: Widget }) => (
            <Widget key={id} />
          ))}
          <Card title={t('dashboard.offer')}>
            <ul className="sm-chips" aria-label={t('dashboard.offer')}>
              {offer.map((module) => (
                <li key={module.code}>
                  <span>{t(`modules.${module.code}`)}</span>
                </li>
              ))}
            </ul>
            {upcoming.length > 0 && (
              <div className="sm-upcoming">
                <h3 id="dashboard-upcoming">{t('dashboard.upcoming')}</h3>
                <ul className="sm-chips" aria-labelledby="dashboard-upcoming">
                  {upcoming.map((code) => (
                    <li key={code}>
                      <span>{t(`modules.${code}`)}</span>
                      <StatusBadge tone="neutral" label={t('common.comingSoon')} />
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </Card>
        </div>
        <div className="sm-stack">
          {shortcuts.length > 0 && (
            <Card title={t('dashboard.shortcuts')}>
              <nav className="sm-quick-actions" aria-label={t('dashboard.shortcuts')}>
                {shortcuts.map((s) => (
                  <Button
                    key={s.id}
                    icon={s.icon}
                    label={t(s.labelKey)}
                    outlined
                    onClick={() => void navigate(s.to)}
                  />
                ))}
              </nav>
            </Card>
          )}
          <Card title={t('dashboard.subscription')}>
            <dl className="sm-details">
              <div>
                <dt>{t('dashboard.plan')}</dt>
                <dd>
                  {caps.plan.name} · {t(`billingPeriod.${caps.subscription.billing_period}`)}
                </dd>
              </div>
              <div>
                <dt>{t('subscriptionPage.status')}</dt>
                <dd>
                  <SubscriptionStatusBadge status={caps.subscription.status} />
                </dd>
              </div>
              <div>
                <dt>{t('dashboard.periodEnd')}</dt>
                <dd>{formatDate(caps.subscription.current_period_end, locale)}</dd>
              </div>
              {caps.profile.sector && (
                <div>
                  <dt>{t('dashboard.sector')}</dt>
                  <dd>{sectorLabel(t, caps.profile.sector)}</dd>
                </div>
              )}
              <div>
                <dt>{t('dashboard.profile')}</dt>
                <dd>{profileLabel(t, caps.profile)}</dd>
              </div>
            </dl>
          </Card>
        </div>
      </div>
    </>
  );
}
