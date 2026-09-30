import { Button } from 'primereact/button';
import { Card } from 'primereact/card';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { translateError } from '@/shared/lib/errors';
import { confirmAction } from '@/shared/ui/confirm';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { StatusBadge } from '@/shared/ui/StatusBadge';
import { useToast } from '@/shared/ui/toast';

import { useCashSiteMutation, useCashSites, type CashSite } from './api';

/**
 * Caisse par site (optionnelle) : activée ou non, sessions ouvertes. Activation et
 * désactivation réservées à la configuration du site (`organization.site.manage`, revérifiée
 * par le serveur pour CE site) ; désactivation refusée par le serveur tant qu'une session est
 * ouverte (message traduit affiché).
 */
export function CashSitesPanel() {
  const { t } = useTranslation();
  const toast = useToast();
  const { can } = useCapabilities();
  const sites = useCashSites();
  const mutation = useCashSiteMutation();
  const canConfigure = can('organization.site.manage');

  const toggle = (site: CashSite) => {
    const run = () =>
      mutation.mutate(
        { siteId: site.site_id, enabled: !site.enabled },
        {
          onSuccess: () =>
            toast.success(
              t(site.enabled ? 'cash.siteDisabled' : 'cash.siteEnabled', { site: site.site_name }),
            ),
          onError: (error) => toast.error(translateError(t, error)),
        },
      );
    if (!site.enabled) return run();
    confirmAction(t, {
      header: t('cash.disableSiteTitle'),
      message: t('cash.disableSiteConfirm', { site: site.site_name }),
      acceptLabel: t('cash.disableSite'),
      danger: true,
      onAccept: run,
    });
  };

  if (sites.isError) {
    return <ErrorMessage error={sites.error} onRetry={() => void sites.refetch()} />;
  }
  return (
    <Card title={t('cash.sitesTitle')} className="sm-block">
      <p className="sm-help">{t('cash.sitesHelp')}</p>
      <ul className="sm-switch-list" aria-label={t('cash.sitesTitle')}>
        {(sites.data ?? []).map((site) => (
          <li key={site.site_id} className="sm-tags" data-testid={`cash-site-${site.site_id}`}>
            <strong>{site.site_name}</strong>
            <StatusBadge
              tone={site.enabled ? 'success' : 'neutral'}
              label={t(site.enabled ? 'cash.siteCashEnabled' : 'cash.siteCashDisabled')}
            />
            {site.open_sessions > 0 && (
              <StatusBadge
                tone="info"
                label={t('cash.openSessions', { count: site.open_sessions })}
              />
            )}
            {canConfigure && (
              <Button
                type="button"
                size="small"
                outlined
                severity={site.enabled ? 'danger' : undefined}
                label={t(site.enabled ? 'cash.disableSite' : 'cash.enableSite')}
                aria-label={`${t(site.enabled ? 'cash.disableSite' : 'cash.enableSite')} — ${site.site_name}`}
                loading={mutation.isPending && mutation.variables?.siteId === site.site_id}
                onClick={() => toggle(site)}
              />
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}
