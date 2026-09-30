import { Dropdown } from 'primereact/dropdown';
import { Message } from 'primereact/message';
import { useContext, useEffect } from 'react';
import { useTranslation } from 'react-i18next';

import { AuthContext } from '@/core/auth/AuthContext';
import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { FormField } from '@/shared/ui/FormField';

import { useCashSessions, useCashSites } from './api';

/**
 * Poste d'un encaissement en espèces. Caisse optionnelle par site : site sans caisse → rien
 * (espèces encaissées sans session). Site avec caisse : sessions ouvertes **par l'utilisateur**
 * sur ce site (session = site + poste + utilisateur). Aucune : avertissement (le serveur
 * refuse) ; une : indiquée ; plusieurs postes : à choisir. Le serveur vérifie et fait foi.
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
  const userId = useContext(AuthContext)?.user?.id ?? null;
  const allowed = can('cash_register.session.view');
  const canSeeSites = can('cash_register.register.view');
  const sites = useCashSites(allowed && canSeeSites);
  const site = sites.data?.find((s) => s.site_id === siteId);
  const cashEnabled = canSeeSites ? site?.enabled === true : true;
  const params: Record<string, string> = { status: 'OPEN', site_id: siteId, limit: '50' };
  if (userId) params.opened_by = userId;
  const sessions = useCashSessions(
    new URLSearchParams(params).toString(),
    allowed && cashEnabled && !(canSeeSites && sites.isPending),
  );
  const open = sessions.data?.items ?? [];
  const single = open.length === 1 ? open[0] : undefined;

  useEffect(() => {
    if (single && value !== single.cash_register_id) onChange(single.cash_register_id);
  }, [single, value, onChange]);

  if (!allowed || (canSeeSites && sites.isPending)) return null;
  if (!cashEnabled) return null;
  if (sessions.isPending) return null;
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
        options={open.map((s) => ({ value: s.cash_register_id, label: s.cash_register_name }))}
        placeholder={t('cash.chooseRegister')}
      />
    </FormField>
  );
}
