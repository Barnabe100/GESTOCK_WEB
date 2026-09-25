import { Button } from 'primereact/button';
import { Dialog } from 'primereact/dialog';
import { InputText } from 'primereact/inputtext';
import { Message } from 'primereact/message';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatMoney, normalizeDecimal } from '@/shared/lib/decimal';
import { FormField } from '@/shared/ui/FormField';
import { useToast } from '@/shared/ui/toast';

import { useCashMutations, type CashRegister } from './api';
import { cashError } from './ui';

/**
 * Ouverture d'une session : seul le fond initial est saisi. Utilisateur, date et heure sont
 * fixés par le serveur ; le fond initial ne pourra plus être modifié.
 */
export function OpenSessionDialog({
  register,
  onClose,
}: {
  register: CashRegister;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const navigate = useNavigate();
  const { capabilities } = useCapabilities();
  const { currency, locale } = capabilities.tenant;
  const { open } = useCashMutations();
  const [amount, setAmount] = useState('0');
  const [error, setError] = useState<string | null>(null);

  const submit = () => {
    const normalized = normalizeDecimal(amount, 2);
    if (normalized === null) {
      setError(t('cash.invalidAmount'));
      return;
    }
    setError(null);
    open.mutate(
      { cash_register_id: register.id, opening_float: normalized },
      {
        onSuccess: (session) => {
          toast.success(
            t('cash.sessionOpened', {
              number: session.number,
              amount: formatMoney(session.opening_float, currency, locale),
            }),
          );
          onClose();
          void navigate(`/cash/sessions/${session.id}`);
        },
        onError: (e) => setError(cashError(t, e, currency, locale)),
      },
    );
  };

  return (
    <Dialog
      header={t('cash.openTitle', { name: register.name })}
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
        <dl className="sm-details">
          <div>
            <dt>{t('cash.register')}</dt>
            <dd>{`${register.name} (${register.code})`}</dd>
          </div>
          <div>
            <dt>{t('layout.site')}</dt>
            <dd>{register.site_name}</dd>
          </div>
          <div>
            <dt>{t('cash.cashier')}</dt>
            <dd>{capabilities.user.full_name}</dd>
          </div>
        </dl>
        <FormField
          id="opening-float"
          label={t('cash.openingFloat')}
          required
          error={error ?? undefined}
          help={t('cash.openingFloatHelp', { currency })}
        >
          <InputText
            id="opening-float"
            inputMode="decimal"
            value={amount}
            invalid={error !== null}
            onChange={(e) => setAmount(e.target.value)}
            autoFocus
          />
        </FormField>
        <Message severity="info" text={t('cash.serverTime')} />
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button
            type="submit"
            icon="pi pi-lock-open"
            label={t('cash.open')}
            loading={open.isPending}
          />
        </div>
      </form>
    </Dialog>
  );
}
