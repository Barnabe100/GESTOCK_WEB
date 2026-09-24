import { Column } from 'primereact/column';
import { DataTable, type DataTableStateEvent } from 'primereact/datatable';
import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatCost, formatQuantity } from '@/shared/lib/decimal';
import { formatDateTime } from '@/shared/lib/format';
import {
  INITIAL_TABLE,
  toQueryString,
  useDebouncedValue,
  type TableState,
} from '@/shared/lib/serverTable';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { PageHeader } from '@/shared/ui/PageHeader';
import { SearchInput } from '@/shared/ui/SearchInput';

import { useMovements, type Movement, type MovementType } from './api';

/** Types présents dans cette version (les autres sont réservés aux sous-phases suivantes). */
const TYPES: MovementType[] = ['ENTRY', 'EXIT', 'SALE', 'CANCELLATION'];

export default function MovementsPage() {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const [table, setTable] = useState<TableState>({
    ...INITIAL_TABLE,
    sortField: 'occurred_at',
    sortOrder: -1,
  });
  const [search, setSearch] = useState('');
  const [type, setType] = useState<MovementType | null>(null);
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const debounced = useDebouncedValue(search);
  const movements = useMovements(
    toQueryString(table, {
      search: debounced,
      movement_type: type,
      date_from: dateFrom,
      date_to: dateTo,
    }),
  );
  const { currency, locale, timezone } = capabilities.tenant;
  const showSite = capabilities.site === null && capabilities.sites.length > 1;

  const onPage = (e: DataTableStateEvent) =>
    setTable({ first: e.first, rows: e.rows, sortField: e.sortField, sortOrder: e.sortOrder });
  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));

  return (
    <>
      <PageHeader title={t('stock.movementsTitle')} />
      <div className="sm-toolbar">
        <SearchInput
          value={search}
          placeholder={t('stock.movementSearch')}
          onChange={(v) => {
            setSearch(v);
            resetPage();
          }}
        />
        <Dropdown
          value={type}
          onChange={(e) => {
            setType((e.value as MovementType | undefined) ?? null);
            resetPage();
          }}
          options={TYPES.map((v) => ({ value: v, label: t(`stock.movementTypes.${v}`) }))}
          placeholder={t('stock.allTypes')}
          showClear
          aria-label={t('stock.movementType')}
        />
        <InputText
          type="date"
          value={dateFrom}
          aria-label={t('stock.dateFrom')}
          title={t('stock.dateFrom')}
          onChange={(e) => {
            setDateFrom(e.target.value);
            resetPage();
          }}
        />
        <InputText
          type="date"
          value={dateTo}
          aria-label={t('stock.dateTo')}
          title={t('stock.dateTo')}
          onChange={(e) => {
            setDateTo(e.target.value);
            resetPage();
          }}
        />
      </div>
      {movements.isError ? (
        <ErrorMessage error={movements.error} onRetry={() => void movements.refetch()} />
      ) : (
        <DataTable
          value={movements.data?.items ?? []}
          loading={movements.isFetching}
          dataKey="id"
          lazy
          paginator
          first={table.first}
          rows={table.rows}
          rowsPerPageOptions={[10, 25, 50, 100]}
          totalRecords={movements.data?.total ?? 0}
          sortField={table.sortField}
          sortOrder={table.sortOrder}
          onPage={onPage}
          onSort={onPage}
          emptyMessage={t('common.noData')}
        >
          <Column
            field="occurred_at"
            header={t('stock.date')}
            sortable
            body={(m: Movement) => formatDateTime(m.occurred_at, locale, timezone)}
          />
          {showSite && <Column field="site_name" header={t('layout.site')} />}
          <Column
            header={t('stock.movementType')}
            body={(m: Movement) => t(`stock.movementTypes.${m.movement_type}`)}
          />
          <Column field="document_number" header={t('stock.document')} />
          <Column
            header={t('stock.article')}
            body={(m: Movement) => `${m.article_reference} — ${m.article_designation}`}
          />
          <Column
            header={t('stock.quantity')}
            body={(m: Movement) =>
              `${m.quantity.startsWith('-') ? '' : '+'}${formatQuantity(m.quantity, locale)} ${m.unit}`
            }
          />
          <Column
            header={t('stock.stockAfter')}
            body={(m: Movement) => formatQuantity(m.quantity_after, locale)}
          />
          <Column
            header={t('stock.unitCost')}
            body={(m: Movement) => formatCost(m.unit_cost, currency, locale)}
          />
          <Column
            header={t('stock.averageCost')}
            body={(m: Movement) => formatCost(m.average_cost_after, currency, locale)}
          />
          <Column field="user_name" header={t('audit.user')} />
        </DataTable>
      )}
    </>
  );
}
