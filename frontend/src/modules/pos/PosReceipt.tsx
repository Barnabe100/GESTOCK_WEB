import { Button } from 'primereact/button';
import { Dialog } from 'primereact/dialog';
import { Message } from 'primereact/message';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { RECEIPT_PRINT } from '@/modules/sales/receipt/api';
import { ReceiptDialog, useReceiptPrinting } from '@/modules/sales/receipt/ReceiptDialog';
import { SalePaymentBadge, soldQuantity } from '@/modules/sales/ui';
import { LineLotsList } from '@/modules/stock/ui';
import { formatMoney, sumMoney } from '@/shared/lib/decimal';
import { formatDateTime } from '@/shared/lib/format';

import type { CheckoutResult } from './api';

/**
 * Confirmation après encaissement : données renvoyées par le serveur (numéro, lignes, total,
 * paiements, montant reçu, reste dû, monnaie rendue). Lot 3-H-A (O-2) : lots consommés et
 * péremption sous chaque ligne. Reçu 80 mm (palier POS) : « Voir le reçu » et « Imprimer »
 * relisent la vente PERSISTÉE (`/sales/{id}/receipt`), jamais le panier.
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
  const { can, capabilities } = useCapabilities();
  const { currency, locale, timezone } = capabilities.tenant;
  const { sale, payments } = result;
  const money = (v: string) => formatMoney(v, currency, locale);
  const printing = useReceiptPrinting();
  const [viewing, setViewing] = useState(false);
  const completed = payments.filter((p) => p.status === 'COMPLETED');
  // Montant reçu : espèces remises (avant monnaie) + autres moyens ; monnaie rendue : calculée
  // par le serveur sur la seule partie espèces.
  const received = sumMoney(completed.map((p) => p.amount_received ?? p.amount));
  const change = sumMoney(completed.map((p) => p.change_given ?? '0'));

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
                  <LineLotsList lots={line.lots ?? []} unit={line.unit} locale={locale} />
                </td>
                <td className="sm-num">{money(line.line_total)}</td>
              </tr>
            ))}
            <tr className="sm-strong">
              <td>{t('pos.total')}</td>
              <td className="sm-num">{money(sale.total)}</td>
            </tr>
            {completed.map((p) => (
              <tr key={p.id}>
                <td>{p.method_label}</td>
                <td className="sm-num">{money(p.amount)}</td>
              </tr>
            ))}
            <tr>
              <td>{t('pos.amountReceived')}</td>
              <td className="sm-num" data-testid="receipt-received">
                {money(received)}
              </td>
            </tr>
            <tr>
              <td>{t('pos.remaining')}</td>
              <td className="sm-num" data-testid="receipt-remaining">
                {money(sale.remaining_amount ?? '0')}
              </td>
            </tr>
            <tr className="sm-strong">
              <td>{t('pos.change')}</td>
              <td className="sm-num" data-testid="receipt-change">
                {money(change)}
              </td>
            </tr>
          </tbody>
        </table>
        {sale.payment_status && <SalePaymentBadge status={sale.payment_status} />}
        {printing.error && <Message severity="error" text={printing.error} />}
        <div className="sm-dialog-actions">
          {onOpenSale && <Button type="button" label={t('sales.open')} text onClick={onOpenSale} />}
          {can('sales.sale.view') && (
            <Button
              type="button"
              icon="pi pi-receipt"
              label={t('receipt.view')}
              outlined
              onClick={() => setViewing(true)}
            />
          )}
          {can(RECEIPT_PRINT) && (
            <Button
              type="button"
              icon="pi pi-print"
              label={t('receipt.print')}
              outlined
              loading={printing.pending}
              onClick={() => printing.print(sale.id)}
            />
          )}
          <Button
            type="button"
            icon="pi pi-plus"
            label={t('pos.newSale')}
            onClick={onNewSale}
            autoFocus
          />
        </div>
      </div>
      {viewing && <ReceiptDialog saleId={sale.id} onClose={() => setViewing(false)} />}
      {printing.printer}
    </Dialog>
  );
}
