import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { Dropdown } from 'primereact/dropdown';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useSearchParams } from 'react-router';

import { formatDateTime } from '@/shared/lib/format';
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
import { LicenseStateBadge } from '@/shared/ui/StatusBadge';

import { CONSOLE_BASE } from '../ConsoleLayout';
import { useLicenses, usePlans } from '../queries';
import { paymentDay } from '../tenantDisplay';
import type { ConsoleLicense, LicenseState } from '../types';

const STATES: LicenseState[] = ['ACTIVE', 'NOT_YET_VALID', 'EXPIRED', 'REVOKED'];

/**
 * Licences des sites (filtres : entreprise et site par lien, plan, état calculé, numéro ou
 * entreprise ; pagination et tri côté serveur, plus récentes d'abord).
 */
export function LicensesPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const tenantId = params.get('tenant_id');
  const siteId = params.get('site_id');
  const [table, setTable] = useState<TableState>({
    ...INITIAL_TABLE,
    sortField: 'issued_at',
    sortOrder: -1,
  });
  const [search, setSearch] = useState('');
  const [state, setState] = useState<LicenseState | null>(null);
  const [plan, setPlan] = useState<string | null>(null);
  const plans = usePlans();
  const term = useDebouncedValue(search.trim());
  const licenses = useLicenses(
    toQueryString(table, {
      search: term,
      state,
      plan_code: plan,
      tenant_id: tenantId,
      site_id: siteId,
    }),
  );
  const filtered = Boolean(term || state || plan || tenantId || siteId);
  const firstPage = () => setTable((s) => ({ ...s, first: 0 }));
  const clearScope = () => {
    setParams({});
    firstPage();
  };
  const open = (license: ConsoleLicense) => navigate(`${CONSOLE_BASE}/licenses/${license.id}`);

  return (
    <>
      <PageHeader
        title={t('console:licenses.title')}
        description={t('console:licenses.subtitle')}
        actions={
          <Button
            icon="pi pi-refresh"
            label={t('console:payments.refresh')}
            outlined
            loading={licenses.isFetching}
            onClick={() => void licenses.refetch()}
          />
        }
      />
      <FilterBar
        active={filtered}
        onReset={() => {
          setSearch('');
          setState(null);
          setPlan(null);
          clearScope();
        }}
      >
        <SearchInput
          value={search}
          onChange={(value) => {
            setSearch(value);
            firstPage();
          }}
          placeholder={t('console:licenses.search')}
        />
        <Dropdown
          aria-label={t('console:licenses.state')}
          data-testid="filter-license-state"
          value={state}
          options={[
            { label: t('console:licenses.allStates'), value: null },
            ...STATES.map((s) => ({ label: t(`licenseState.${s}`), value: s })),
          ]}
          onChange={(e) => {
            setState(e.value as LicenseState | null);
            firstPage();
          }}
        />
        <Dropdown
          aria-label={t('console:licenses.plan')}
          value={plan}
          options={[
            { label: t('console:licenses.allPlans'), value: null },
            ...(plans.data ?? []).map((p) => ({ label: p.name, value: p.code })),
          ]}
          onChange={(e) => {
            setPlan(e.value as string | null);
            firstPage();
          }}
        />
        {(tenantId || siteId) && (
          <Button
            type="button"
            icon="pi pi-times"
            iconPos="right"
            outlined
            label={t(siteId ? 'console:licenses.siteFilter' : 'console:payments.tenantFilter')}
            aria-label={t('console:payments.clearTenant')}
            onClick={clearScope}
            data-testid="scope-filter"
          />
        )}
      </FilterBar>
      <ServerTable
        query={licenses}
        table={table}
        onTableChange={setTable}
        minWidth="64rem"
        onRowClick={open}
        empty={
          <ListEmpty filtered={filtered} icon="pi pi-key" title={t('console:licenses.empty')} />
        }
      >
        <Column
          field="license_number"
          sortable
          header={t('console:license.number')}
          bodyClassName="sm-nowrap sm-strong"
        />
        <Column
          header={t('console:payments.company')}
          body={(l: ConsoleLicense) => (
            <span>
              <span className="sm-strong">{l.tenant_name}</span>
              <br />
              <small className="sm-muted">
                {l.site_name} · {l.plan_code}
              </small>
            </span>
          )}
        />
        <Column
          field="valid_until"
          sortable
          header={t('console:license.validity')}
          bodyClassName="sm-nowrap"
          body={(l: ConsoleLicense) =>
            t('console:license.validityValue', {
              start: paymentDay(l.valid_from),
              end: paymentDay(l.valid_until),
            })
          }
        />
        <Column
          header={t('console:license.maxActivations')}
          headerClassName="sm-num"
          bodyClassName="sm-num"
          field="max_activations"
        />
        <Column
          header={t('console:licenses.state')}
          body={(l: ConsoleLicense) => <LicenseStateBadge state={l.state} />}
        />
        <Column
          field="issued_at"
          sortable
          header={t('console:license.issuedAt')}
          bodyClassName="sm-nowrap"
          body={(l: ConsoleLicense) => formatDateTime(l.issued_at, 'fr')}
        />
        <Column
          header=""
          body={(l: ConsoleLicense) => (
            <Button
              icon="pi pi-arrow-right"
              text
              rounded
              aria-label={`${t('console:licenses.open')} ${l.license_number}`}
              onClick={(e) => {
                e.stopPropagation();
                open(l);
              }}
            />
          )}
        />
      </ServerTable>
    </>
  );
}
