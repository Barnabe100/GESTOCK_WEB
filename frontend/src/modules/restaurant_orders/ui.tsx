import type { TFunction } from 'i18next';
import { Button } from 'primereact/button';
import { InputText } from 'primereact/inputtext';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { ApiError } from '@/core/api/client';
import { itemLabel, useMenuItems, type MenuItem } from '@/modules/restaurant_menu/api';
import { formatMoney, multiplyMoney, normalizeDecimal, sumMoney } from '@/shared/lib/decimal';
import { translateError } from '@/shared/lib/errors';
import { LoadingState } from '@/shared/ui/LoadingState';
import { StatusBadge, type Tone } from '@/shared/ui/StatusBadge';

import type { LineStatus, Order, OrderLineInput, OrderStatus, PrepStatus } from './api';

const ORDER_TONES: Record<OrderStatus, Tone> = {
  PENDING_CONFIRMATION: 'info',
  OPEN: 'info',
  CLOSED: 'success',
  CANCELLED: 'danger',
  REJECTED: 'danger',
};

const PREP_TONES: Record<PrepStatus, Tone> = {
  RECEIVED: 'neutral',
  IN_PREPARATION: 'warning',
  READY: 'success',
  SERVED: 'info',
  NONE: 'neutral',
};

const LINE_TONES: Record<LineStatus, Tone> = {
  RECEIVED: 'neutral',
  IN_PREPARATION: 'warning',
  READY: 'success',
  SERVED: 'info',
  CANCELLED: 'danger',
};

export function OrderStatusBadge({ status }: { status: OrderStatus }) {
  const { t } = useTranslation();
  return <StatusBadge tone={ORDER_TONES[status]} label={t(`restaurantOrders.status.${status}`)} />;
}

export function PrepBadge({ status }: { status: PrepStatus }) {
  const { t } = useTranslation();
  return <StatusBadge tone={PREP_TONES[status]} label={t(`restaurantOrders.prep.${status}`)} />;
}

/** « Servie » sur place, « Remise » au comptoir et à emporter (un seul état technique). */
export function LineStatusBadge({ status, order }: { status: LineStatus; order: Order }) {
  const { t } = useTranslation();
  const key = status === 'SERVED' && order.service_mode !== 'ON_SITE' ? 'HANDED_OVER' : status;
  return <StatusBadge tone={LINE_TONES[status]} label={t(`restaurantOrders.lineStatus.${key}`)} />;
}

export function SettlementBadge({ order }: { order: Order }) {
  const { t } = useTranslation();
  return order.settlement_status === 'SETTLED' ? (
    <StatusBadge tone="success" label={t('restaurantOrders.settlement.SETTLED')} />
  ) : (
    <StatusBadge tone="warning" label={t('restaurantOrders.settlement.UNSETTLED')} />
  );
}

/**
 * Refus du serveur traduit ; « non commandable » détaillé (éléments et motifs du menu) et
 * protection d'une commande prise (responsable nommé).
 */
export function orderError(t: TFunction, error: unknown): string {
  if (error instanceof ApiError && error.code === 'menu_item_not_orderable') {
    const items = Array.isArray(error.extra.items)
      ? (error.extra.items as { label?: string; blockers?: string[] }[])
          .map(
            (i) =>
              `${i.label ?? '?'} (${(i.blockers ?? [])
                .map((b) => t(`restaurantMenu.blockers.${b}`))
                .join(', ')})`,
          )
          .join(' ; ')
      : '';
    return t('errors:menu_item_not_orderable', { items });
  }
  return translateError(t, error);
}

// --- Saisie des lignes depuis le menu du site -------------------------------------------------------

export interface CartLine {
  item: MenuItem;
  quantity: string;
  note: string;
}

export function cartTotal(lines: CartLine[]): string {
  return sumMoney(lines.map((l) => multiplyMoney(l.quantity || '0', l.item.price ?? '0')));
}

export function toLineInputs(lines: CartLine[]): OrderLineInput[] {
  return lines.map((l) => ({
    menu_item_id: l.item.id,
    quantity: l.quantity,
    note: l.note.trim() || null,
  }));
}

/**
 * Éléments COMMANDABLES du menu du site (calculé par le serveur), regroupés par section ; un
 * clic ajoute une ligne (ou une unité à la ligne existante). Prix du catalogue indicatifs : le
 * serveur fige le prix à l'enregistrement et revérifie tout.
 */
export function MenuCart({
  siteId,
  lines,
  onChange,
}: {
  siteId: string | null;
  lines: CartLine[];
  onChange: (lines: CartLine[]) => void;
}) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const { currency, locale } = capabilities.tenant;
  const query = new URLSearchParams({
    status: 'active',
    availability: 'available',
    limit: '100',
  });
  if (siteId) query.set('site_id', siteId);
  const items = useMenuItems(query.toString(), siteId !== null);
  const orderable = (items.data?.items ?? []).filter((i) => i.orderable && i.price !== null);
  const sections = [...new Set(orderable.map((i) => i.section_name))];
  const money = (v: string) => formatMoney(v, currency, locale);

  const add = (item: MenuItem) => {
    const index = lines.findIndex((l) => l.item.id === item.id && l.note === '');
    if (index >= 0) {
      const next = [...lines];
      const current = next[index] as CartLine;
      next[index] = { ...current, quantity: String(Number(current.quantity || '0') + 1) };
      onChange(next);
    } else {
      onChange([...lines, { item, quantity: '1', note: '' }]);
    }
  };
  const update = (index: number, patch: Partial<CartLine>) =>
    onChange(lines.map((l, i) => (i === index ? { ...l, ...patch } : l)));

  if (siteId === null) return <p className="sm-help">{t('restaurantOrders.chooseSiteFirst')}</p>;
  return (
    <div className="sm-order-cart">
      <section className="sm-order-menu" aria-label={t('restaurantOrders.menu')}>
        {items.isPending ? (
          <LoadingState />
        ) : orderable.length === 0 ? (
          <p className="sm-help">{t('restaurantOrders.noOrderableItem')}</p>
        ) : (
          sections.map((section) => (
            <div key={section} className="sm-order-menu-section">
              <h3>{section}</h3>
              <div className="sm-order-menu-items">
                {orderable
                  .filter((i) => i.section_name === section)
                  .map((i) => (
                    <Button
                      key={i.id}
                      type="button"
                      outlined
                      className="sm-order-menu-item"
                      aria-label={t('restaurantOrders.addItem', { name: itemLabel(i) })}
                      onClick={() => add(i)}
                    >
                      <span>{itemLabel(i)}</span>
                      <small>{money(i.price ?? '0')}</small>
                    </Button>
                  ))}
              </div>
            </div>
          ))
        )}
      </section>
      <section className="sm-order-lines" aria-label={t('restaurantOrders.lines')}>
        {lines.length === 0 ? (
          <p className="sm-help">{t('restaurantOrders.emptyCart')}</p>
        ) : (
          <ul>
            {lines.map((line, index) => (
              <li key={`${line.item.id}-${index}`} data-testid="cart-line">
                <div className="sm-order-line-head">
                  <span>{itemLabel(line.item)}</span>
                  <Button
                    type="button"
                    icon="pi pi-times"
                    text
                    rounded
                    severity="danger"
                    aria-label={t('restaurantOrders.removeLine', { name: itemLabel(line.item) })}
                    onClick={() => onChange(lines.filter((_, i) => i !== index))}
                  />
                </div>
                <div className="sm-order-line-fields">
                  <InputText
                    value={line.quantity}
                    inputMode="decimal"
                    aria-label={t('restaurantOrders.quantityOf', { name: itemLabel(line.item) })}
                    onChange={(e) => {
                      const value = normalizeDecimal(e.target.value, 3);
                      if (value !== null) update(index, { quantity: value });
                    }}
                  />
                  <InputText
                    value={line.note}
                    maxLength={200}
                    placeholder={t('restaurantOrders.notePlaceholder')}
                    aria-label={t('restaurantOrders.noteOf', { name: itemLabel(line.item) })}
                    onChange={(e) => update(index, { note: e.target.value })}
                  />
                </div>
              </li>
            ))}
          </ul>
        )}
        <p className="sm-total">
          {t('restaurantOrders.indicativeTotal', { total: money(cartTotal(lines)) })}
        </p>
      </section>
    </div>
  );
}
