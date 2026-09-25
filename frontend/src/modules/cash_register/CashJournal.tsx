import { Column } from 'primereact/column';
import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatMoney, normalizeDecimal } from '@/shared/lib/decimal';
import { formatDateTime } from '@/shared/lib/format';
import {
  INITIAL_TABLE,
  toQueryString,
  useDebouncedValue,
  type TableState,
} from '@/shared/lib/serverTable';
import { ListEmpty } from '@/shared/ui/EmptyState';
import { DateRangeFilter, FilterBar } from '@/shared/ui/FilterBar';
import { SearchInput } from '@/shared/ui/SearchInput';
import { ServerTable } from '@/shared/ui/ServerTable';

import {
  CASH_MOVEMENT_TYPES,
  useCashMovements,
  type CashMovement,
  type CashMovementType,
} from './api';
import { CashMovementBadge } from './ui';

/**
 * Journal de caisse paginé côté serveur : date, type, référence, motif, utilisateur, entrée,
 * sortie et solde de la session après chaque mouvement (calculé par le serveur).
 * `sessionId` : journal d'une session ; sinon, tous les mouvements des sites visibles.
 */
export function CashJournal({ sessionId }: { sessionId?: string }) {
  const { t } = useTranslation();
  const { can, capabilities } = useCapabilities();
  const { currency, locale, timezone } = capabilities.tenant;
  const [table, setTable] = useState<TableState>({
    ...INITIAL_TABLE,
    sortField: 'occurred_at',
    sortOrder: sessionId ? 1 : -1,
  });
  const [search, setSearch] = useState('');
  const [type, setType] = useState<CashMovementType | null>(null);
  const [minAmount, setMinAmount] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const debouncedSearch = useDebouncedValue(search);
  const debouncedMin = useDebouncedValue(minAmount);
  const movements = useCashMovements(
    toQueryString(table, {
      search: debouncedSearch,
      movement_type: type,
      min_amount: normalizeDecimal(debouncedMin, 2),
      date_from: dateFrom,
      date_to: dateTo,
    }),
    sessionId,
  );
  const money = (value: string) => formatMoney(value, currency, locale);
  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));
  const filtered =
    search !== '' || type !== null || minAmount !== '' || dateFrom !== '' || dateTo !== '';
  const reset = () => {
    setSearch('');
    setType(null);
    setMinAmount('');
    setDateFrom('');
    setDateTo('');
    resetPage();
  };
  const reference = (m: CashMovement) => {
    if (m.source_type === 'sale' && m.source_id && m.source_number) {
      const label = m.reference ? `${m.source_number} · ${m.reference}` : m.source_number;
      return can('sales.sale.view') ? <Link to={`/sales/${m.source_id}`}>{label}</Link> : label;
    }
    return m.reference ?? '—';
  };
  const motive = (m: CashMovement) => {
    if (m.category === null) return m.reason ?? '—';
    return `${t(`cash.categories.${m.category}`)} — ${m.reason ?? ''}`;
  };

  return (
    <>
      <FilterBar onReset={reset} active={filtered}>
        <SearchInput
          value={search}
          placeholder={t('cash.journalSearch')}
          onChange={(v) => {
            setSearch(v);
            resetPage();
          }}
        />
        <Dropdown
          value={type}
          onChange={(e) => {
            setType((e.value as CashMovementType | undefined) ?? null);
            resetPage();
          }}
          options={CASH_MOVEMENT_TYPES.map((v) => ({
            value: v,
            label: t(`cash.movementType.${v}`),
          }))}
          placeholder={t('cash.allTypes')}
          showClear
          aria-label={t('cash.movementTypeLabel')}
        />
        <InputText
          className="sm-filter-amount"
          value={minAmount}
          inputMode="decimal"
          placeholder={t('cash.minAmount', { currency })}
          aria-label={t('cash.minAmountLabel')}
          onChange={(e) => {
            setMinAmount(e.target.value);
            resetPage();
          }}
        />
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
        query={movements}
        table={table}
        onTableChange={setTable}
        minWidth="60rem"
        empty={<ListEmpty filtered={filtered} icon="pi pi-list" title={t('cash.journalEmpty')} />}
      >
        <Column
          field="occurred_at"
          header={t('cash.date')}
          sortable
          body={(m: CashMovement) => formatDateTime(m.occurred_at, locale, timezone)}
        />
        {!sessionId && (
          <Column
            header={t('cash.register')}
            body={(m: CashMovement) => (
              <Link to={`/cash/sessions/${m.cash_session_id}`}>
                {`${m.cash_register_name} · ${m.cash_session_number}`}
              </Link>
            )}
          />
        )}
        <Column
          header={t('cash.movementTypeLabel')}
          body={(m: CashMovement) => <CashMovementBadge type={m.movement_type} />}
        />
        <Column header={t('cash.reference')} body={reference} bodyClassName="sm-nowrap" />
        <Column header={t('cash.reason')} body={motive} />
        <Column field="created_by_name" header={t('cash.user')} />
        <Column
          header={t('cash.cashIn')}
          headerClassName="sm-num"
          bodyClassName="sm-num"
          body={(m: CashMovement) => (m.signed_amount.startsWith('-') ? '' : money(m.amount))}
        />
        <Column
          header={t('cash.cashOut')}
          headerClassName="sm-num"
          bodyClassName="sm-num"
          body={(m: CashMovement) => (m.signed_amount.startsWith('-') ? money(m.amount) : '')}
        />
        <Column
          header={t('cash.balanceAfter')}
          headerClassName="sm-num"
          bodyClassName="sm-num sm-strong"
          body={(m: CashMovement) => money(m.balance_after)}
        />
      </ServerTable>
    </>
  );
}
