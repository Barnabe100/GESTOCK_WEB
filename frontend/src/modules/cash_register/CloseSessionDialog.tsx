import { Button } from 'primereact/button';
import { Checkbox } from 'primereact/checkbox';
import { Dialog } from 'primereact/dialog';
import { InputText } from 'primereact/inputtext';
import { InputTextarea } from 'primereact/inputtextarea';
import { Message } from 'primereact/message';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatMoney, normalizeDecimal, subtractMoney } from '@/shared/lib/decimal';
import { formatDateTime } from '@/shared/lib/format';
import { FormField } from '@/shared/ui/FormField';
import { useToast } from '@/shared/ui/toast';

import { useCashMutations, type CashSession } from './api';
import { CashVariance, cashError } from './ui';

/**
 * Clôture : récapitulatif (ouverture, fond initial, encaissements, sorties, solde théorique),
 * montant réellement compté, écart indicatif, confirmation explicite. Le serveur recalcule le
 * solde théorique et l'écart sous verrou : ils font foi.
 */
export function CloseSessionDialog({
  session,
  onClose,
}: {
  session: CashSession;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const { capabilities } = useCapabilities();
  const { currency, locale, timezone } = capabilities.tenant;
  const { close } = useCashMutations();
  const [counted, setCounted] = useState('');
  const [note, setNote] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const money = (value: string) => formatMoney(value, currency, locale);
  const normalized = normalizeDecimal(counted, 2);
  const variance = normalized ? subtractMoney(normalized, session.theoretical_balance) : null;

  const rows: [string, string][] = [
    [t('cash.session'), `${session.number} — ${session.cash_register_name}`],
    [t('cash.openedAt'), formatDateTime(session.opened_at, locale, timezone)],
    [t('cash.openingFloat'), money(session.opening_float)],
    [t('cash.cashInTotal'), money(session.cash_in_total)],
    [t('cash.cashOutTotal'), money(session.cash_out_total)],
  ];

  const submit = () => {
    if (normalized === null) {
      setError(t('cash.invalidAmount'));
      return;
    }
    setError(null);
    close.mutate(
      { id: session.id, counted: normalized, note: note.trim() || null },
      {
        onSuccess: (closed) => {
          toast.success(
            t('cash.sessionClosed', {
              number: closed.number,
              variance: money(closed.variance ?? '0'),
            }),
          );
          onClose();
        },
        onError: (e) => setError(cashError(t, e, currency, locale)),
      },
    );
  };

  return (
    <Dialog
      header={t('cash.closeTitle', { number: session.number })}
      visible
      onHide={onClose}
      className="sm-dialog"
    >
      <form
        className="sm-form"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <dl className="sm-details" aria-label={t('cash.closeSummary')}>
          {rows.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
          <div>
            <dt>{t('cash.theoreticalBalance')}</dt>
            <dd className="sm-strong" data-testid="close-theoretical">
              {money(session.theoretical_balance)}
            </dd>
          </div>
        </dl>
        <FormField
          id="counted-balance"
          label={t('cash.countedBalance')}
          required
          error={error ?? undefined}
          help={t('cash.countedHelp', { currency })}
        >
          <InputText
            id="counted-balance"
            inputMode="decimal"
            value={counted}
            invalid={error !== null}
            onChange={(e) => {
              setCounted(e.target.value);
              setConfirmed(false);
            }}
            autoFocus
          />
        </FormField>
        {variance !== null && (
          <p className="sm-help" data-testid="close-variance">
            {t('cash.expectedVariance')}{' '}
            <CashVariance value={variance} currency={currency} locale={locale} />
          </p>
        )}
        <FormField id="close-note" label={t('cash.closingNote')}>
          <InputTextarea
            id="close-note"
            rows={2}
            maxLength={500}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </FormField>
        <Message severity="warn" text={t('cash.closeWarning')} />
        <div className="sm-checkbox">
          <Checkbox
            inputId="close-confirm"
            checked={confirmed}
            disabled={normalized === null}
            onChange={(e) => setConfirmed(e.checked === true)}
          />
          <label htmlFor="close-confirm">{t('cash.closeConfirm')}</label>
        </div>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button
            type="submit"
            icon="pi pi-lock"
            label={t('cash.close')}
            severity="danger"
            disabled={!confirmed || normalized === null}
            loading={close.isPending}
          />
        </div>
      </form>
    </Dialog>
  );
}
