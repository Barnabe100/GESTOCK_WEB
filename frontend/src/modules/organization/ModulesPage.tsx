import { Column } from 'primereact/column';
import { DataTable } from 'primereact/datatable';
import { Dropdown } from 'primereact/dropdown';
import { InputSwitch } from 'primereact/inputswitch';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { profileLabel } from '@/core/capabilities/profile';
import { translateError } from '@/shared/lib/errors';
import { EmptyState } from '@/shared/ui/EmptyState';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { PageHeader } from '@/shared/ui/PageHeader';
import { StatusBadge } from '@/shared/ui/StatusBadge';
import { useToast } from '@/shared/ui/toast';

import { useSiteModules, useTenantModules, useToggleSiteModule, type SiteModule } from './api';
import {
  canToggle,
  MODULE_STATE_ORDER,
  MODULE_STATE_TONES,
  moduleState,
  type ModuleState,
} from './moduleState';

interface Row {
  module: SiteModule;
  state: ModuleState;
}

/**
 * Modules d'UN site (paliers C et E) : proposés par le profil DU SITE, inclus dans l'abonnement
 * DU SITE, activés sur CE site. Six états lisibles, tous déduits des champs du serveur ; un
 * interrupteur n'est proposé que si l'activation est possible (jamais pour un module hors
 * abonnement, à venir ou non proposé par le profil). Le serveur revérifie tout.
 */
export default function ModulesPage() {
  const { t } = useTranslation();
  const toast = useToast();
  const { can, capabilities, siteId: selectedSite } = useCapabilities();
  const [params] = useSearchParams();
  const requested = capabilities.sites.find((s) => s.id === params.get('site'))?.id;
  // Site sélectionné dans l'en-tête, sinon celui demandé (lien de la page Sites), sinon le
  // site principal calculé par le serveur.
  const [chosenSite, setChosenSite] = useState<string | null>(
    requested ?? capabilities.main_site_id ?? capabilities.sites[0]?.id ?? null,
  );
  const siteId = selectedSite ?? chosenSite;
  const site = capabilities.sites.find((s) => s.id === siteId);
  const modules = useSiteModules(siteId);
  // Synthèse de l'entreprise : modules proposés par le profil d'un AUTRE site seulement.
  const summary = useTenantModules(siteId !== null);
  const toggle = useToggleSiteModule();
  const canManage = can('organization.module.manage');

  const siteRows: Row[] = (modules.data ?? [])
    .filter((m) => !m.core)
    .map((module) => ({ module, state: moduleState(module) }));
  const listed = new Set(siteRows.map((r) => r.module.code));
  const elsewhere: Row[] = modules.data
    ? (summary.data ?? [])
        .filter((m) => !m.core && !listed.has(m.code))
        .map((m) => ({
          module: {
            code: m.code,
            status: m.status,
            core: false,
            depends_on: m.depends_on,
            in_profile: false,
            in_plan: m.in_plan,
            activated_for_site: false,
            effective: false,
          },
          state: 'notInProfile' as const,
        }))
    : [];
  const rows = [...siteRows, ...elsewhere].sort(
    (a, b) => MODULE_STATE_ORDER.indexOf(a.state) - MODULE_STATE_ORDER.indexOf(b.state),
  );

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
            <p data-testid="site-profile">
              {t('moduleAdmin.siteProfile', {
                site: site?.name ?? '',
                profile: site?.profile ? profileLabel(t, site.profile) : t('layout.unknownProfile'),
              })}
            </p>
          </div>
          {modules.isError ? (
            <ErrorMessage error={modules.error} onRetry={() => void modules.refetch()} />
          ) : (
            <DataTable
              value={rows}
              loading={modules.isPending}
              dataKey="module.code"
              className="sm-table"
            >
              <Column
                header={t('moduleAdmin.module')}
                body={({ module: m }: Row) => (
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
                body={({ state }: Row) => (
                  <div data-testid={`module-state-${state}`}>
                    <StatusBadge
                      tone={MODULE_STATE_TONES[state]}
                      label={t(`moduleAdmin.states.${state}`)}
                    />
                    <small className="sm-muted sm-module-state-help">
                      {t(`moduleAdmin.stateHelp.${state}`)}
                    </small>
                  </div>
                )}
              />
              <Column
                header={t('moduleAdmin.enabled')}
                body={({ module: m, state }: Row) =>
                  canToggle(state) ? (
                    <InputSwitch
                      checked={m.activated_for_site}
                      disabled={!canManage || toggle.isPending}
                      onChange={(e) => onToggle(m, Boolean(e.value))}
                      aria-label={t(`modules.${m.code}`)}
                    />
                  ) : (
                    <span className="sm-muted">—</span>
                  )
                }
              />
            </DataTable>
          )}
        </>
      )}
    </>
  );
}
