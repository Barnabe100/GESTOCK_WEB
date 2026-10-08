import { Column } from 'primereact/column';
import { DataTable } from 'primereact/datatable';
import { Dropdown } from 'primereact/dropdown';
import { InputSwitch } from 'primereact/inputswitch';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { profileLabel } from '@/core/capabilities/profile';
import { translateError } from '@/shared/lib/errors';
import { EmptyState } from '@/shared/ui/EmptyState';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { PageHeader } from '@/shared/ui/PageHeader';
import { StatusBadge } from '@/shared/ui/StatusBadge';
import { useToast } from '@/shared/ui/toast';

import { useSiteModules, useToggleSiteModule, type SiteModule } from './api';

/**
 * Modules d'UN site (palier C) : proposés par le profil DU SITE, inclus dans l'abonnement DU
 * SITE, activés sur CE site. Activer un module sur un site ne modifie jamais un autre site ; le
 * serveur revérifie permission, site, profil, abonnement et dépendances.
 */
export default function ModulesPage() {
  const { t } = useTranslation();
  const toast = useToast();
  const { can, capabilities, siteId: selectedSite } = useCapabilities();
  // Site sélectionné dans l'en-tête, sinon le site principal calculé par le serveur.
  const [chosenSite, setChosenSite] = useState<string | null>(
    selectedSite ?? capabilities.main_site_id ?? capabilities.sites[0]?.id ?? null,
  );
  const siteId = selectedSite ?? chosenSite;
  const site = capabilities.sites.find((s) => s.id === siteId);
  const modules = useSiteModules(siteId);
  const toggle = useToggleSiteModule();
  const canManage = can('organization.module.manage');
  const rows = (modules.data ?? []).filter((m) => !m.core);

  const onToggle = (module: SiteModule, enabled: boolean) => {
    if (!siteId) return;
    toggle.mutate(
      { siteId, code: module.code, enabled },
      {
        onSuccess: () => toast.success(t('moduleAdmin.saved')),
        onError: (error) => toast.error(translateError(t, error)),
      },
    );
  };

  return (
    <>
      <PageHeader title={t('moduleAdmin.title')} description={t('moduleAdmin.intro')} />
      {!siteId ? (
        <EmptyState icon="pi pi-building" title={t('moduleAdmin.noSite')} />
      ) : (
        <>
          <div className="sm-form-grid">
            {capabilities.sites.length > 1 && (
              <Dropdown
                value={siteId}
                onChange={(e) => setChosenSite((e.value as string | undefined) ?? null)}
                options={capabilities.sites.map((s) => ({ value: s.id, label: s.name }))}
                disabled={selectedSite !== null}
                aria-label={t('layout.site')}
              />
            )}
            {site?.profile && (
              <p data-testid="site-profile">
                {t('moduleAdmin.siteProfile', { profile: profileLabel(t, site.profile) })}
              </p>
            )}
          </div>
          {modules.isError ? (
            <ErrorMessage error={modules.error} onRetry={() => void modules.refetch()} />
          ) : (
            <DataTable value={rows} loading={modules.isPending} dataKey="code">
              <Column
                header={t('moduleAdmin.module')}
                body={(m: SiteModule) => (
                  <div>
                    <div>{t(`modules.${m.code}`)}</div>
                    {m.depends_on.length > 0 && (
                      <small className="sm-muted">
                        {t('moduleAdmin.dependsOn', {
                          modules: m.depends_on.map((d) => t(`modules.${d}`)).join(', '),
                        })}
                      </small>
                    )}
                  </div>
                )}
              />
              <Column
                header={t('moduleAdmin.status')}
                body={(m: SiteModule) => (
                  <div className="sm-tags">
                    {!m.in_plan && (
                      <StatusBadge tone="warning" label={t('moduleAdmin.notInPlan')} />
                    )}
                    {m.status === 'planned' && (
                      <StatusBadge tone="neutral" label={t('moduleAdmin.planned')} />
                    )}
                    {m.status === 'available' && m.effective && (
                      <StatusBadge tone="success" label={t('moduleAdmin.effective')} />
                    )}
                    {m.status === 'available' && m.activated_for_site && !m.effective && (
                      <StatusBadge tone="warning" label={t('moduleAdmin.notEffective')} />
                    )}
                  </div>
                )}
              />
              <Column
                header={t('moduleAdmin.enabled')}
                body={(m: SiteModule) => (
                  <InputSwitch
                    checked={m.activated_for_site && m.in_plan}
                    disabled={!canManage || !m.in_plan || toggle.isPending}
                    onChange={(e) => onToggle(m, Boolean(e.value))}
                    aria-label={t(`modules.${m.code}`)}
                  />
                )}
              />
            </DataTable>
          )}
        </>
      )}
    </>
  );
}
