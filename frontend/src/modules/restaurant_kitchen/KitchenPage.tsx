import { Button } from 'primereact/button';
import { Dropdown } from 'primereact/dropdown';
import { Message } from 'primereact/message';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import {
  ORDER_PREPARE,
  orderNumber,
  useOrderMutations,
  type TransitionAction,
} from '@/modules/restaurant_orders/api';
import { orderError } from '@/modules/restaurant_orders/ui';
import {
  RecipeSheetDialog,
  useCanSeeSheets,
  type SheetTarget,
} from '@/modules/restaurant_recipes/RecipeSheetDialog';
import { formatQuantity } from '@/shared/lib/decimal';
import { translateError } from '@/shared/lib/errors';
import { LoadingState } from '@/shared/ui/LoadingState';
import { PageHeader } from '@/shared/ui/PageHeader';
import { StatusBadge } from '@/shared/ui/StatusBadge';
import { useToast } from '@/shared/ui/toast';

import {
  PRIORITIES,
  PRIORITY_SET,
  TICKET_STATES,
  useKitchenBoard,
  useKitchenMutations,
  type Priority,
  type StationFilter,
  type Ticket,
} from './api';
import { SiteDropdown, useDefaultSite } from './sites';
import { PriorityBadge } from './ui';

/**
 * Écran cuisine (palier R6, ADR-0050 K4) : tickets (commande × poste) en trois colonnes, classés
 * par le SERVEUR (priorité décroissante, puis ancienneté). Préparer reste l'action des commandes
 * (`restaurant.orders.order.prepare`) ; servir reste au service (écran des commandes).
 */

const FILTER_KEY = 'sm.kitchen.station';

function rememberedFilter(): StationFilter {
  try {
    return localStorage.getItem(FILTER_KEY) ?? 'all';
  } catch {
    return 'all';
  }
}

function remember(filter: StationFilter): void {
  try {
    localStorage.setItem(FILTER_KEY, filter);
  } catch {
    // Stockage indisponible (navigation privée) : filtre non mémorisé.
  }
}

/** Heure locale de l'entreprise (fuseau du tenant, ADR-0028). */
function formatTime(iso: string, locale: string, timeZone: string): string {
  return new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit', timeZone }).format(
    new Date(iso),
  );
}

/** Horloge de l'écran : le temps écoulé se met à jour sans requête. */
function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}

function TicketCard({ ticket, now }: { ticket: Ticket; now: number }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { capabilities, can } = useCapabilities();
  const { locale, timezone } = capabilities.tenant;
  const { transition } = useOrderMutations();
  const { setPriority } = useKitchenMutations();
  const canPrepare = can(ORDER_PREPARE) && !ticket.awaiting_payment;
  // Palier R8 (ADR-0053, D13) : fiche technique d'une ligne (recettes effectives et consultables).
  const canSeeSheets = useCanSeeSheets();
  const [sheet, setSheet] = useState<SheetTarget | null>(null);
  const elapsed = Math.max(0, Math.floor((now - new Date(ticket.received_at).getTime()) / 60_000));
  const ids = (status: string) => ticket.lines.filter((l) => l.status === status).map((l) => l.id);
  const act = (action: TransitionAction, lineIds: string[]) =>
    transition.mutate(
      { id: ticket.order_id, action, lineIds },
      { onError: (error) => toast.error(orderError(t, error)) },
    );
  const where =
    ticket.service_mode === 'ON_SITE' && ticket.table_label
      ? t('restaurantKitchen.table', { label: ticket.table_label })
      : t(`restaurantOrders.serviceModes.${ticket.service_mode}`);
  const toStart = ids('RECEIVED');
  const toFinish = ids('IN_PREPARATION');
  return (
    <li
      className={`sm-kitchen-ticket sm-kitchen-priority-${ticket.priority.toLowerCase()}`}
      data-testid="kitchen-ticket"
      aria-label={t('restaurantKitchen.ticketOf', {
        number: orderNumber(ticket),
        station: ticket.station_name ?? t('restaurantKitchen.noStation'),
      })}
    >
      <div className="sm-kitchen-ticket-head">
        <Link to={`/restaurant/orders/${ticket.order_id}`} className="sm-order-number">
          {orderNumber(ticket)}
        </Link>
        <PriorityBadge priority={ticket.priority} />
      </div>
      <div className="sm-kitchen-ticket-meta">
        <strong>{where}</strong>
        {ticket.call_name && <span>{ticket.call_name}</span>}
        <span>
          <i className="pi pi-inbox" aria-hidden />{' '}
          {ticket.station_name ?? t('restaurantKitchen.noStation')}
        </span>
        <span>
          {t('restaurantKitchen.received', {
            time: formatTime(ticket.received_at, locale, timezone),
          })}{' '}
          ·{' '}
          <span data-testid="ticket-elapsed">
            {t('restaurantKitchen.elapsed', { count: elapsed })}
          </span>
        </span>
        <span>
          <i className="pi pi-user" aria-hidden />{' '}
          {ticket.assigned_name ?? t('restaurantOrders.unassigned')}
        </span>
      </div>
      <ul className="sm-kitchen-lines">
        {ticket.lines.map((line) => (
          <li
            key={line.id}
            className={`sm-kitchen-line sm-kitchen-line-${line.status.toLowerCase()}`}
          >
            <span className="sm-kitchen-qty">{formatQuantity(line.quantity, locale)} ×</span>
            <span className="sm-kitchen-label">
              {line.label}
              {line.packaging_name && <small className="sm-help"> ({line.packaging_name})</small>}
              {line.note && <small className="sm-kitchen-note">{line.note}</small>}
            </span>
            <StatusBadge
              tone={
                line.status === 'READY'
                  ? 'success'
                  : line.status === 'RECEIVED'
                    ? 'neutral'
                    : 'info'
              }
              label={t(`restaurantOrders.lineStatus.${line.status}`)}
            />
            {canSeeSheets && line.article_id && (
              <Button
                type="button"
                icon="pi pi-list-check"
                text
                rounded
                aria-label={t('restaurantKitchen.sheetOf', { name: line.label })}
                tooltip={t('restaurantRecipes.sheet')}
                onClick={() =>
                  setSheet({
                    articleId: line.article_id ?? '',
                    packagingId: line.packaging_id ?? null,
                    quantity: line.quantity,
                    label: line.packaging_name
                      ? `${line.label} (${line.packaging_name})`
                      : line.label,
                  })
                }
              />
            )}
            {canPrepare && line.status === 'READY' && (
              <Button
                type="button"
                icon="pi pi-undo"
                text
                rounded
                aria-label={t('restaurantKitchen.revertLine', { name: line.label })}
                tooltip={t('restaurantOrders.actions.revert')}
                onClick={() => act('revert', [line.id])}
              />
            )}
          </li>
        ))}
      </ul>
      {sheet && <RecipeSheetDialog target={sheet} onClose={() => setSheet(null)} />}
      {ticket.awaiting_payment && (
        <Message severity="warn" text={t('restaurantKitchen.awaitingPayment')} />
      )}
      <div className="sm-kitchen-ticket-actions">
        {canPrepare && toStart.length > 0 && (
          <Button
            type="button"
            icon="pi pi-play"
            label={t('restaurantOrders.actions.start')}
            loading={transition.isPending}
            onClick={() => act('start', toStart)}
          />
        )}
        {canPrepare && toFinish.length > 0 && (
          <Button
            type="button"
            icon="pi pi-check"
            severity="success"
            label={t('restaurantOrders.actions.ready')}
            loading={transition.isPending}
            onClick={() => act('ready', toFinish)}
          />
        )}
        {can(PRIORITY_SET) && (
          <Dropdown
            value={ticket.priority}
            options={PRIORITIES.map((p) => ({
              value: p,
              label: t(`restaurantKitchen.priorities.${p}`),
            }))}
            onChange={(e) =>
              setPriority.mutate(
                { id: ticket.order_id, priority: e.value as Priority },
                {
                  onSuccess: () => toast.success(t('restaurantKitchen.prioritySaved')),
                  onError: (error) => toast.error(translateError(t, error)),
                },
              )
            }
            aria-label={t('restaurantKitchen.priorityOf', { number: orderNumber(ticket) })}
            className="sm-kitchen-priority"
          />
        )}
      </div>
    </li>
  );
}

export default function KitchenPage() {
  const { t } = useTranslation();
  const { siteId: defaultSite, chooseSite } = useDefaultSite();
  const [site, setSite] = useState<string | null>(defaultSite);
  const [filter, setFilter] = useState<StationFilter>(rememberedFilter);
  const board = useKitchenBoard(site, filter);
  const now = useNow();
  const stations = board.data?.stations ?? [];
  const known = filter === 'all' || filter === 'none' || stations.some((s) => s.id === filter);
  return (
    <>
      <PageHeader
        title={t('restaurantKitchen.title')}
        description={t('restaurantKitchen.subtitle')}
      />
      <div className="sm-kitchen-toolbar">
        {chooseSite && (
          <div className="sm-field">
            <label htmlFor="kitchen-site">{t('layout.site')}</label>
            <SiteDropdown id="kitchen-site" value={site} onChange={setSite} />
          </div>
        )}
        <div className="sm-field">
          <label htmlFor="kitchen-station">{t('restaurantKitchen.station')}</label>
          <Dropdown
            inputId="kitchen-station"
            value={known ? filter : 'all'}
            options={[
              { value: 'all', label: t('restaurantKitchen.allStations') },
              ...stations.map((s) => ({
                value: s.id,
                label: s.is_active
                  ? s.name
                  : t('restaurantKitchen.inactiveStation', { name: s.name }),
              })),
              { value: 'none', label: t('restaurantKitchen.noStation') },
            ]}
            onChange={(e) => {
              const value = e.value as StationFilter;
              setFilter(value);
              remember(value);
            }}
          />
        </div>
        {board.data && (
          <div className="sm-badges" role="status" aria-label={t('restaurantKitchen.counts')}>
            {TICKET_STATES.map((state) => (
              <StatusBadge
                key={state}
                label={t(`restaurantKitchen.count.${state}`, {
                  count:
                    state === 'RECEIVED'
                      ? board.data.counts.received
                      : state === 'IN_PREPARATION'
                        ? board.data.counts.in_preparation
                        : board.data.counts.ready,
                })}
              />
            ))}
          </div>
        )}
      </div>
      {site === null ? (
        <Message severity="info" text={t('restaurantKitchen.chooseSite')} />
      ) : board.isError ? (
        <Message severity="error" text={translateError(t, board.error)} />
      ) : board.isPending ? (
        <LoadingState />
      ) : (
        <div className="sm-kitchen-board">
          {TICKET_STATES.map((state) => {
            const tickets = board.data.tickets.filter((ticket) => ticket.state === state);
            return (
              <section
                key={state}
                className="sm-kitchen-column"
                aria-label={t(`restaurantKitchen.columns.${state}`)}
              >
                <h2>
                  {t(`restaurantKitchen.columns.${state}`)}{' '}
                  <span className="sm-help">({tickets.length})</span>
                </h2>
                {tickets.length === 0 ? (
                  <p className="sm-help">{t('restaurantKitchen.emptyColumn')}</p>
                ) : (
                  <ul>
                    {tickets.map((ticket) => (
                      <TicketCard
                        key={`${ticket.order_id}:${ticket.station_id ?? 'none'}`}
                        ticket={ticket}
                        now={now}
                      />
                    ))}
                  </ul>
                )}
              </section>
            );
          })}
        </div>
      )}
    </>
  );
}
