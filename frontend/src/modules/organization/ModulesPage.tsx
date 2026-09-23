import { Column } from 'primereact/column';
import { DataTable } from 'primereact/datatable';
import { InputSwitch } from 'primereact/inputswitch';
import { Tag } from 'primereact/tag';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { translateError } from '@/shared/lib/errors';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { PageHeader } from '@/shared/ui/PageHeader';
import { useToast } from '@/shared/ui/toast';

import { useModules, useToggleModule, type TenantModule } from './api';

export default function ModulesPage() {
  const { t } = useTranslation();
  const toast = useToast();
  const { can } = useCapabilities();
  const modules = useModules();
  const toggle = useToggleModule();
  const canManage = can('organization.module.manage');
  const rows = (modules.data ?? []).filter((m) => !m.core);

  const onToggle = (module: TenantModule, enabled: boolean) =>
    toggle.mutate(
      { code: module.code, enabled },
      {
        onSuccess: () => toast.success(t('moduleAdmin.saved')),
        onError: (error) => toast.error(translateError(t, error)),
      },
    );

  return (
    <>
      <PageHeader title={t('moduleAdmin.title')} />
      <p className="sm-muted">{t('moduleAdmin.intro')}</p>
      {modules.isError ? (
        <ErrorMessage error={modules.error} onRetry={() => void modules.refetch()} />
      ) : (
        <DataTable value={rows} loading={modules.isPending} dataKey="code">
          <Column
            header={t('moduleAdmin.module')}
            body={(m: TenantModule) => (
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
            body={(m: TenantModule) => (
              <div className="sm-tags">
                {!m.in_plan && <Tag severity="warning" value={t('moduleAdmin.notInPlan')} />}
                {m.status === 'planned' && (
                  <Tag severity="secondary" value={t('moduleAdmin.planned')} />
                )}
              </div>
            )}
          />
          <Column
            header={t('moduleAdmin.enabled')}
            body={(m: TenantModule) => (
              <InputSwitch
                checked={m.enabled && m.in_plan}
                disabled={!canManage || !m.in_plan || toggle.isPending}
                onChange={(e) => onToggle(m, Boolean(e.value))}
                aria-label={t(`modules.${m.code}`)}
              />
            )}
          />
        </DataTable>
      )}
    </>
  );
}
