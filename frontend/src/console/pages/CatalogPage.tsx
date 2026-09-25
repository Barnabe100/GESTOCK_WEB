import { Column } from 'primereact/column';
import { DataTable } from 'primereact/datatable';
import { TabPanel, TabView } from 'primereact/tabview';
import { useTranslation } from 'react-i18next';

import { ActiveBadge, StatusBadge } from '@/shared/ui/StatusBadge';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { LoadingState } from '@/shared/ui/LoadingState';
import { PageHeader } from '@/shared/ui/PageHeader';

import { useCatalog } from '../queries';
import type { Catalog } from '../types';

type Module = Catalog['modules'][number];
type Profile = Catalog['profiles'][number];
type RoleTemplate = Catalog['role_templates'][number];
type Policy = Catalog['policies'][number];

const list = (values: string[]) => (values.length ? values.join(', ') : '—');

/** Catalogue technique : consultation seulement (aucune action, aucun formulaire). */
export function CatalogPage() {
  const { t } = useTranslation();
  const catalog = useCatalog();
  if (catalog.isLoading) return <LoadingState />;
  if (catalog.isError || !catalog.data) {
    return <ErrorMessage error={catalog.error} onRetry={() => void catalog.refetch()} />;
  }
  const c = catalog.data;
  const sectors = new Map(c.sectors.map((s) => [s.code, s.name]));
  return (
    <>
      <PageHeader
        title={t('console:catalog.title')}
        description={t('console:catalog.subtitle')}
        actions={
          <StatusBadge tone="neutral" icon="pi pi-lock" label={t('console:plan.readOnly')} />
        }
      />
      <TabView>
        <TabPanel header={`${t('console:catalog.modules')} (${c.modules.length})`}>
          <DataTable
            className="sm-table"
            tableStyle={{ minWidth: '56rem' }}
            value={c.modules}
            dataKey="code"
          >
            <Column
              header={t('console:catalog.code')}
              body={(m: Module) => (
                <span>
                  <span className="sm-strong">
                    {t(`modules.${m.code}`, { defaultValue: m.code })}
                  </span>
                  <br />
                  <small className="sm-muted sm-code">{m.code}</small>
                </span>
              )}
            />
            <Column
              header={t('console:catalog.status')}
              body={(m: Module) => (
                <span className="sm-tags">
                  <StatusBadge
                    tone={m.status === 'available' ? 'success' : 'neutral'}
                    label={t(`console:catalog.${m.status}`)}
                  />
                  {m.core && <StatusBadge tone="info" label={t('console:catalog.core')} />}
                </span>
              )}
            />
            <Column
              header={t('console:catalog.dependsOn')}
              body={(m: Module) => list(m.depends_on)}
            />
            <Column header={t('console:catalog.features')} body={(m: Module) => list(m.features)} />
            <Column header={t('console:catalog.limits')} body={(m: Module) => list(m.limits)} />
            <Column
              header={t('console:catalog.permissions')}
              body={(m: Module) =>
                m.permissions.length ? (
                  <details>
                    <summary>{m.permissions.length}</summary>
                    <ul className="sm-plain-list">
                      {m.permissions.map((p) => (
                        <li key={p.code}>
                          <span className="sm-code">{p.code}</span>
                          <small className="sm-muted">
                            {t(`console:accessKind.${p.access}`)}
                            {p.feature ? ` · ${p.feature}` : ''}
                          </small>
                        </li>
                      ))}
                    </ul>
                  </details>
                ) : (
                  '—'
                )
              }
            />
          </DataTable>
        </TabPanel>
        <TabPanel header={`${t('console:catalog.profiles')} (${c.profiles.length})`}>
          <DataTable
            className="sm-table"
            tableStyle={{ minWidth: '48rem' }}
            value={c.profiles}
            dataKey="code"
          >
            <Column field="name" header={t('console:catalog.name')} />
            <Column field="code" header={t('console:catalog.code')} bodyClassName="sm-code" />
            <Column
              header={t('console:catalog.sector')}
              body={(p: Profile) =>
                p.sector_code ? (sectors.get(p.sector_code) ?? p.sector_code) : '—'
              }
            />
            <Column
              header={t('console:catalog.uxProfile')}
              body={(p: Profile) => p.ux_profile_code ?? '—'}
            />
            <Column
              header={t('console:catalog.status')}
              body={(p: Profile) => <ActiveBadge active={p.is_active} />}
            />
            <Column header={t('console:catalog.modules')} body={(p: Profile) => list(p.modules)} />
          </DataTable>
        </TabPanel>
        <TabPanel header={`${t('console:catalog.roles')} (${c.role_templates.length})`}>
          <DataTable
            className="sm-table"
            tableStyle={{ minWidth: '48rem' }}
            value={c.role_templates}
            dataKey="code"
          >
            <Column
              header={t('console:catalog.name')}
              body={(r: RoleTemplate) => (
                <span className="sm-tags">
                  {r.name}
                  {r.protected && (
                    <StatusBadge tone="warning" label={t('console:catalog.protected')} />
                  )}
                </span>
              )}
            />
            <Column field="code" header={t('console:catalog.code')} bodyClassName="sm-code" />
            <Column
              header={t('console:catalog.patterns')}
              body={(r: RoleTemplate) => list(r.permission_patterns)}
            />
            <Column
              header={t('console:catalog.excluded')}
              body={(r: RoleTemplate) => list(r.exclude_patterns)}
            />
          </DataTable>
        </TabPanel>
        <TabPanel header={`${t('console:catalog.policies')} (${c.policies.length})`}>
          <DataTable
            className="sm-table"
            tableStyle={{ minWidth: '40rem' }}
            value={c.policies}
            dataKey="status"
          >
            <Column
              header={t('console:catalog.status')}
              body={(p: Policy) => t(`subscriptionStatus.${p.status}`, { defaultValue: p.status })}
            />
            <Column
              header={t('console:catalog.allowedAccess')}
              body={(p: Policy) => list(p.allowed_access.map((a) => t(`console:accessKind.${a}`)))}
            />
            <Column
              header={t('console:catalog.description')}
              body={(p: Policy) => p.description ?? '—'}
            />
          </DataTable>
        </TabPanel>
      </TabView>
    </>
  );
}
