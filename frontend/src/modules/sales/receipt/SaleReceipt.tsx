import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatMoney, formatQuantity } from '@/shared/lib/decimal';
import { formatDateTime } from '@/shared/lib/format';

import type { Receipt, ReceiptLine } from './api';
import { DEFAULT_RECEIPT_FORMAT, type ReceiptFormat } from './formats';

/** « 2 Carton 24 », « 3 bouteille » : la présentation réellement vendue (instantané). */
function presented(line: ReceiptLine, locale: string): string {
  return `${formatQuantity(line.quantity, locale)} ${line.packaging_name ?? line.unit}`;
}

/**
 * Contenu du reçu de vente, indépendant du format d'impression (le format ne fixe que la mise
 * en page : largeur, `@page`). Données exclusivement issues du reçu construit par le serveur
 * depuis la vente persistée ; aucune donnée interne (coûts, stock, audit, permissions).
 */
export function SaleReceipt({
  receipt,
  format = DEFAULT_RECEIPT_FORMAT,
}: {
  receipt: Receipt;
  format?: ReceiptFormat;
}) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const { currency, locale, timezone } = capabilities.tenant;
  const money = (v: string) => formatMoney(v, currency, locale);
  const { issuer } = receipt;
  // Reste dû et monnaie rendue calculés par le serveur : jamais positifs ensemble.
  const hasRemaining = /[1-9]/.test(receipt.remaining_amount);

  return (
    <article
      className={`sm-receipt ${format.className}`}
      aria-label={t('receipt.title', { number: receipt.number })}
      data-testid="sale-receipt"
      data-format={format.id}
    >
      <header className="sm-receipt-header">
        {issuer.logo_url && (
          <img className="sm-receipt-logo" src={issuer.logo_url} alt="" aria-hidden="true" />
        )}
        <p className="sm-receipt-issuer">{issuer.trade_name ?? issuer.name}</p>
        {issuer.trade_name && issuer.trade_name !== issuer.name && <p>{issuer.name}</p>}
        {[...issuer.contact, ...issuer.identifiers].map((line) => (
          <p key={`${line.kind}-${line.value}`}>
            {t(`company.preview.lines.${line.kind}`, { value: line.value })}
          </p>
        ))}
      </header>

      <dl className="sm-receipt-meta">
        <div>
          <dt>{t('receipt.site')}</dt>
          <dd>{receipt.site_name}</dd>
        </div>
        <div>
          <dt>{t('receipt.saleNumber')}</dt>
          <dd data-testid="receipt-number">{receipt.number}</dd>
        </div>
        <div>
          <dt>{t('receipt.date')}</dt>
          <dd>{formatDateTime(receipt.issued_at, locale, timezone)}</dd>
        </div>
        {receipt.cashier_name && (
          <div>
            <dt>{t('receipt.cashier')}</dt>
            <dd>{receipt.cashier_name}</dd>
          </div>
        )}
        {receipt.customer_name && (
          <div>
            <dt>{t('receipt.customer')}</dt>
            <dd>{receipt.customer_name}</dd>
          </div>
        )}
      </dl>

      <ul className="sm-receipt-lines" aria-label={t('receipt.lines')}>
        {receipt.lines.map((line, index) => (
          <li key={index} data-testid="receipt-line">
            <span className="sm-receipt-designation">{line.designation}</span>
            <span className="sm-receipt-row">
              <span>{`${presented(line, locale)} × ${money(line.unit_price)}`}</span>
              <span className="sm-num">{money(line.line_total)}</span>
            </span>
          </li>
        ))}
      </ul>

      <dl className="sm-receipt-totals">
        <div className="sm-receipt-total">
          <dt>{t('receipt.total')}</dt>
          <dd data-testid="receipt-total">{money(receipt.total)}</dd>
        </div>
        {receipt.payments?.map((p, index) => (
          <div key={index} data-testid="receipt-payment">
            <dt>{p.method_label}</dt>
            <dd>{money(p.amount)}</dd>
          </div>
        ))}
        {receipt.amount_received !== null && (
          <div>
            <dt>{t('receipt.amountReceived')}</dt>
            <dd data-testid="receipt-received">{money(receipt.amount_received)}</dd>
          </div>
        )}
        {receipt.change_given !== null && (
          <div>
            <dt>{t('receipt.change')}</dt>
            <dd data-testid="receipt-change">{money(receipt.change_given)}</dd>
          </div>
        )}
        <div>
          <dt>{t('receipt.remaining')}</dt>
          <dd data-testid="receipt-remaining">{money(receipt.remaining_amount)}</dd>
        </div>
      </dl>
      {receipt.is_credit && hasRemaining && (
        <p className="sm-receipt-credit" data-testid="receipt-credit">
          {t('receipt.credit', { amount: money(receipt.remaining_amount) })}
        </p>
      )}

      <p className="sm-receipt-thanks">{t('receipt.thanks')}</p>
    </article>
  );
}
