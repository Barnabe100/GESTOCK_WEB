import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { Dropdown } from 'primereact/dropdown';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import {
  INITIAL_TABLE,
  toQueryString,
  useDebouncedValue,
  type TableState,
} from '@/shared/lib/serverTable';
import { ListEmpty } from '@/shared/ui/EmptyState';
import { FilterBar } from '@/shared/ui/FilterBar';
import { PageHeader } from '@/shared/ui/PageHeader';
import { SearchInput } from '@/shared/ui/SearchInput';
import { ServerTable } from '@/shared/ui/ServerTable';
import { SubscriptionStatusBadge } from '@/shared/ui/StatusBadge';

import { CONSOLE_BASE } from '../ConsoleLayout';
import { usePlans, useTenants } from '../queries';
import { TenantStatusBadge, tenantDate } from '../tenantDisplay';
import type { SubscriptionStatus, TenantListItem } from '../types';

const SUBSCRIPTION_STATUSES: SubscriptionStatus[] = [
  'pending_activation',
  'trial',
  'active',
  'past_due',
  'expired',
  'suspended',
  'cancelled',
];

/** Entreprises clientes : métadonnées plateforme (pagination, tri et filtres côté serveur). */
export function TenantsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const plans = usePlans();
  const [table, setTable] = useState<TableState>({
    ...INITIAL_TABLE,
    sortField: 'name',
    sortOrder: 1,
  });
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<string | null>(null);
  const [plan, setPlan] = useState<string | null>(null);
  const [subscription, setSubscription] = useState<string | null>(null);
  const term = useDebouncedValue(search.trim());
  const query = toQueryString(table, {
    search: term,
    status,
    plan_code: plan,
    subscription_status: subscription,
  });
  const tenants = useTenants(query);
  const filtered = Boolean(term || status || plan || subscription);
  const change =
    <T,>(setter: (value: T) => void) =>
    (value: T) => {
      setter(value);
      setTable((s) => ({ ...s, first: 0 }));
    };
  const open = (tenant: TenantListItem) => navigate(`${CONSOLE_BASE}/tenants/${tenant.id}`);

  const planOptions = useMemo(
    () => [
      { label: t('console:tenants.allPlans'), value: null },
      ...(plans.data ?? []).map((p) => ({ label: p.name, value: p.code })),
    ],
    [plans.data, t],
  );

  return (
    <>
      <PageHeader title={t('console:tenants.title')} description={t('console:tenants.subtitle')} />
      <FilterBar
        active={filtered}
        onReset={() => {
          setSearch('');
          setStatus(null);
          setPlan(null);
          setSubscription(null);
          setTable((s) => ({ ...s, first: 0 }));
        }}
      >
        <SearchInput
          value={search}
          onChange={change(setSearch)}
          placeholder={t('console:tenants.search')}
        />
        <Dropdown
          aria-label={t('console:tenants.status')}
          value={status}
          options={[
            { label: t('console:tenants.allStatuses'), value: null },
            { label: t('console:tenantStatus.active'), value: 'active' },
            { label: t('console:tenantStatus.suspended'), value: 'suspended' },
          ]}
          onChange={(e) => change(setStatus)(e.value as string | null)}
          data-testid="filter-status"
        />
        <Dropdown
          aria-label={t('console:tenants.plan')}
          value={plan}
          options={planOptions}
          onChange={(e) => change(setPlan)(e.value as string | null)}
          data-testid="filter-plan"
        />
        <Dropdown
          aria-label={t('console:tenants.subscription')}
          value={subscription}
          options={[
            { label: t('console:tenants.allSubscriptions'), value: null },
            ...SUBSCRIPTION_STATUSES.map((s) => ({
              label: t(`subscriptionStatus.${s}`),
              value: s,
            })),
          ]}
          onChange={(e) => change(setSubscription)(e.value as string | null)}
          data-testid="filter-subscription"
        />
      </FilterBar>
      <ServerTable
        query={tenants}
        table={table}
        onTableChange={setTable}
        minWidth="60rem"
        onRowClick={open}
        empty={
          <ListEmpty filtered={filtered} icon="pi pi-building" title={t('console:tenants.empty')} />
        }
      >
        <Column
          field="name"
          sortable
          header={t('console:tenants.company')}
          body={(row: TenantListItem) => (
            <span>
              <span className="sm-strong">{row.name}</span>
              <br />
              <small className="sm-muted">
                {row.trade_name ? `${row.trade_name} · ` : ''}
                {row.business_profile_name}
              </small>
            </span>
          )}
        />
        <Column header={t('console:tenants.plan')} body={(row: TenantListItem) => row.plan_name} />
        <Column
          field="status"
          sortable
          header={t('console:tenants.status')}
          body={(row: TenantListItem) => <TenantStatusBadge status={row.status} />}
        />
        <Column
          header={t('console:tenants.subscription')}
          body={(row: TenantListItem) => <SubscriptionStatusBadge status={row.effective_status} />}
        />
        <Column
          field="current_period_end"
          sortable
          header={t('console:tenants.expiration')}
          bodyClassName="sm-nowrap"
          body={(row: TenantListItem) => tenantDate(row.current_period_end)}
        />
        <Column field="sites" header={t('console:tenants.sites')} bodyClassName="sm-num" />
        <Column field="users" header={t('console:tenants.users')} bodyClassName="sm-num" />
        <Column
          field="created_at"
          sortable
          header={t('console:tenants.createdAt')}
          bodyClassName="sm-nowrap"
          body={(row: TenantListItem) => tenantDate(row.created_at)}
        />
        <Column
          header=""
          body={(row: TenantListItem) => (
            <Button
              icon="pi pi-arrow-right"
              text
              rounded
              aria-label={`${t('console:tenants.open')} ${row.name}`}
              onClick={(e) => {
                e.stopPropagation();
                open(row);
              }}
            />
          )}
        />
      </ServerTable>
    </>
  );
}
