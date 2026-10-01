import { Button } from 'primereact/button';
import { Dialog } from 'primereact/dialog';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { SalePaymentBadge, soldQuantity } from '@/modules/sales/ui';
import { formatMoney } from '@/shared/lib/decimal';
import { formatDateTime } from '@/shared/lib/format';

import type { CheckoutResult } from './api';

/**
 * Confirmation après encaissement : données renvoyées par le serveur (numéro, lignes, total,
 * paiements, reste dû). Point d'extension du futur ticket / reçu (impression, PDF) : il
 * consommera le même `CheckoutResult`.
 */
export function PosReceipt({
  result,
  onNewSale,
  onOpenSale,
}: {
  result: CheckoutResult;
  onNewSale: () => void;
  onOpenSale?: () => void;
}) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const { currency, locale, timezone } = capabilities.tenant;
  const { sale, payments } = result;
  const money = (v: string) => formatMoney(v, currency, locale);

  return (
    <Dialog
      header={t('pos.saleRecorded', { number: sale.number ?? '' })}
      visible
      onHide={onNewSale}
      className="sm-dialog"
    >
      <div className="sm-form" data-testid="pos-receipt">
        <p className="sm-muted">
          {[
            formatDateTime(sale.validated_at ?? sale.created_at, locale, timezone),
            sale.site_name,
            sale.customer_name ?? t('sales.anonymousShort'),
          ].join(' · ')}
        </p>
        <table className="sm-pos-receipt">
          <tbody>
            {sale.lines.map((line) => (
              <tr key={line.id} data-testid="receipt-line">
                {/* Lot 3-B : présentation vendue (« 2 Carton 24 × 10 500 F »), prix figé. */}
                <td>
                  <span>{line.article_designation}</span>
                  <br />
                  <span className="sm-muted">
                    {`${soldQuantity(line, locale)} × ${money(line.unit_price)}`}
                  </span>
                </td>
                <td className="sm-num">{money(line.line_total)}</td>
              </tr>
            ))}
            <tr className="sm-strong">
              <td>{t('pos.total')}</td>
              <td className="sm-num">{money(sale.total)}</td>
            </tr>
            {payments.map((p) => (
              <tr key={p.id}>
                <td>{p.method_label}</td>
                <td className="sm-num">{money(p.amount)}</td>
              </tr>
            ))}
            {payments
              .filter((p) => p.amount_received !== null)
              .map((p) => (
                <tr key={`${p.id}-cash`}>
                  <td>
                    {t('pos.receivedAndChange', { received: money(p.amount_received ?? '0') })}
                  </td>
                  <td className="sm-num" data-testid="receipt-change">
                    {money(p.change_given ?? '0')}
                  </td>
                </tr>
              ))}
            <tr>
              <td>{t('pos.remaining')}</td>
              <td className="sm-num" data-testid="receipt-remaining">
                {money(sale.remaining_amount ?? '0')}
              </td>
            </tr>
          </tbody>
        </table>
        {sale.payment_status && <SalePaymentBadge status={sale.payment_status} />}
        <div className="sm-dialog-actions">
          {onOpenSale && <Button type="button" label={t('sales.open')} text onClick={onOpenSale} />}
          <Button
            type="button"
            icon="pi pi-plus"
            label={t('pos.newSale')}
            onClick={onNewSale}
            autoFocus
          />
        </div>
      </div>
    </Dialog>
  );
}
