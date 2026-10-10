import { useQuery } from '@tanstack/react-query';
import { Button } from 'primereact/button';
import { Dialog } from 'primereact/dialog';
import { Message } from 'primereact/message';
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatQuantity } from '@/shared/lib/decimal';
import { translateError } from '@/shared/lib/errors';
import { formatDateTime } from '@/shared/lib/format';
import { LoadingState } from '@/shared/ui/LoadingState';

import { fetchTicket, type OrderTicket } from './api';

/**
 * Ticket de retrait (D14, Q3) : numéro de commande, nom d'appel, mode de service et lignes —
 * AUCUN prix, coût ni stock (contenu construit par le serveur). Composant PROPRE au module
 * (N2) : le reçu de vente, son impression et le point de vente ne sont pas modifiés. Même
 * principe d'impression : rendu hors de l'application (`#sm-print-root`), règle `@page` 80 mm
 * injectée le temps de l'impression ; réimpression libre, non journalisée en V1.
 */

const PAGE_RULE = '@page { size: 80mm auto; margin: 0; }';

export function OrderTicketView({ ticket }: { ticket: OrderTicket }) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const { locale } = capabilities.tenant;
  return (
    <article
      className="sm-receipt sm-receipt--thermal-80"
      aria-label={t('restaurantOrders.ticket.title')}
    >
      <header className="sm-receipt-header">
        <p className="sm-receipt-issuer">{ticket.company_name}</p>
        <p>{ticket.site_name}</p>
      </header>
      <p className="sm-ticket-number" data-testid="ticket-number">
        {t('restaurantOrders.numberLabel', { number: ticket.daily_number })}
      </p>
      {ticket.call_name && <p className="sm-ticket-call-name">{ticket.call_name}</p>}
      <dl className="sm-receipt-meta">
        <div>
          <dt>{t('restaurantOrders.serviceMode')}</dt>
          <dd>{t(`restaurantOrders.serviceModes.${ticket.service_mode}`)}</dd>
        </div>
        <div>
          <dt>{t('restaurantOrders.ticket.date')}</dt>
          <dd>{formatDateTime(ticket.created_at, locale, ticket.timezone)}</dd>
        </div>
      </dl>
      <ul className="sm-receipt-lines" aria-label={t('restaurantOrders.lines')}>
        {ticket.lines.map((line, index) => (
          <li key={index} data-testid="ticket-line">
            <span className="sm-receipt-row">
              <span className="sm-receipt-designation">
                {line.packaging_name ? `${line.label} — ${line.packaging_name}` : line.label}
              </span>
              <span className="sm-num">{`× ${formatQuantity(line.quantity, locale)}`}</span>
            </span>
            {line.note && <small>{line.note}</small>}
          </li>
        ))}
      </ul>
      <p className="sm-receipt-thanks">{t('restaurantOrders.ticket.footer')}</p>
    </article>
  );
}

/** Impression du navigateur limitée au ticket, puis nettoyage (une impression par montage). */
export function OrderTicketPrinter({
  ticket,
  onDone,
}: {
  ticket: OrderTicket;
  onDone: () => void;
}) {
  const printed = useRef(false);
  useEffect(() => {
    if (printed.current) return;
    printed.current = true;
    const page = document.createElement('style');
    page.dataset.orderTicket = 'THERMAL_80';
    page.textContent = PAGE_RULE;
    document.head.append(page);
    document.body.classList.add('sm-printing');
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      window.removeEventListener('afterprint', finish);
      page.remove();
      document.body.classList.remove('sm-printing');
      onDone();
    };
    window.addEventListener('afterprint', finish, { once: true });
    window.print();
    finish();
  }, [onDone]);
  return createPortal(
    <div id="sm-print-root">
      <OrderTicketView ticket={ticket} />
    </div>,
    document.body,
  );
}

/** Aperçu du ticket de retrait, avec impression (et réimpression) par le navigateur. */
export function OrderTicketDialog({ orderId, onClose }: { orderId: string; onClose: () => void }) {
  const { t } = useTranslation();
  const [printing, setPrinting] = useState(false);
  const ticket = useQuery({
    queryKey: ['restaurant.orders', 'ticket', orderId],
    queryFn: () => fetchTicket(orderId),
  });
  return (
    <Dialog
      header={t('restaurantOrders.ticket.title')}
      visible
      onHide={onClose}
      className="sm-dialog"
    >
      {ticket.isPending ? (
        <LoadingState />
      ) : ticket.isError ? (
        <Message severity="error" text={translateError(t, ticket.error)} />
      ) : (
        <>
          <div className="sm-receipt-preview">
            <OrderTicketView ticket={ticket.data} />
          </div>
          <p className="sm-help">{t('restaurantOrders.ticket.noPrices')}</p>
          <div className="sm-dialog-actions">
            <Button type="button" label={t('actions.close')} text onClick={onClose} />
            <Button
              type="button"
              icon="pi pi-print"
              label={t('restaurantOrders.ticket.print')}
              onClick={() => setPrinting(true)}
            />
          </div>
          {printing && (
            <OrderTicketPrinter ticket={ticket.data} onDone={() => setPrinting(false)} />
          )}
        </>
      )}
    </Dialog>
  );
}
