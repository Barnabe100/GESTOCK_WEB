import { Message } from 'primereact/message';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import {
  ORDER_VIEW,
  orderNumber,
  type OrderStatus,
  type PrepStatus,
} from '@/modules/restaurant_orders/api';
import { OrderStatusBadge, PrepBadge } from '@/modules/restaurant_orders/ui';
import { translateError } from '@/shared/lib/errors';
import { formatDateTime } from '@/shared/lib/format';
import { EmptyState } from '@/shared/ui/EmptyState';
import { FormField } from '@/shared/ui/FormField';
import { LoadingState } from '@/shared/ui/LoadingState';
import { StatusBadge, type Tone } from '@/shared/ui/StatusBadge';

import { useBoard, type BoardTable, type TableState } from './api';
import { SiteDropdown, useDefaultSite } from './sites';

/** Tonalité de l'état CALCULÉ PAR LE SERVEUR (aucun recalcul ici). */
const STATE_TONES: Record<TableState, Tone> = {
  FREE: 'success',
  OCCUPIED: 'warning',
  TO_SETTLE: 'info',
};

/** Tables consécutives d'une même zone, dans l'ordre du serveur (sans zone en dernier). */
function byZone(
  tables: BoardTable[],
): { key: string; name: string | null; tables: BoardTable[] }[] {
  const groups: { key: string; name: string | null; tables: BoardTable[] }[] = [];
  for (const table of tables) {
    const key = table.zone_id ?? '';
    const last = groups.at(-1);
    if (last && last.key === key) last.tables.push(table);
    else groups.push({ key, name: table.zone_name, tables: [table] });
  }
  return groups;
}

function TableCard({ table, canOpenOrders }: { table: BoardTable; canOpenOrders: boolean }) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const { locale, timezone } = capabilities.tenant;
  return (
    <li className="sm-floor-table" data-testid="floor-table" aria-label={table.label}>
      <div className="sm-order-card-head">
        <strong className="sm-floor-table-label">{table.label}</strong>
        <StatusBadge
          tone={STATE_TONES[table.state]}
          label={t(`restaurantTables.board.states.${table.state}`)}
        />
      </div>
      {table.seats !== null && (
        <span className="sm-help">{t('restaurantTables.board.seats', { count: table.seats })}</span>
      )}
      {!table.is_active && (
        <StatusBadge tone="danger" label={t('restaurantTables.board.inactiveOccupied')} />
      )}
      {table.order_count > 0 && (
        <span className="sm-help">
          {t('restaurantTables.board.orderCount', { count: table.order_count })}
          {table.occupied_since &&
            ` · ${t('restaurantTables.board.since', {
              time: formatDateTime(table.occupied_since, locale, timezone),
            })}`}
        </span>
      )}
      {table.order_count > 1 && (
        <small className="sm-floor-warning" role="note">
          {t('restaurantTables.board.multipleOrders')}
        </small>
      )}
      {table.orders && table.orders.length > 0 && (
        <ul
          className="sm-floor-orders"
          aria-label={t('restaurantTables.board.ordersOf', { label: table.label })}
        >
          {table.orders.map((order) => (
            <li key={order.id}>
              {canOpenOrders ? (
                <Link to={`/restaurant/orders/${order.id}`}>{orderNumber(order)}</Link>
              ) : (
                <span>{orderNumber(order)}</span>
              )}
              {order.call_name && <span>{` · ${order.call_name}`}</span>}
              <span className="sm-badges">
                {order.status !== 'OPEN' && (
                  <OrderStatusBadge status={order.status as OrderStatus} />
                )}
                {order.to_settle ? (
                  <StatusBadge tone="info" label={t('restaurantTables.board.awaitingSettlement')} />
                ) : (
                  <PrepBadge status={order.prep_status as PrepStatus} />
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
      {canOpenOrders && table.order_count > 0 && (
        <Link className="sm-floor-link" to={`/restaurant/orders?table_id=${table.id}`}>
          {t('restaurantTables.board.viewOrders')}
        </Link>
      )}
    </li>
  );
}

/**
 * Plan de salle d'UN site (palier R5-C2) : tables groupées par zone dans l'ordre du serveur, état
 * Libre / Occupée / À régler CALCULÉ PAR LE SERVEUR, nombre de commandes et ancienneté ;
 * détail des commandes seulement s'il est fourni (`orders_visible`). Actualisé toutes les 15 s.
 */
export function FloorPlan() {
  const { t } = useTranslation();
  const { can, hasModule } = useCapabilities();
  const { siteId: defaultSite, chooseSite } = useDefaultSite();
  const [chosen, setChosen] = useState<string | null>(null);
  const siteId = defaultSite ?? chosen;
  const board = useBoard(siteId);
  const canOpenOrders = hasModule('restaurant.orders') && can(ORDER_VIEW);

  return (
    <div className="sm-floor-plan">
      {chooseSite && (
        <FormField id="floor-site" label={t('layout.site')} required>
          <SiteDropdown id="floor-site" value={chosen} onChange={setChosen} />
        </FormField>
      )}
      {siteId === null ? (
        <Message severity="info" text={t('restaurantTables.board.chooseSite')} />
      ) : board.isPending ? (
        <LoadingState />
      ) : board.isError ? (
        <Message severity="error" text={translateError(t, board.error)} />
      ) : board.data.tables.length === 0 ? (
        <EmptyState
          icon="pi pi-th-large"
          title={t('restaurantTables.board.empty')}
          description={t('restaurantTables.board.emptyHelp')}
        />
      ) : (
        <>
          <div className="sm-badges" role="status" aria-label={t('restaurantTables.board.counts')}>
            <StatusBadge
              tone="success"
              label={t('restaurantTables.board.countFree', { count: board.data.counts.free })}
            />
            <StatusBadge
              tone="warning"
              label={t('restaurantTables.board.countOccupied', {
                count: board.data.counts.occupied,
              })}
            />
            <StatusBadge
              tone="info"
              label={t('restaurantTables.board.countToSettle', {
                count: board.data.counts.to_settle,
              })}
            />
          </div>
          {!board.data.orders_visible && (
            <p className="sm-help">{t('restaurantTables.board.ordersHidden')}</p>
          )}
          {byZone(board.data.tables).map((group) => (
            <section
              key={group.key || 'none'}
              className="sm-floor-zone"
              aria-label={group.name ?? t('restaurantTables.noZone')}
            >
              <h2>{group.name ?? t('restaurantTables.noZone')}</h2>
              <ul className="sm-floor-tables">
                {group.tables.map((table) => (
                  <TableCard key={table.id} table={table} canOpenOrders={canOpenOrders} />
                ))}
              </ul>
            </section>
          ))}
        </>
      )}
    </div>
  );
}
