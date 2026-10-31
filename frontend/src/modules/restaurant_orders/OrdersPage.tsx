import { Button } from 'primereact/button';
import { Checkbox } from 'primereact/checkbox';
import { Column } from 'primereact/column';
import { Dropdown } from 'primereact/dropdown';
import { TabPanel, TabView } from 'primereact/tabview';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useSearchParams } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { useTables } from '@/modules/restaurant_tables/api';
import { SalePaymentBadge } from '@/modules/sales/ui';
import { formatMoney } from '@/shared/lib/decimal';
import { formatDateTime } from '@/shared/lib/format';
import {
  INITIAL_TABLE,
  toQueryString,
  useDebouncedValue,
  type TableState,
} from '@/shared/lib/serverTable';
import { ListEmpty } from '@/shared/ui/EmptyState';
import { FilterBar } from '@/shared/ui/FilterBar';
import { LoadingState } from '@/shared/ui/LoadingState';
import { PageHeader } from '@/shared/ui/PageHeader';
import { SearchInput } from '@/shared/ui/SearchInput';
import { ServerTable } from '@/shared/ui/ServerTable';

import {
  ORDER_CREATE,
  SETTINGS_MANAGE,
  orderNumber,
  useOrders,
  type Order,
  type OrderStateFilter,
  type PrepStatus,
} from './api';
import { tableLabel, useCanChooseTable } from './tables';
import { OrderStatusBadge, PrepBadge, SettlementBadge } from './ui';

/** Colonnes de l'écran de suivi (P-13) : état de préparation de la commande. */
const BOARD_COLUMNS: PrepStatus[] = ['RECEIVED', 'IN_PREPARATION', 'READY'];

/** Filtre par table (B7) : partagé par les trois onglets, porté par l'URL (`?table_id=`). */
interface TableFilterProps {
  tableId: string | null;
  onChange: (tableId: string | null) => void;
}

/** Choix de la table filtrée : tables des sites lisibles (actives ou non, pour l'historique). */
function TableFilter({ tableId, onChange }: TableFilterProps) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const canChoose = useCanChooseTable();
  const tables = useTables('limit=100&sort=position', canChoose);
  if (!canChoose) return null;
  const showSite = capabilities.site === null && capabilities.sites.length > 1;
  return (
    <Dropdown
      value={tableId}
      onChange={(e) => onChange((e.value as string | undefined) ?? null)}
      options={(tables.data?.items ?? []).map((table) => ({
        value: table.id,
        label: showSite ? `${tableLabel(table)} (${table.site_name})` : tableLabel(table),
      }))}
      placeholder={t('restaurantOrders.allTables')}
      showClear
      aria-label={t('restaurantOrders.table')}
    />
  );
}

function TableCell({ order }: { order: Order }) {
  return order.table ? <span>{tableLabel(order.table)}</span> : <span>—</span>;
}

function OrderCard({ order, showSite }: { order: Order; showSite: boolean }) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const { locale, timezone } = capabilities.tenant;
  return (
    <li>
      <Link
        to={`/restaurant/orders/${order.id}`}
        className="sm-order-card"
        aria-label={t('restaurantOrders.openOrder', { number: order.daily_number })}
      >
        <span className="sm-order-card-head">
          <span className="sm-order-number">{orderNumber(order)}</span>
          <SettlementBadge order={order} />
        </span>
        {order.call_name && <span className="sm-order-call-name">{order.call_name}</span>}
        {order.table && (
          <span className="sm-help">
            {t('restaurantOrders.tableNamed', { label: tableLabel(order.table) })}
          </span>
        )}
        <span className="sm-help">
          {t(`restaurantOrders.serviceModes.${order.service_mode}`)}
          {showSite ? ` · ${order.site_name}` : ''}
        </span>
        <span className="sm-help">
          {order.assigned_name ?? t('restaurantOrders.unassigned')}
          {` · ${formatDateTime(order.created_at, locale, timezone)}`}
        </span>
        <span className="sm-help">
          {t('restaurantOrders.counts', {
            received: order.line_counts.received,
            preparing: order.line_counts.in_preparation,
            ready: order.line_counts.ready,
          })}
        </span>
      </Link>
    </li>
  );
}

/** Écran de suivi en trois colonnes (Reçues, En préparation, Prêtes), actualisé toutes les 15 s. */
function Board({ tableId, onChange }: TableFilterProps) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const [search, setSearch] = useState('');
  const debounced = useDebouncedValue(search);
  const showSite = capabilities.site === null && capabilities.sites.length > 1;
  return (
    <>
      <FilterBar
        onReset={() => {
          setSearch('');
          onChange(null);
        }}
        active={search !== '' || tableId !== null}
      >
        <SearchInput
          value={search}
          placeholder={t('restaurantOrders.searchPlaceholder')}
          onChange={setSearch}
        />
        <TableFilter tableId={tableId} onChange={onChange} />
      </FilterBar>
      <div className="sm-order-board">
        {BOARD_COLUMNS.map((prep) => (
          <BoardColumn
            key={prep}
            prep={prep}
            search={debounced}
            tableId={tableId}
            showSite={showSite}
          />
        ))}
      </div>
    </>
  );
}

function BoardColumn({
  prep,
  search,
  tableId,
  showSite,
}: {
  prep: PrepStatus;
  search: string;
  tableId: string | null;
  showSite: boolean;
}) {
  const { t } = useTranslation();
  const params = new URLSearchParams({
    state: 'active',
    prep_status: prep,
    limit: '100',
    sort: 'created_at',
  });
  if (search) params.set('search', search);
  if (tableId) params.set('table_id', tableId);
  const orders = useOrders(params.toString());
  const items = orders.data?.items ?? [];
  return (
    <section className="sm-order-column" aria-label={t(`restaurantOrders.columns.${prep}`)}>
      <h2>
        {t(`restaurantOrders.columns.${prep}`)} <span className="sm-help">({items.length})</span>
      </h2>
      {orders.isPending ? (
        <LoadingState />
      ) : items.length === 0 ? (
        <p className="sm-help">{t('restaurantOrders.emptyColumn')}</p>
      ) : (
        <ul>
          {items.map((o) => (
            <OrderCard key={o.id} order={o} showSite={showSite} />
          ))}
        </ul>
      )}
    </section>
  );
}

/** Commandes en cours non réglées, de la plus ancienne à la plus récente (limites de V1). */
function ToSettle({ tableId, onChange }: TableFilterProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { capabilities } = useCapabilities();
  const { currency, locale, timezone } = capabilities.tenant;
  const [table, setTable] = useState<TableState>(INITIAL_TABLE);
  const [search, setSearch] = useState('');
  const [servedOnly, setServedOnly] = useState(false);
  const debounced = useDebouncedValue(search);
  const orders = useOrders(
    toQueryString(table, {
      state: 'active',
      settlement_status: 'UNSETTLED',
      unsettled_served: servedOnly ? 'true' : null,
      search: debounced,
      table_id: tableId,
    }),
  );
  const filtered = search !== '' || servedOnly || tableId !== null;
  return (
    <>
      <FilterBar
        onReset={() => {
          setSearch('');
          setServedOnly(false);
          onChange(null);
        }}
        active={filtered}
      >
        <SearchInput
          value={search}
          placeholder={t('restaurantOrders.searchPlaceholder')}
          onChange={(v) => {
            setSearch(v);
            setTable((s) => ({ ...s, first: 0 }));
          }}
        />
        <div className="sm-checkbox">
          <Checkbox
            inputId="orders-served-only"
            checked={servedOnly}
            onChange={(e) => setServedOnly(e.checked === true)}
          />
          <label htmlFor="orders-served-only">{t('restaurantOrders.servedUnsettled')}</label>
        </div>
        <TableFilter
          tableId={tableId}
          onChange={(value) => {
            onChange(value);
            setTable((s) => ({ ...s, first: 0 }));
          }}
        />
      </FilterBar>
      <ServerTable
        query={orders}
        table={table}
        onTableChange={setTable}
        minWidth="44rem"
        onRowClick={(o: Order) => void navigate(`/restaurant/orders/${o.id}`)}
        empty={<ListEmpty filtered={filtered} title={t('restaurantOrders.nothingToSettle')} />}
      >
        <Column
          header={t('restaurantOrders.number')}
          sortField="number"
          sortable
          body={(o: Order) => <strong>{orderNumber(o)}</strong>}
        />
        <Column
          header={t('restaurantOrders.callNameOrCustomer')}
          body={(o: Order) => o.call_name ?? o.customer_name ?? '—'}
        />
        <Column header={t('restaurantOrders.table')} body={(o: Order) => <TableCell order={o} />} />
        <Column
          header={t('restaurantOrders.prepState')}
          body={(o: Order) => <PrepBadge status={o.prep_status} />}
        />
        <Column
          header={t('restaurantOrders.total')}
          headerClassName="sm-num"
          bodyClassName="sm-num"
          body={(o: Order) => formatMoney(o.total, currency, locale)}
        />
        <Column
          header={t('restaurantOrders.createdAt')}
          sortField="created_at"
          sortable
          body={(o: Order) => formatDateTime(o.created_at, locale, timezone)}
        />
        <Column
          header={t('restaurantOrders.lastServedAt')}
          body={(o: Order) =>
            o.last_served_at ? formatDateTime(o.last_served_at, locale, timezone) : '—'
          }
        />
      </ServerTable>
    </>
  );
}

/** Historique : commandes closes, annulées ou toutes, recherche par numéro ou nom d'appel. */
function History({ tableId, onChange }: TableFilterProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { capabilities } = useCapabilities();
  const { currency, locale, timezone } = capabilities.tenant;
  const [table, setTable] = useState<TableState>({
    ...INITIAL_TABLE,
    sortField: 'created_at',
    sortOrder: -1,
  });
  const [search, setSearch] = useState('');
  const [state, setState] = useState<OrderStateFilter>('closed');
  const debounced = useDebouncedValue(search);
  const orders = useOrders(toQueryString(table, { state, search: debounced, table_id: tableId }));
  return (
    <>
      <FilterBar
        onReset={() => {
          setSearch('');
          setState('closed');
          onChange(null);
        }}
        active={search !== '' || state !== 'closed' || tableId !== null}
      >
        <SearchInput
          value={search}
          placeholder={t('restaurantOrders.searchPlaceholder')}
          onChange={(v) => {
            setSearch(v);
            setTable((s) => ({ ...s, first: 0 }));
          }}
        />
        <Dropdown
          value={state}
          onChange={(e) => {
            setState(e.value as OrderStateFilter);
            setTable((s) => ({ ...s, first: 0 }));
          }}
          options={(['closed', 'cancelled', 'active', 'all'] as const).map((v) => ({
            value: v,
            label: t(`restaurantOrders.stateFilter.${v}`),
          }))}
          aria-label={t('restaurantOrders.state')}
        />
        <TableFilter
          tableId={tableId}
          onChange={(value) => {
            onChange(value);
            setTable((s) => ({ ...s, first: 0 }));
          }}
        />
      </FilterBar>
      <ServerTable
        query={orders}
        table={table}
        onTableChange={setTable}
        minWidth="48rem"
        onRowClick={(o: Order) => void navigate(`/restaurant/orders/${o.id}`)}
        empty={
          <ListEmpty
            filtered={search !== '' || tableId !== null}
            title={t('restaurantOrders.noOrders')}
          />
        }
      >
        <Column
          header={t('restaurantOrders.number')}
          sortField="number"
          sortable
          body={(o: Order) => <strong>{orderNumber(o)}</strong>}
        />
        <Column
          header={t('restaurantOrders.businessDate')}
          sortField="business_date"
          sortable
          body={(o: Order) => formatDateTime(o.created_at, locale, timezone)}
        />
        <Column header={t('restaurantOrders.table')} body={(o: Order) => <TableCell order={o} />} />
        <Column
          header={t('restaurantOrders.state')}
          body={(o: Order) => <OrderStatusBadge status={o.status} />}
        />
        <Column header={t('restaurantOrders.sale')} body={(o: Order) => o.sale_number ?? '—'} />
        <Column
          header={t('restaurantOrders.paymentStatus')}
          body={(o: Order) =>
            o.payment_status ? <SalePaymentBadge status={o.payment_status} /> : '—'
          }
        />
        <Column
          header={t('restaurantOrders.total')}
          headerClassName="sm-num"
          bodyClassName="sm-num"
          body={(o: Order) => formatMoney(o.total, currency, locale)}
        />
      </ServerTable>
    </>
  );
}

/**
 * Commandes de restauration (palier R2) : suivi en trois colonnes, commandes à régler (dont les
 * servies non réglées, limites de V1) et historique. La saisie, le détail, le règlement et les
 * réglages ont leurs propres écrans.
 */
export default function OrdersPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { can } = useCapabilities();
  // Filtre par table porté par l'URL : lien « Voir les commandes de la table » du plan de salle.
  const [params, setParams] = useSearchParams();
  const tableId = params.get('table_id');
  const filter: TableFilterProps = {
    tableId,
    onChange: (value) =>
      setParams(
        (current) => {
          const next = new URLSearchParams(current);
          if (value) next.set('table_id', value);
          else next.delete('table_id');
          return next;
        },
        { replace: true },
      ),
  };
  return (
    <>
      <PageHeader
        title={t('restaurantOrders.title')}
        description={t('restaurantOrders.subtitle')}
        actions={
          <>
            {can(SETTINGS_MANAGE) && (
              <Button
                icon="pi pi-cog"
                label={t('restaurantOrders.settings.title')}
                outlined
                onClick={() => void navigate('/restaurant/orders/settings')}
              />
            )}
            {can(ORDER_CREATE) && (
              <Button
                icon="pi pi-plus"
                label={t('restaurantOrders.newOrder')}
                onClick={() => void navigate('/restaurant/orders/new')}
              />
            )}
          </>
        }
      />
      <TabView>
        <TabPanel header={t('restaurantOrders.tabs.board')}>
          <Board {...filter} />
        </TabPanel>
        <TabPanel header={t('restaurantOrders.tabs.toSettle')}>
          <ToSettle {...filter} />
        </TabPanel>
        <TabPanel header={t('restaurantOrders.tabs.history')}>
          <History {...filter} />
        </TabPanel>
      </TabView>
    </>
  );
}
