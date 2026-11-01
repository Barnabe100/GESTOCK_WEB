import { Checkbox } from 'primereact/checkbox';
import { Dropdown } from 'primereact/dropdown';
import { useTranslation } from 'react-i18next';

import { FormField } from '@/shared/ui/FormField';

import { accountText, type ConfiguredPaymentMethod, type PaymentAccount } from './api';

/**
 * Moyen de réception à la caisse (R6.5, ADR-0051) : identifiant où le client envoie les fonds
 * (facultatif ; un seul identifiant actif : présélectionné ; plusieurs : choisi par le caissier)
 * et CONFIRMATION MANUELLE explicite que les fonds ont été reçus (hors espèces). Choisir un moyen
 * ne vaut jamais confirmation ; le serveur revérifie tout (moyen, site, identifiant, devise).
 */

/** Identifiant proposé d'office : le seul identifiant actif utilisable sur le site. */
export function defaultAccountId(method: ConfiguredPaymentMethod | undefined): string | null {
  const accounts = method?.accounts ?? [];
  return accounts.length === 1 ? (accounts[0]?.id ?? null) : null;
}

export function needsConfirmation(method: ConfiguredPaymentMethod | undefined): boolean {
  return method !== undefined && method.kind !== 'CASH';
}

export function ReceivingAccountFields({
  idPrefix,
  method,
  accountId,
  confirmed,
  confirmError,
  onChange,
}: {
  idPrefix: string;
  method: ConfiguredPaymentMethod | undefined;
  accountId: string | null;
  confirmed: boolean;
  confirmError?: string;
  onChange: (patch: { accountId?: string | null; confirmed?: boolean }) => void;
}) {
  const { t } = useTranslation();
  if (!method || method.kind === 'CASH') return null;
  const accounts: PaymentAccount[] = method.accounts ?? [];
  const selected = accounts.find((a) => a.id === accountId);
  return (
    <>
      {method.provider_label && (
        <p className="sm-help" data-testid={`${idPrefix}-provider`}>
          {t('receiving.provider')} : {method.provider_label}
        </p>
      )}
      {accounts.length > 0 && (
        <FormField id={`${idPrefix}-account`} label={t('receiving.account')}>
          <Dropdown
            inputId={`${idPrefix}-account`}
            value={accountId ?? 'none'}
            onChange={(e) => {
              const value = e.value as string;
              onChange({ accountId: value === 'none' ? null : value });
            }}
            options={[
              ...accounts.map((a) => ({
                value: a.id,
                label: `${accountText(t, a)}${a.shared ? '' : ` · ${a.site_name ?? ''}`}`,
              })),
              { value: 'none', label: t('receiving.noAccount') },
            ]}
          />
        </FormField>
      )}
      {selected && (
        <div className="sm-receiving-account" data-testid={`${idPrefix}-account-details`}>
          <strong>{accountText(t, selected)}</strong>
          {selected.instructions && <small className="sm-help">{selected.instructions}</small>}
        </div>
      )}
      <div className="sm-checkbox">
        <Checkbox
          inputId={`${idPrefix}-confirmed`}
          checked={confirmed}
          invalid={confirmError !== undefined}
          onChange={(e) => onChange({ confirmed: e.checked === true })}
        />
        <label htmlFor={`${idPrefix}-confirmed`}>{t('receiving.confirm')}</label>
      </div>
      {confirmError ? (
        <small className="p-error">{confirmError}</small>
      ) : (
        <small className="sm-help">{t('receiving.confirmHelp')}</small>
      )}
    </>
  );
}
