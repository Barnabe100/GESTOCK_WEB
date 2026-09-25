import { Dropdown } from 'primereact/dropdown';
import { Message } from 'primereact/message';
import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { FormField } from '@/shared/ui/FormField';

import { useCashSessions } from './api';

/**
 * Caisse d'un encaissement en espèces : sessions ouvertes sur le site de la vente. Aucune :
 * avertissement (le serveur refuse) ; une : indiquée ; plusieurs : à choisir. Le serveur
 * vérifie toujours la caisse (site, session ouverte) et fait foi.
 */
export function CashRegisterChoice({
  siteId,
  value,
  onChange,
}: {
  siteId: string;
  value: string | null;
  onChange: (registerId: string | null) => void;
}) {
  const { t } = useTranslation();
  const { can } = useCapabilities();
  const allowed = can('cash_register.session.view');
  const sessions = useCashSessions(
    new URLSearchParams({ status: 'OPEN', site_id: siteId, limit: '50' }).toString(),
    allowed,
  );
  const open = sessions.data?.items ?? [];
  const single = open.length === 1 ? open[0] : undefined;

  useEffect(() => {
    if (single && value !== single.cash_register_id) onChange(single.cash_register_id);
  }, [single, value, onChange]);

  if (!allowed || sessions.isPending) return null;
  if (open.length === 0) {
    return <Message severity="warn" text={t('cash.noOpenRegister')} />;
  }
  if (single) {
    return (
      <Message
        severity="info"
        text={t('cash.cashInto', { name: single.cash_register_name, number: single.number })}
      />
    );
  }
  return (
    <FormField id="payment-cash-register" label={t('cash.register')} required>
      <Dropdown
        inputId="payment-cash-register"
        value={value}
        onChange={(e) => onChange((e.value as string | undefined) ?? null)}
        options={open.map((s) => ({
          value: s.cash_register_id,
          label: `${s.cash_register_name} · ${s.opened_by_name ?? ''}`,
        }))}
        placeholder={t('cash.chooseRegister')}
      />
    </FormField>
  );
}
