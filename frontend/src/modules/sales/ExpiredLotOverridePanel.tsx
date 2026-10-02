import { Button } from 'primereact/button';
import { Checkbox } from 'primereact/checkbox';
import { InputText } from 'primereact/inputtext';
import { InputTextarea } from 'primereact/inputtextarea';
import { Message } from 'primereact/message';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import type { ExpiredShortage } from '@/modules/stock/ui';
import {
  compareQuantity,
  formatQuantity,
  normalizeDecimal,
  subtractQuantity,
} from '@/shared/lib/decimal';
import { formatDate } from '@/shared/lib/format';
import { FormField } from '@/shared/ui/FormField';

import type { ExpiredLotOverride } from './api';

export const EXPIRED_LOT_OVERRIDE = 'sales.sale.expired_lot_override';

/** Quantités proposées : le manque de chaque article, réparti dans l'ordre des lots périmés
 *  (le plus ancien d'abord). Simple proposition : l'utilisateur la confirme ou la corrige. */
function proposal(shortages: ExpiredShortage[]): Record<string, string> {
  const result: Record<string, string> = {};
  for (const article of shortages) {
    let rest = article.missing;
    for (const lot of article.expired_lots) {
      if (compareQuantity(rest, '0') <= 0) break;
      const taken = compareQuantity(lot.available, rest) < 0 ? lot.available : rest;
      result[lot.lot_id] = taken;
      rest = subtractQuantity(rest, taken);
    }
  }
  return result;
}

/**
 * Dérogation EXPLICITE à la vente d'un lot périmé (Lot 3-H-A, O-1) — jamais automatique :
 * affichée seulement après le refus `insufficient_unexpired_stock` du serveur. Lots périmés
 * clairement signalés, quantités à confirmer, motif obligatoire et confirmation explicite ;
 * réservée à `sales.sale.expired_lot_override` (le serveur revérifie permission, lots, quantités
 * et motif, et audite la dérogation).
 */
export function ExpiredLotOverridePanel({
  shortages,
  idPrefix,
  pending,
  onConfirm,
}: {
  shortages: ExpiredShortage[];
  idPrefix: string;
  pending: boolean;
  onConfirm: (override: ExpiredLotOverride) => void;
}) {
  const { t } = useTranslation();
  const { can, capabilities } = useCapabilities();
  const { locale } = capabilities.tenant;
  const [quantities, setQuantities] = useState<Record<string, string>>(() => proposal(shortages));
  const [reason, setReason] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  if (!can(EXPIRED_LOT_OVERRIDE)) {
    return <Message severity="warn" text={t('expiredLots.notAllowed')} />;
  }
  const lots = shortages.flatMap((a) =>
    a.expired_lots.map((lot) => ({ ...lot, article_id: a.article_id })),
  );
  const picks = lots
    .map((lot) => ({ lot, quantity: normalizeDecimal(quantities[lot.lot_id] ?? '', 3) }))
    .filter((p) => p.quantity !== null && /[1-9]/.test(p.quantity));
  const invalid = lots.some(
    (lot) =>
      (quantities[lot.lot_id] ?? '') !== '' &&
      normalizeDecimal(quantities[lot.lot_id] ?? '', 3) === null,
  );
  const ready = picks.length > 0 && !invalid && reason.trim().length >= 5 && confirmed;

  return (
    <section className="sm-expired-override" aria-labelledby={`${idPrefix}-expired-title`}>
      <h3 id={`${idPrefix}-expired-title`}>{t('expiredLots.title')}</h3>
      <Message severity="warn" text={t('expiredLots.warning')} />
      {shortages.map((article) => (
        <div key={article.article_id} className="sm-expired-article">
          <p>
            {t('expiredLots.article', {
              reference: article.reference,
              missing: formatQuantity(article.missing, locale),
            })}
          </p>
          <ul className="sm-lot-allocation-list">
            {article.expired_lots.map((lot) => {
              const inputId = `${idPrefix}-expired-${lot.lot_id}`;
              return (
                <li key={lot.lot_id} className="sm-lot-allocation-row">
                  <div className="sm-cell-stack">
                    <strong>{t('lots.lotNumberShort', { number: lot.lot_number })}</strong>
                    <span className="p-error">
                      {lot.expiry_date
                        ? t('expiredLots.expiredOn', {
                            date: formatDate(lot.expiry_date, locale, 'UTC'),
                          })
                        : t('lots.states.expired')}
                    </span>
                    <span className="sm-muted">
                      {t('lotAllocation.available', {
                        quantity: formatQuantity(lot.available, locale),
                      })}
                    </span>
                  </div>
                  <label htmlFor={inputId} className="sm-lot-allocation-input">
                    <span>{t('lotAllocation.quantity', { number: lot.lot_number })}</span>
                    <InputText
                      id={inputId}
                      inputMode="decimal"
                      value={quantities[lot.lot_id] ?? ''}
                      onChange={(e) =>
                        setQuantities((q) => ({ ...q, [lot.lot_id]: e.target.value }))
                      }
                    />
                  </label>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
      <FormField
        id={`${idPrefix}-expired-reason`}
        label={t('expiredLots.reason')}
        help={t('expiredLots.reasonHelp')}
        required
      >
        <InputTextarea
          id={`${idPrefix}-expired-reason`}
          rows={3}
          maxLength={500}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
      </FormField>
      <div className="sm-checkbox">
        <Checkbox
          inputId={`${idPrefix}-expired-confirm`}
          checked={confirmed}
          onChange={(e) => setConfirmed(e.checked === true)}
        />
        <label htmlFor={`${idPrefix}-expired-confirm`}>{t('expiredLots.confirm')}</label>
      </div>
      <div className="sm-dialog-actions">
        <Button
          type="button"
          icon="pi pi-exclamation-triangle"
          severity="danger"
          label={t('expiredLots.sell')}
          disabled={!ready}
          loading={pending}
          onClick={() =>
            onConfirm({
              reason: reason.trim(),
              lots: picks.map((p) => ({
                article_id: p.lot.article_id,
                lot_id: p.lot.lot_id,
                quantity: p.quantity ?? '0',
              })),
            })
          }
        />
      </div>
    </section>
  );
}
