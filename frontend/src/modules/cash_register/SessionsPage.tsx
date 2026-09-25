import { Column } from 'primereact/column';
import { Dropdown } from 'primereact/dropdown';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatMoney } from '@/shared/lib/decimal';
import { formatDateTime } from '@/shared/lib/format';
import {
  INITIAL_TABLE,
  toQueryString,
  useDebouncedValue,
  type TableState,
} from '@/shared/lib/serverTable';
import { ListEmpty } from '@/shared/ui/EmptyState';
import { DateRangeFilter, FilterBar } from '@/shared/ui/FilterBar';
import { PageHeader } from '@/shared/ui/PageHeader';
import { RowActions } from '@/shared/ui/RowActions';
import { SearchInput } from '@/shared/ui/SearchInput';
import { ServerTable } from '@/shared/ui/ServerTable';

import {
  CASH_SESSION_STATUSES,
  useCashRegisters,
  useCashSessions,
  type CashSession,
  type CashSessionStatus,
} from './api';
import { CashSessionBadge, CashVariance } from './ui';

/** Sessions de caisse (ouvertes et clôturées) : fond initial, solde, comptage, écart. */
export default function SessionsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { can, capabilities } = useCapabilities();
  const { currency, locale, timezone } = capabilities.tenant;
  const multiSite = capabilities.site === null && capabilities.sites.length > 1;
  const [table, setTable] = useState<TableState>({
    ...INITIAL_TABLE,
    sortField: 'opened_at',
    sortOrder: -1,
  });
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<CashSessionStatus | null>(null);
  const [registerId, setRegisterId] = useState<string | null>(null);
  const [siteId, setSiteId] = useState<string | null>(null);
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const debounced = useDebouncedValue(search);
  const sessions = useCashSessions(
    toQueryString(table, {
      search: debounced,
      status,
      cash_register_id: registerId,
      site_id: siteId,
      date_from: dateFrom,
      date_to: dateTo,
    }),
  );
  const registers = useCashRegisters('limit=200&sort=code', can('cash_register.register.view'));
  const money = (value: string) => formatMoney(value, currency, locale);
  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));
  const filtered =
    search !== '' ||
    status !== null ||
    registerId !== null ||
    siteId !== null ||
    dateFrom !== '' ||
    dateTo !== '';
  const reset = () => {
    setSearch('');
    setStatus(null);
    setRegisterId(null);
    setSiteId(null);
    setDateFrom('');
    setDateTo('');
    resetPage();
  };
  const open = (s: CashSession) => void navigate(`/cash/sessions/${s.id}`);

  return (
    <>
      <PageHeader title={t('cash.sessionsTitle')} description={t('cash.sessionsSubtitle')} />
      <FilterBar onReset={reset} active={filtered}>
        <SearchInput
          value={search}
          placeholder={t('cash.sessionSearch')}
          onChange={(v) => {
            setSearch(v);
            resetPage();
          }}
        />
        <Dropdown
          value={status}
          onChange={(e) => {
            setStatus((e.value as CashSessionStatus | undefined) ?? null);
            resetPage();
          }}
          options={CASH_SESSION_STATUSES.map((v) => ({
            value: v,
            label: t(`cash.sessionStatus.${v}`),
          }))}
          placeholder={t('cash.allStatuses')}
          showClear
          aria-label={t('cash.status')}
        />
        {registers.data && (
          <Dropdown
            value={registerId}
            onChange={(e) => {
              setRegisterId((e.value as string | undefined) ?? null);
              resetPage();
            }}
            options={registers.data.items.map((r) => ({ value: r.id, label: r.name }))}
            placeholder={t('cash.allRegisters')}
            showClear
            aria-label={t('cash.register')}
          />
        )}
        {multiSite && (
          <Dropdown
            value={siteId}
            onChange={(e) => {
              setSiteId((e.value as string | undefined) ?? null);
              resetPage();
            }}
            options={capabilities.sites.map((s) => ({ value: s.id, label: s.name }))}
            placeholder={t('sales.allSites')}
            showClear
            aria-label={t('layout.site')}
          />
        )}
        <DateRangeFilter
          from={dateFrom}
          to={dateTo}
          onChange={({ from, to }) => {
            setDateFrom(from);
            setDateTo(to);
            resetPage();
          }}
        />
      </FilterBar>
      <ServerTable
        query={sessions}
        table={table}
        onTableChange={setTable}
        onRowClick={open}
        minWidth="60rem"
        empty={<ListEmpty filtered={filtered} icon="pi pi-clock" title={t('cash.sessionsEmpty')} />}
      >
        <Column field="number" header={t('cash.session')} sortable bodyClassName="sm-nowrap" />
        <Column field="cash_register_name" header={t('cash.register')} />
        {multiSite && <Column field="site_name" header={t('layout.site')} />}
        <Column
          field="opened_at"
          header={t('cash.openedAt')}
          sortable
          body={(s: CashSession) => (
            <div>
              <div>{formatDateTime(s.opened_at, locale, timezone)}</div>
              <div className="sm-help">{s.opened_by_name}</div>
            </div>
          )}
        />
        <Column
          header={t('cash.status')}
          body={(s: CashSession) => <CashSessionBadge status={s.status} />}
        />
        <Column
          header={t('cash.theoreticalBalance')}
          headerClassName="sm-num"
          bodyClassName="sm-num"
          body={(s: CashSession) => money(s.theoretical_balance)}
        />
        <Column
          header={t('cash.countedBalance')}
          headerClassName="sm-num"
          bodyClassName="sm-num"
          body={(s: CashSession) => (s.counted_balance ? money(s.counted_balance) : '—')}
        />
        <Column
          header={t('cash.variance.label')}
          body={(s: CashSession) => (
            <CashVariance value={s.variance} currency={currency} locale={locale} />
          )}
        />
        <Column
          header={t('common.actions')}
          body={(s: CashSession) => (
            <RowActions
              actions={[
                {
                  key: 'open',
                  label: t('cash.viewSession'),
                  icon: 'pi pi-eye',
                  onClick: () => open(s),
                },
              ]}
            />
          )}
        />
      </ServerTable>
    </>
  );
}
