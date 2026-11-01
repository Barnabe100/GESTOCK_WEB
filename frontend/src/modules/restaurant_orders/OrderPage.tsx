import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { DataTable } from 'primereact/datatable';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { InputTextarea } from 'primereact/inputtextarea';
import { Message } from 'primereact/message';
import { useState, type ReactNode } from 'react';
import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';
import { Link, useParams, useSearchParams } from 'react-router';

import { ApiError } from '@/core/api/client';
import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import type { CreditOverride, ExpiredLotOverride, ImmediatePayment } from '@/modules/sales/api';
import { PriorityBadge } from '@/modules/restaurant_kitchen/ui';
import { CustomerPicker, type CustomerOption } from '@/modules/sales/CustomerPicker';
import { ReceiptDialog } from '@/modules/sales/receipt/ReceiptDialog';
import { SalePaymentBadge, saleError } from '@/modules/sales/ui';
import { ValidateSaleDialog } from '@/modules/sales/ValidateSaleDialog';
import { expiredShortages, type ExpiredShortage } from '@/modules/stock/ui';
import { formatMoney, formatQuantity } from '@/shared/lib/decimal';
import { formatDateTime } from '@/shared/lib/format';
import { FormField } from '@/shared/ui/FormField';
import { LoadingState } from '@/shared/ui/LoadingState';
import { PageHeader } from '@/shared/ui/PageHeader';
import { RowActions, type RowAction } from '@/shared/ui/RowActions';
import { StatusBadge } from '@/shared/ui/StatusBadge';
import { useToast } from '@/shared/ui/toast';

import {
  ORDER_CANCEL,
  ORDER_CANCEL_PREPARED,
  ORDER_CLAIM,
  ORDER_CREATE,
  ORDER_PREPARE,
  ORDER_REASSIGN,
  ORDER_SERVE,
  SETTLE_PERMISSIONS,
  newKey,
  orderNumber,
  useAssignees,
  useOrder,
  useOrderEvents,
  useOrderMutations,
  type Order,
  type OrderLine,
  type TransitionAction,
} from './api';
import { PriorityDialog, RouteLinesDialog, useKitchenOnOrder } from './kitchen';
import { OrderTicketDialog } from './OrderTicket';
import { TablePicker, tableLabel, useCanChooseTable } from './tables';
import {
  LineStatusBadge,
  MenuCart,
  OrderStatusBadge,
  PrepBadge,
  SettlementBadge,
  orderError,
  toLineInputs,
  type CartLine,
} from './ui';

const OPEN_LINE = (l: OrderLine) => l.status !== 'SERVED' && l.status !== 'CANCELLED';

/** Motif obligatoire (annulation d'une ligne, de la commande ; réattribution). */
function ReasonDialog({
  header,
  message,
  confirmLabel,
  pending,
  onConfirm,
  onClose,
  children,
  optional = false,
}: {
  header: string;
  message: string;
  confirmLabel: string;
  pending: boolean;
  onConfirm: (reason: string) => void;
  onClose: () => void;
  children?: ReactNode;
  /** Motif facultatif (libération) : action non destructive, confirmable sans motif. */
  optional?: boolean;
}) {
  const { t } = useTranslation();
  const [reason, setReason] = useState('');
  return (
    <Dialog header={header} visible onHide={onClose} className="sm-dialog">
      <div className="sm-form">
        <p>{message}</p>
        {children}
        <FormField id="order-reason" label={t('restaurantOrders.reason')} required={!optional}>
          <InputTextarea
            id="order-reason"
            rows={2}
            maxLength={500}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            autoFocus
          />
        </FormField>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button
            type="button"
            label={confirmLabel}
            severity={optional ? undefined : 'danger'}
            disabled={!optional && reason.trim() === ''}
            loading={pending}
            onClick={() => onConfirm(reason.trim())}
          />
        </div>
      </div>
    </Dialog>
  );
}

function AddLinesDialog({ order, onClose }: { order: Order; onClose: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const [lines, setLines] = useState<CartLine[]>([]);
  const [key] = useState(newKey);
  const { addLines } = useOrderMutations();
  return (
    <Dialog
      header={t('restaurantOrders.addLines')}
      visible
      onHide={onClose}
      className="sm-dialog sm-dialog-wide"
    >
      <MenuCart siteId={order.site_id} lines={lines} onChange={setLines} />
      <div className="sm-dialog-actions">
        <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
        <Button
          type="button"
          icon="pi pi-plus"
          label={t('restaurantOrders.addLines')}
          disabled={lines.length === 0}
          loading={addLines.isPending}
          onClick={() =>
            addLines.mutate(
              { id: order.id, lines: toLineInputs(lines), idempotencyKey: key },
              {
                onSuccess: () => {
                  toast.success(t('restaurantOrders.linesAdded'));
                  onClose();
                },
                onError: (error) => toast.error(orderError(t, error)),
              },
            )
          }
        />
      </div>
    </Dialog>
  );
}

/** Libération par son responsable : la commande redevient « à prendre » (motif facultatif). */
function ReleaseDialog({ order, onClose }: { order: Order; onClose: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { release } = useOrderMutations();
  return (
    <ReasonDialog
      header={t('restaurantOrders.release')}
      message={t('restaurantOrders.releaseHelp')}
      confirmLabel={t('restaurantOrders.release')}
      pending={release.isPending}
      optional
      onClose={onClose}
      onConfirm={(reason) =>
        release.mutate(
          { id: order.id, reason: reason === '' ? null : reason },
          {
            onSuccess: () => {
              toast.success(t('restaurantOrders.released'));
              onClose();
            },
            onError: (error) => toast.error(orderError(t, error)),
          },
        )
      }
    />
  );
}

function ReassignDialog({ order, onClose }: { order: Order; onClose: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const assignees = useAssignees(order.id, true);
  const [assignee, setAssignee] = useState<string | null>(null);
  const { reassign } = useOrderMutations();
  return (
    <ReasonDialog
      header={t('restaurantOrders.reassign')}
      message={t('restaurantOrders.reassignHelp')}
      confirmLabel={t('restaurantOrders.reassign')}
      pending={reassign.isPending}
      onClose={onClose}
      onConfirm={(reason) => {
        if (assignee === null) return toast.error(t('restaurantOrders.chooseAssignee'));
        reassign.mutate(
          { id: order.id, assignee, reason },
          {
            onSuccess: () => {
              toast.success(t('restaurantOrders.reassigned'));
              onClose();
            },
            onError: (error) => toast.error(orderError(t, error)),
          },
        );
      }}
    >
      <FormField id="order-assignee" label={t('restaurantOrders.newAssignee')} required>
        <Dropdown
          inputId="order-assignee"
          value={assignee}
          onChange={(e) => setAssignee(e.value as string | null)}
          options={(assignees.data ?? [])
            .filter((a) => a.user_id !== order.assigned_user_id)
            .map((a) => ({ value: a.user_id, label: a.full_name }))}
          placeholder={t('restaurantOrders.chooseAssignee')}
          emptyMessage={t('restaurantOrders.noAssignee')}
        />
      </FormField>
    </ReasonDialog>
  );
}

/**
 * Association tardive d'un client (Z1) : commande ouverte non réglée ; motif obligatoire pour
 * remplacer un client déjà associé. Aucun crédit n'en découle : le règlement applique les
 * règles des ventes. Le serveur revérifie tout (permission, état, client actif).
 */
function CustomerDialog({ order, onClose }: { order: Order; onClose: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const [customer, setCustomer] = useState<CustomerOption | null>(null);
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | null>(null);
  const { setCustomer: associate } = useOrderMutations();
  const replacing = order.customer_id !== null;
  const unchanged = customer !== null && customer.id === order.customer_id;
  return (
    <Dialog
      header={t(replacing ? 'restaurantOrders.changeCustomer' : 'restaurantOrders.setCustomer')}
      visible
      onHide={onClose}
      className="sm-dialog"
    >
      <div className="sm-form">
        <p>{t('restaurantOrders.setCustomerHelp')}</p>
        {replacing && (
          <p className="sm-help">
            {t('restaurantOrders.currentCustomer', { name: order.customer_name ?? '' })}
          </p>
        )}
        <FormField id="order-late-customer" label={t('restaurantOrders.customer')} required>
          <CustomerPicker id="order-late-customer" value={customer} onChange={setCustomer} />
        </FormField>
        {replacing && (
          <FormField id="order-customer-reason" label={t('restaurantOrders.reason')} required>
            <InputTextarea
              id="order-customer-reason"
              rows={2}
              maxLength={500}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </FormField>
        )}
        {error && <Message severity="error" text={error} />}
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button
            type="button"
            label={t('actions.save')}
            disabled={customer === null || unchanged || (replacing && reason.trim() === '')}
            loading={associate.isPending}
            onClick={() => {
              if (customer === null) return;
              setError(null);
              associate.mutate(
                { id: order.id, customerId: customer.id, reason: reason.trim() || null },
                {
                  onSuccess: () => {
                    toast.success(t('restaurantOrders.customerSet'));
                    onClose();
                  },
                  onError: (failure) => setError(orderError(t, failure)),
                },
              );
            }}
          />
        </div>
      </div>
    </Dialog>
  );
}

/**
 * Changement ou retrait de la table (R5-B, B-2) : commande ouverte « sur place », motif
 * facultatif. Choisir une table exige la consultation des tables et le module ; le RETRAIT reste
 * possible sans eux (B4). Même table : rien n'est envoyé. Le serveur revérifie tout.
 */
function TableDialog({ order, onClose }: { order: Order; onClose: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const canChoose = useCanChooseTable();
  const [tableId, setTableId] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | null>(null);
  const { setTable } = useOrderMutations();
  const save = (next: string | null) => {
    setError(null);
    setTable.mutate(
      { id: order.id, tableId: next, reason: reason.trim() || null },
      {
        onSuccess: () => {
          toast.success(
            t(next === null ? 'restaurantOrders.tableRemoved' : 'restaurantOrders.tableSet'),
          );
          onClose();
        },
        onError: (failure) => setError(orderError(t, failure)),
      },
    );
  };
  return (
    <Dialog
      header={t(order.table ? 'restaurantOrders.changeTable' : 'restaurantOrders.setTable')}
      visible
      onHide={onClose}
      className="sm-dialog"
    >
      <div className="sm-form">
        {order.table && (
          <p className="sm-help">
            {t('restaurantOrders.currentTable', { label: tableLabel(order.table) })}
          </p>
        )}
        {canChoose ? (
          <FormField id="order-new-table" label={t('restaurantOrders.newTable')}>
            <TablePicker
              id="order-new-table"
              siteId={order.site_id}
              value={tableId}
              onChange={setTableId}
              excludeId={order.table_id}
            />
          </FormField>
        ) : (
          <Message severity="info" text={t('restaurantOrders.tablesUnavailableRemoveOnly')} />
        )}
        <FormField
          id="order-table-reason"
          label={t('restaurantOrders.reasonOptional')}
          help={t('restaurantOrders.tableReasonHelp')}
        >
          <InputTextarea
            id="order-table-reason"
            rows={2}
            maxLength={500}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </FormField>
        {error && <Message severity="error" text={error} />}
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          {order.table_id !== null && (
            <Button
              type="button"
              label={t('restaurantOrders.removeTable')}
              severity="danger"
              outlined
              loading={setTable.isPending && setTable.variables?.tableId === null}
              onClick={() => save(null)}
            />
          )}
          {canChoose && (
            <Button
              type="button"
              label={t('actions.save')}
              disabled={tableId === null || tableId === order.table_id}
              loading={setTable.isPending && setTable.variables?.tableId !== null}
              onClick={() => save(tableId)}
            />
          )}
        </div>
      </div>
    </Dialog>
  );
}

/** « T1 (Salle) » tel qu'enregistré dans l'évènement (libellés d'alors), sinon « sans table ». */
function eventTable(t: TFunction, label: unknown, zone: unknown): string {
  if (typeof label !== 'string') return t('restaurantOrders.noTable');
  return typeof zone === 'string' ? `${label} (${zone})` : label;
}

/** Historique de la commande (ajout seul) : évènement, auteur, heure, motif, vente. */
function OrderHistory({ order }: { order: Order }) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const { locale, timezone } = capabilities.tenant;
  const events = useOrderEvents(order.id);
  const lineNo = new Map((order.lines ?? []).map((l) => [l.id, l.line_no]));
  if (events.isPending) return <LoadingState />;
  return (
    <ol className="sm-timeline" aria-label={t('restaurantOrders.history')}>
      {(events.data ?? []).map((e) => {
        const lines = e.line_ids.map((id) => lineNo.get(id)).filter((n) => n !== undefined);
        const sale = typeof e.data.sale_number === 'string' ? e.data.sale_number : null;
        const customerName =
          e.event_type === 'CUSTOMER_SET' && typeof e.data.customer_name === 'string'
            ? e.data.customer_name
            : null;
        // Tables : libellés ENREGISTRÉS dans l'évènement (jamais le libellé actuel).
        const tableChange =
          e.event_type === 'TABLE_CHANGED'
            ? t('restaurantOrders.tableChange', {
                from: eventTable(t, e.data.previous_table_label, e.data.previous_zone_name),
                to: eventTable(t, e.data.table_label, e.data.zone_name),
              })
            : null;
        const initialTable =
          e.event_type === 'CREATED' && typeof e.data.table_label === 'string'
            ? t('restaurantOrders.tableNamed', {
                label: eventTable(t, e.data.table_label, e.data.zone_name),
              })
            : null;
        // Cuisine (R6) : priorité et poste ENREGISTRÉS dans l'évènement.
        const priorityChange =
          e.event_type === 'PRIORITY_CHANGED' && typeof e.data.priority === 'string'
            ? t('restaurantKitchen.priorityChange', {
                from: t(`restaurantKitchen.priorities.${String(e.data.previous)}`),
                to: t(`restaurantKitchen.priorities.${e.data.priority}`),
              })
            : null;
        const stationChange =
          e.event_type === 'LINES_ROUTED'
            ? t('restaurantKitchen.stationNamed', {
                name:
                  typeof e.data.station_name === 'string'
                    ? e.data.station_name
                    : t('restaurantKitchen.noStation'),
              })
            : null;
        return (
          <li key={e.id} data-testid="order-event">
            <strong>{t(`restaurantOrders.events.${e.event_type}`)}</strong>
            {lines.length > 0 && (
              <span>{` — ${t('restaurantOrders.lineNumbers', { lines: lines.join(', ') })}`}</span>
            )}
            {sale && <span>{` — ${t('restaurantOrders.saleNumber', { number: sale })}`}</span>}
            {customerName && (
              <span>{` — ${t('restaurantOrders.customerNamed', { name: customerName })}`}</span>
            )}
            {tableChange && <span>{` — ${tableChange}`}</span>}
            {initialTable && <span>{` — ${initialTable}`}</span>}
            {priorityChange && <span>{` — ${priorityChange}`}</span>}
            {stationChange && <span>{` — ${stationChange}`}</span>}
            <small className="sm-help">
              {` ${formatDateTime(e.occurred_at, locale, timezone)}${
                e.actor_name ? ` · ${e.actor_name}` : ''
              }`}
            </small>
            {e.reason && <p className="sm-help">{e.reason}</p>}
          </li>
        );
      })}
    </ol>
  );
}

/**
 * Détail d'une commande : numéro (affiché en grand après la création), ticket de retrait sans
 * prix, lignes aux prix FIGÉS et leurs actions (préparation, service, annulation motivée),
 * prise en charge et réattribution, règlement (T2 : vente validée et paiements, même dialogue
 * que les ventes), état financier LU sur la vente, historique. Chaque bouton n'est proposé
 * qu'avec la permission et dans l'état qui le permettent ; le serveur refait tous les contrôles.
 */
export default function OrderPage() {
  const { t } = useTranslation();
  const toast = useToast();
  const { id } = useParams();
  const [search] = useSearchParams();
  const { can, capabilities, hasModule } = useCapabilities();
  const canChooseTable = useCanChooseTable();
  const kitchen = useKitchenOnOrder();
  const { currency, locale, timezone } = capabilities.tenant;
  const money = (v: string) => formatMoney(v, currency, locale);
  const query = useOrder(id);
  const mutations = useOrderMutations();
  const [ticket, setTicket] = useState(false);
  const [adding, setAdding] = useState(false);
  const [reassigning, setReassigning] = useState(false);
  const [releasing, setReleasing] = useState(false);
  const [choosingCustomer, setChoosingCustomer] = useState(false);
  const [choosingTable, setChoosingTable] = useState(false);
  const [cancelling, setCancelling] = useState<OrderLine[] | 'order' | null>(null);
  const [settling, setSettling] = useState<string | null>(null);
  const [settleError, setSettleError] = useState<{
    message: string;
    overrideAllowed: boolean;
    expiredShortages: ExpiredShortage[];
  } | null>(null);
  const [receipt, setReceipt] = useState<string | null>(null);
  const [routing, setRouting] = useState<OrderLine[] | null>(null);
  const [prioritizing, setPrioritizing] = useState(false);

  if (query.isPending) return <LoadingState />;
  if (query.isError) return <Message severity="error" text={orderError(t, query.error)} />;
  const order = query.data;
  const lines = order.lines ?? [];
  const open = order.status === 'OPEN';
  const unsettled = order.settlement_status === 'UNSETTLED';
  const atOrderBlocked = order.payment_timing === 'AT_ORDER' && unsettled;
  const activeLines = lines.filter((l) => l.status !== 'CANCELLED');
  const canSettle = SETTLE_PERMISSIONS.every((p) => can(p));
  const mine = order.assigned_user_id === capabilities.user.id;
  // Même permission que le choix du client à la création ; recherche des clients : leur lecture.
  const canSetCustomer =
    open &&
    unsettled &&
    can(ORDER_CREATE) &&
    hasModule('customers') &&
    can('customers.customer.view');
  // Table (R5) : commande ouverte « sur place » et `order.create` ; choisir une table exige en
  // plus la consultation des tables, retirer la table actuelle reste toujours proposé (B4).
  const canEditTable =
    open &&
    can(ORDER_CREATE) &&
    order.service_mode === 'ON_SITE' &&
    (canChooseTable || order.table_id !== null);
  const notify = {
    onSuccess: () => toast.success(t('restaurantOrders.updated')),
    onError: (error: unknown) => toast.error(orderError(t, error)),
  };
  const transition = (action: TransitionAction, lineIds?: string[]) =>
    mutations.transition.mutate({ id: order.id, action, lineIds }, notify);
  const canCancelLine = (l: OrderLine) =>
    open &&
    unsettled &&
    ((l.status === 'RECEIVED' && can(ORDER_CANCEL)) ||
      ((l.status === 'IN_PREPARATION' || l.status === 'READY') && can(ORDER_CANCEL_PREPARED)));

  const lineActions = (l: OrderLine): RowAction[] => [
    {
      key: 'start',
      label: t('restaurantOrders.actions.start'),
      icon: 'pi pi-play',
      hidden: !(open && l.status === 'RECEIVED' && can(ORDER_PREPARE) && !atOrderBlocked),
      onClick: () => transition('start', [l.id]),
    },
    {
      key: 'ready',
      label: t('restaurantOrders.actions.ready'),
      icon: 'pi pi-check',
      hidden: !(open && l.status === 'IN_PREPARATION' && can(ORDER_PREPARE)),
      onClick: () => transition('ready', [l.id]),
    },
    {
      key: 'revert',
      label: t('restaurantOrders.actions.revert'),
      icon: 'pi pi-undo',
      hidden: !(open && l.status === 'READY' && can(ORDER_PREPARE)),
      onClick: () => transition('revert', [l.id]),
    },
    {
      key: 'serve',
      label: t(
        order.service_mode === 'ON_SITE'
          ? 'restaurantOrders.actions.serve'
          : 'restaurantOrders.actions.handOver',
      ),
      icon: 'pi pi-send',
      hidden: !(open && l.status === 'READY' && can(ORDER_SERVE)),
      onClick: () => transition('serve', [l.id]),
    },
    {
      key: 'route',
      label: t('restaurantKitchen.changeStation'),
      icon: 'pi pi-sitemap',
      hidden: !(open && kitchen.canRoute),
      onClick: () => setRouting([l]),
    },
    {
      key: 'cancel',
      label: t('restaurantOrders.actions.cancelLine'),
      icon: 'pi pi-times',
      danger: true,
      hidden: !canCancelLine(l),
      onClick: () => setCancelling([l]),
    },
  ];
  const any = (status: OrderLine['status']) => lines.some((l) => l.status === status);

  const confirmSettlement = (
    payments: ImmediatePayment[],
    creditOverride: CreditOverride | null,
    expiredLotOverride: ExpiredLotOverride | null = null,
  ) =>
    mutations.settle.mutate(
      {
        id: order.id,
        input: {
          payments,
          credit_override: creditOverride,
          expired_lot_override: expiredLotOverride,
          idempotency_key: settling ?? newKey(),
        },
      },
      {
        onSuccess: (result) => {
          setSettling(null);
          toast.success(
            t('restaurantOrders.settledToast', { number: result.order.sale_number ?? '' }),
          );
          if (can('sales.sale.view')) setReceipt(result.sale_id);
        },
        // Échec : RIEN n'a été enregistré (ni vente, ni paiement, ni stock) ; la commande reste
        // « à régler » et le dialogue reste ouvert pour corriger.
        onError: (error) =>
          setSettleError({
            message: saleError(t, error, locale, currency),
            overrideAllowed:
              error instanceof ApiError &&
              error.code === 'credit_limit_exceeded' &&
              error.extra.override_allowed === true,
            expiredShortages: expiredShortages(error),
          }),
      },
    );

  return (
    <>
      <PageHeader
        title={t('restaurantOrders.orderTitle', { number: order.daily_number })}
        breadcrumbs={[
          { label: t('restaurantOrders.title'), to: '/restaurant/orders' },
          { label: orderNumber(order) },
        ]}
        actions={
          <>
            <Button
              icon="pi pi-print"
              label={t('restaurantOrders.ticket.open')}
              outlined
              onClick={() => setTicket(true)}
            />
            {open && can(ORDER_CLAIM) && !mine && (
              <Button
                icon="pi pi-user-plus"
                label={t('restaurantOrders.actions.claim')}
                outlined
                loading={mutations.claim.isPending}
                onClick={() => mutations.claim.mutate(order.id, notify)}
              />
            )}
            {/* Seul le responsable libère ; le serveur revérifie (order_not_claimed_by_you). */}
            {open && can(ORDER_CLAIM) && mine && (
              <Button
                icon="pi pi-user-minus"
                label={t('restaurantOrders.release')}
                outlined
                onClick={() => setReleasing(true)}
              />
            )}
            {open && can(ORDER_REASSIGN) && (
              <Button
                icon="pi pi-users"
                label={t('restaurantOrders.reassign')}
                outlined
                onClick={() => setReassigning(true)}
              />
            )}
            {open && unsettled && can(ORDER_CREATE) && (
              <Button
                icon="pi pi-plus"
                label={t('restaurantOrders.addLines')}
                outlined
                onClick={() => setAdding(true)}
              />
            )}
            {open && unsettled && canSettle && activeLines.length > 0 && (
              <Button
                icon="pi pi-wallet"
                label={t('restaurantOrders.settle')}
                onClick={() => {
                  setSettleError(null);
                  setSettling(newKey());
                }}
              />
            )}
          </>
        }
      />
      {search.get('created') === '1' && (
        <div className="sm-order-created" role="status">
          <p>{t('restaurantOrders.createdBanner')}</p>
          <p className="sm-order-number-big" data-testid="order-number">
            {orderNumber(order)}
          </p>
          {order.call_name && <p>{order.call_name}</p>}
          <Button
            icon="pi pi-print"
            label={t('restaurantOrders.ticket.print')}
            onClick={() => setTicket(true)}
          />
        </div>
      )}
      <section className="sm-block sm-order-summary">
        <dl className="sm-details">
          <div>
            <dt>{t('restaurantOrders.number')}</dt>
            <dd className="sm-order-number">{orderNumber(order)}</dd>
          </div>
          <div>
            <dt>{t('restaurantOrders.state')}</dt>
            <dd className="sm-badges">
              <OrderStatusBadge status={order.status} />
              <PrepBadge status={order.prep_status} />
              <SettlementBadge order={order} />
            </dd>
          </div>
          {kitchen.effective && (
            <div>
              <dt>{t('restaurantKitchen.priority')}</dt>
              <dd className="sm-badges" data-testid="order-priority">
                {(order.priority ?? 'NORMAL') === 'NORMAL' ? (
                  t('restaurantKitchen.priorities.NORMAL')
                ) : (
                  <PriorityBadge priority={order.priority ?? 'NORMAL'} />
                )}
                {open && kitchen.canPrioritize && (
                  <Button
                    type="button"
                    icon="pi pi-sort-amount-up"
                    label={t('restaurantKitchen.changePriority')}
                    text
                    size="small"
                    onClick={() => setPrioritizing(true)}
                  />
                )}
              </dd>
            </div>
          )}
          <div>
            <dt>{t('restaurantOrders.serviceMode')}</dt>
            <dd>{t(`restaurantOrders.serviceModes.${order.service_mode}`)}</dd>
          </div>
          {order.call_name && (
            <div>
              <dt>{t('restaurantOrders.callName')}</dt>
              <dd>{order.call_name}</dd>
            </div>
          )}
          {(order.table || canEditTable) && (
            <div>
              <dt>{t('restaurantOrders.table')}</dt>
              <dd className="sm-badges" data-testid="order-table">
                {order.table ? tableLabel(order.table) : t('restaurantOrders.noTable')}
                {order.table && !order.table.is_active && (
                  <StatusBadge tone="warning" label={t('restaurantOrders.tableInactive')} />
                )}
                {canEditTable && (
                  <Button
                    type="button"
                    icon="pi pi-th-large"
                    label={t(
                      order.table ? 'restaurantOrders.changeTable' : 'restaurantOrders.setTable',
                    )}
                    text
                    size="small"
                    onClick={() => setChoosingTable(true)}
                  />
                )}
              </dd>
            </div>
          )}
          <div>
            <dt>{t('restaurantOrders.customer')}</dt>
            <dd>
              {order.customer_name ?? t('restaurantOrders.noCustomer')}
              {canSetCustomer && (
                <Button
                  type="button"
                  icon="pi pi-user-edit"
                  label={t(
                    order.customer_id === null
                      ? 'restaurantOrders.setCustomer'
                      : 'restaurantOrders.changeCustomer',
                  )}
                  text
                  size="small"
                  onClick={() => setChoosingCustomer(true)}
                />
              )}
            </dd>
          </div>
          <div>
            <dt>{t('restaurantOrders.assignee')}</dt>
            <dd>{order.assigned_name ?? t('restaurantOrders.unassigned')}</dd>
          </div>
          <div>
            <dt>{t('restaurantOrders.createdAt')}</dt>
            <dd>{formatDateTime(order.created_at, locale, timezone)}</dd>
          </div>
          <div>
            <dt>{t('restaurantOrders.paymentTiming')}</dt>
            <dd>{t(`restaurantOrders.paymentTimings.${order.payment_timing}`)}</dd>
          </div>
        </dl>
      </section>

      <section className="sm-block" aria-label={t('restaurantOrders.financial')}>
        <h2>{t('restaurantOrders.financial')}</h2>
        <dl className="sm-details">
          <div>
            <dt>{t('restaurantOrders.total')}</dt>
            <dd data-testid="order-total">{money(order.total)}</dd>
          </div>
          {order.sale_id ? (
            <>
              <div>
                <dt>{t('restaurantOrders.sale')}</dt>
                <dd>
                  {can('sales.sale.view') ? (
                    <Link to={`/sales/${order.sale_id}`}>{order.sale_number}</Link>
                  ) : (
                    order.sale_number
                  )}
                </dd>
              </div>
              <div>
                <dt>{t('restaurantOrders.paymentStatus')}</dt>
                <dd>
                  {order.payment_status && <SalePaymentBadge status={order.payment_status} />}
                </dd>
              </div>
              <div>
                <dt>{t('restaurantOrders.amountDue')}</dt>
                <dd data-testid="order-amount-due">{money(order.amount_due ?? '0')}</dd>
              </div>
            </>
          ) : (
            <div>
              <dt>{t('restaurantOrders.paymentStatus')}</dt>
              <dd>{t('restaurantOrders.settlement.UNSETTLED')}</dd>
            </div>
          )}
        </dl>
        {order.sale_id && can('sales.sale.view') && (
          <Button
            icon="pi pi-receipt"
            label={t('receipt.view')}
            text
            onClick={() => setReceipt(order.sale_id)}
          />
        )}
        {order.status === 'CLOSED' && (
          <Message severity="success" text={t('restaurantOrders.closedNotice')} />
        )}
        {open && !unsettled && (
          <Message severity="info" text={t('restaurantOrders.settledNotice')} />
        )}
        {atOrderBlocked && open && (
          <Message severity="warn" text={t('restaurantOrders.payFirstNotice')} />
        )}
        {open && unsettled && order.customer_id === null && order.line_counts.served > 0 && (
          <Message severity="warn" text={t('restaurantOrders.noCustomerCreditNotice')} />
        )}
        {order.status === 'CANCELLED' && order.cancel_reason && (
          <Message
            severity="warn"
            text={t('restaurantOrders.cancelledNotice', { reason: order.cancel_reason })}
          />
        )}
      </section>

      <section className="sm-block" aria-label={t('restaurantOrders.lines')}>
        <div className="sm-section-header">
          <h2>{t('restaurantOrders.lines')}</h2>
          {open && (
            <div className="sm-toolbar">
              {any('RECEIVED') && can(ORDER_PREPARE) && !atOrderBlocked && (
                <Button
                  label={t('restaurantOrders.actions.startAll')}
                  size="small"
                  outlined
                  onClick={() => transition('start')}
                />
              )}
              {any('IN_PREPARATION') && can(ORDER_PREPARE) && (
                <Button
                  label={t('restaurantOrders.actions.readyAll')}
                  size="small"
                  outlined
                  onClick={() => transition('ready')}
                />
              )}
              {any('READY') && can(ORDER_SERVE) && (
                <Button
                  label={t(
                    order.service_mode === 'ON_SITE'
                      ? 'restaurantOrders.actions.serveAll'
                      : 'restaurantOrders.actions.handOverAll',
                  )}
                  size="small"
                  onClick={() => transition('serve')}
                />
              )}
              {unsettled && can(ORDER_CANCEL) && !any('SERVED') && (
                <Button
                  label={t('restaurantOrders.cancelOrder')}
                  size="small"
                  severity="danger"
                  text
                  onClick={() => setCancelling('order')}
                />
              )}
            </div>
          )}
        </div>
        <DataTable value={lines} dataKey="id" size="small" className="sm-table">
          <Column field="line_no" header="#" />
          <Column
            header={t('restaurantOrders.item')}
            body={(l: OrderLine) => (
              <div className="sm-stack-xs">
                <span>{l.packaging_name ? `${l.label} — ${l.packaging_name}` : l.label}</span>
                {l.note && <small className="sm-help">{l.note}</small>}
                {l.cancel_reason && <small className="sm-help">{l.cancel_reason}</small>}
              </div>
            )}
          />
          <Column
            header={t('restaurantOrders.quantity')}
            headerClassName="sm-num"
            bodyClassName="sm-num"
            body={(l: OrderLine) => formatQuantity(l.quantity, locale)}
          />
          <Column
            header={t('restaurantOrders.frozenPrice')}
            headerClassName="sm-num"
            bodyClassName="sm-num"
            body={(l: OrderLine) => money(l.unit_price)}
          />
          <Column
            header={t('restaurantOrders.lineTotal')}
            headerClassName="sm-num"
            bodyClassName="sm-num"
            body={(l: OrderLine) => money(l.line_total)}
          />
          {(kitchen.effective || lines.some((l) => l.station_name)) && (
            <Column
              header={t('restaurantKitchen.station')}
              body={(l: OrderLine) => l.station_name ?? t('restaurantKitchen.noStation')}
            />
          )}
          <Column
            header={t('restaurantOrders.state')}
            body={(l: OrderLine) => <LineStatusBadge status={l.status} order={order} />}
          />
          {open && (
            <Column
              header={t('common.actions')}
              body={(l: OrderLine) =>
                OPEN_LINE(l) ? <RowActions actions={lineActions(l)} /> : null
              }
            />
          )}
        </DataTable>
      </section>

      <section className="sm-block" aria-label={t('restaurantOrders.history')}>
        <h2>{t('restaurantOrders.history')}</h2>
        <OrderHistory order={order} />
      </section>

      {ticket && <OrderTicketDialog orderId={order.id} onClose={() => setTicket(false)} />}
      {adding && <AddLinesDialog order={order} onClose={() => setAdding(false)} />}
      {reassigning && <ReassignDialog order={order} onClose={() => setReassigning(false)} />}
      {releasing && <ReleaseDialog order={order} onClose={() => setReleasing(false)} />}
      {choosingCustomer && (
        <CustomerDialog order={order} onClose={() => setChoosingCustomer(false)} />
      )}
      {choosingTable && <TableDialog order={order} onClose={() => setChoosingTable(false)} />}
      {routing && (
        <RouteLinesDialog order={order} lines={routing} onClose={() => setRouting(null)} />
      )}
      {prioritizing && <PriorityDialog order={order} onClose={() => setPrioritizing(false)} />}
      {cancelling !== null && (
        <ReasonDialog
          header={t(
            cancelling === 'order'
              ? 'restaurantOrders.cancelOrder'
              : 'restaurantOrders.actions.cancelLine',
          )}
          message={
            cancelling === 'order'
              ? t('restaurantOrders.cancelOrderConfirm', { number: order.daily_number })
              : t('restaurantOrders.cancelLineConfirm', { name: cancelling[0]?.label ?? '' })
          }
          confirmLabel={t('restaurantOrders.confirmCancel')}
          pending={mutations.cancel.isPending || mutations.cancelLines.isPending}
          onClose={() => setCancelling(null)}
          onConfirm={(reason) => {
            const done = {
              onSuccess: () => {
                toast.success(t('restaurantOrders.updated'));
                setCancelling(null);
              },
              onError: (error: unknown) => toast.error(orderError(t, error)),
            };
            if (cancelling === 'order') {
              mutations.cancel.mutate({ id: order.id, reason }, done);
            } else {
              mutations.cancelLines.mutate(
                { id: order.id, lineIds: cancelling.map((l) => l.id), reason },
                done,
              );
            }
          }}
        />
      )}
      {settling !== null && (
        <ValidateSaleDialog
          total={order.total}
          siteId={order.site_id}
          hasCustomer={order.customer_id !== null}
          pending={mutations.settle.isPending}
          error={settleError?.message ?? null}
          overrideAllowed={settleError?.overrideAllowed ?? false}
          expiredShortages={settleError?.expiredShortages ?? []}
          onConfirm={confirmSettlement}
          onClose={() => setSettling(null)}
        />
      )}
      {receipt && <ReceiptDialog saleId={receipt} onClose={() => setReceipt(null)} />}
    </>
  );
}
