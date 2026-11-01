import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { DataTable } from 'primereact/datatable';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { InputTextarea } from 'primereact/inputtextarea';
import { Message } from 'primereact/message';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { usePublicCountries } from '@/core/api/geo';
import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { translateError } from '@/shared/lib/errors';
import { confirmAction } from '@/shared/ui/confirm';
import { ListEmpty } from '@/shared/ui/EmptyState';
import { FormField } from '@/shared/ui/FormField';
import { RowActions } from '@/shared/ui/RowActions';
import { ActiveBadge, StatusBadge } from '@/shared/ui/StatusBadge';
import { useToast } from '@/shared/ui/toast';

import {
  IDENTIFIER_TYPES,
  usePaymentAccountMutations,
  usePaymentAccounts,
  usePaymentProviders,
  type ConfiguredPaymentMethod,
  type IdentifierType,
  type PaymentAccount,
} from './api';

/**
 * Identifiants de réception d'un moyen (R6.5, ADR-0051) : numéros, codes marchands, codes agents,
 * comptes ou identifiants personnalisés, COMMUNS à tous les sites ou PROPRES à un site, sans
 * nombre maximal. La liste et les droits sont calculés par le serveur : identifiants des sites
 * accessibles seulement ; un identifiant commun exige la permission sur tous les sites.
 */

const SHARED = 'shared';
const NO_COUNTRY = '__none';

function AccountForm({
  method,
  account,
  defaultSite,
  onClose,
}: {
  method: ConfiguredPaymentMethod;
  account: PaymentAccount | null;
  defaultSite: string | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const { capabilities } = useCapabilities();
  const { save } = usePaymentAccountMutations(method.id);
  const providers = usePaymentProviders({}, method.provider_code != null);
  const rules = providers.data?.find((p) => p.code === method.provider_code)?.identifiers ?? [];
  const accepted = rules.length > 0 ? rules.map((r) => r.type) : IDENTIFIER_TYPES;
  const [scope, setScope] = useState<string>(account?.site_id ?? defaultSite ?? SHARED);
  const [type, setType] = useState<IdentifierType>(
    account?.identifier_type ?? accepted[0] ?? 'PHONE',
  );
  const [customLabel, setCustomLabel] = useState(account?.custom_label ?? '');
  const [value, setValue] = useState(account?.value ?? '');
  const [holder, setHolder] = useState(account?.holder_name ?? '');
  // Pays de rattachement : par défaut celui du moyen ; aucun = international.
  const countries = usePublicCountries();
  const [country, setCountry] = useState<string>(
    (account ? account.country_code : method.country_code) ?? NO_COUNTRY,
  );
  const callingCode = countries.data?.find((c) => c.code === country)?.calling_code ?? null;
  const [instructions, setInstructions] = useState(account?.instructions ?? '');
  const [order, setOrder] = useState(String(account?.sort_order ?? 0));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const rule = rules.find((r) => r.type === type);
  const submit = () => {
    const next: Record<string, string> = {};
    if (value.trim() === '') next.value = t('validation.required');
    if (type === 'CUSTOM' && customLabel.trim() === '') next.customLabel = t('validation.required');
    if (!/^\d{1,4}$/.test(order)) next.order = t('validation.required');
    setErrors(next);
    if (Object.keys(next).length > 0) return;
    save.mutate(
      {
        id: account?.id,
        input: {
          site_id: scope === SHARED ? null : scope,
          identifier_type: type,
          country_code: country === NO_COUNTRY ? null : country,
          custom_label: type === 'CUSTOM' ? customLabel.trim() : null,
          value: value.trim(),
          holder_name: holder.trim() || null,
          instructions: instructions.trim() || null,
          sort_order: Number(order),
        },
      },
      {
        onSuccess: () => {
          toast.success(t('receiving.accountSaved'));
          onClose();
        },
        onError: (error) => toast.error(translateError(t, error)),
      },
    );
  };
  return (
    <Dialog
      header={t(account ? 'receiving.editAccount' : 'receiving.newAccount')}
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
        <FormField
          id="acc-scope"
          label={t('receiving.scope')}
          required
          help={t('receiving.scopeHelp')}
        >
          <Dropdown
            inputId="acc-scope"
            value={scope}
            disabled={account !== null}
            onChange={(e) => setScope(e.value as string)}
            options={[
              { value: SHARED, label: t('receiving.allSites') },
              ...capabilities.sites.map((s) => ({ value: s.id, label: s.name })),
            ]}
          />
        </FormField>
        <FormField id="acc-type" label={t('receiving.identifierType')} required>
          <Dropdown
            inputId="acc-type"
            value={type}
            disabled={account !== null}
            onChange={(e) => setType(e.value as IdentifierType)}
            options={accepted.map((k) => ({
              value: k,
              label: t(`receiving.identifierTypes.${k}`),
            }))}
          />
        </FormField>
        {type === 'CUSTOM' && (
          <FormField
            id="acc-label"
            label={t('receiving.customLabel')}
            required
            error={errors.customLabel}
          >
            <InputText
              id="acc-label"
              maxLength={60}
              value={customLabel}
              onChange={(e) => setCustomLabel(e.target.value)}
            />
          </FormField>
        )}
        <FormField
          id="acc-country"
          label={t('receiving.attachmentCountry')}
          help={t('receiving.attachmentCountryHelp')}
        >
          <Dropdown
            inputId="acc-country"
            value={country}
            filter
            onChange={(e) => setCountry(e.value as string)}
            options={[
              { value: NO_COUNTRY, label: t('receiving.international') },
              ...(countries.data ?? []).map((c) => ({
                value: c.code,
                label: c.calling_code ? `${c.name} (+${c.calling_code})` : c.name,
              })),
            ]}
          />
        </FormField>
        <FormField
          id="acc-value"
          label={t('receiving.value')}
          required
          error={errors.value}
          help={
            rule && (rule.min_length || rule.max_length)
              ? t('receiving.ruleHelp', {
                  min: rule.min_length ?? 1,
                  max: rule.max_length ?? 100,
                })
              : type === 'PHONE'
                ? callingCode
                  ? t('receiving.phoneHelpCountry', { code: callingCode })
                  : t('receiving.phoneHelp')
                : t('receiving.valueHelp')
          }
        >
          <InputText
            id="acc-value"
            maxLength={100}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            autoFocus
          />
        </FormField>
        <FormField id="acc-holder" label={t('receiving.holder')}>
          <InputText
            id="acc-holder"
            maxLength={100}
            value={holder}
            onChange={(e) => setHolder(e.target.value)}
          />
        </FormField>
        <FormField id="acc-instructions" label={t('receiving.instructions')}>
          <InputTextarea
            id="acc-instructions"
            rows={2}
            maxLength={500}
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
          />
        </FormField>
        <FormField id="acc-order" label={t('paymentMethods.order')} error={errors.order}>
          <InputText
            id="acc-order"
            inputMode="numeric"
            value={order}
            onChange={(e) => setOrder(e.target.value)}
          />
        </FormField>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button type="submit" label={t('actions.save')} loading={save.isPending} />
        </div>
      </form>
    </Dialog>
  );
}

export function PaymentAccountsDialog({
  method,
  site,
  onClose,
}: {
  method: ConfiguredPaymentMethod;
  /** Site mis en avant (venu de la page Sites) : préselectionné pour un nouvel identifiant. */
  site: string | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const accounts = usePaymentAccounts(method.id);
  const { setActive } = usePaymentAccountMutations(method.id);
  const [editing, setEditing] = useState<PaymentAccount | null | undefined>(undefined);
  const rows = (accounts.data ?? []).filter((a) => site === null || a.shared || a.site_id === site);
  const toggle = (a: PaymentAccount) => {
    const run = () =>
      setActive.mutate(
        { id: a.id, active: !a.is_active },
        {
          onSuccess: () => toast.success(t('receiving.statusChanged')),
          onError: (error) => toast.error(translateError(t, error)),
        },
      );
    if (!a.is_active) return run();
    confirmAction(t, {
      header: t('receiving.deactivateTitle'),
      message: t('receiving.deactivateConfirm', { value: a.value }),
      acceptLabel: t('actions.deactivate'),
      danger: true,
      onAccept: run,
    });
  };
  return (
    <Dialog
      header={t('receiving.accountsOf', { label: method.label })}
      visible
      onHide={onClose}
      className="sm-dialog sm-dialog-wide"
    >
      <div className="sm-form">
        <p className="sm-help">{t('receiving.accountsHelp')}</p>
        {method.kind === 'CASH' ? (
          <Message severity="info" text={t('receiving.cashHasNoAccount')} />
        ) : (
          <>
            <DataTable
              className="sm-table"
              value={rows}
              loading={accounts.isFetching}
              dataKey="id"
              tableStyle={{ minWidth: '36rem' }}
              emptyMessage={<ListEmpty filtered={false} title={t('receiving.noAccounts')} />}
            >
              <Column
                header={t('receiving.scope')}
                body={(a: PaymentAccount) =>
                  a.shared ? (
                    <StatusBadge tone="info" icon="pi pi-globe" label={t('receiving.allSites')} />
                  ) : (
                    <StatusBadge tone="neutral" icon="pi pi-building" label={a.site_name ?? ''} />
                  )
                }
              />
              <Column
                header={t('receiving.identifierType')}
                body={(a: PaymentAccount) =>
                  a.identifier_type === 'CUSTOM' && a.custom_label
                    ? a.custom_label
                    : t(`receiving.identifierTypes.${a.identifier_type}`)
                }
              />
              <Column
                header={t('receiving.value')}
                body={(a: PaymentAccount) =>
                  a.identifier_type === 'PHONE' && a.phone_format === 'local' && a.calling_code
                    ? `${a.value} · ${a.country_code ?? ''} +${a.calling_code}`
                    : a.value
                }
              />
              <Column
                header={t('receiving.holder')}
                body={(a: PaymentAccount) => a.holder_name ?? '—'}
              />
              <Column
                header={t('paymentMethods.status')}
                body={(a: PaymentAccount) => <ActiveBadge active={a.is_active} />}
              />
              <Column
                header={t('common.actions')}
                body={(a: PaymentAccount) => (
                  <RowActions
                    actions={[
                      {
                        key: 'edit',
                        label: t('actions.edit'),
                        icon: 'pi pi-pencil',
                        onClick: () => setEditing(a),
                      },
                      {
                        key: 'status',
                        label: t(a.is_active ? 'actions.deactivate' : 'actions.activate'),
                        icon: a.is_active ? 'pi pi-ban' : 'pi pi-check-circle',
                        danger: a.is_active,
                        onClick: () => toggle(a),
                      },
                    ]}
                  />
                )}
              />
            </DataTable>
          </>
        )}
        <div className="sm-dialog-actions">
          {method.kind !== 'CASH' && (
            <Button
              type="button"
              icon="pi pi-plus"
              label={t('receiving.newAccount')}
              outlined
              onClick={() => setEditing(null)}
            />
          )}
          <Button type="button" label={t('actions.close')} onClick={onClose} />
        </div>
      </div>
      {editing !== undefined && (
        <AccountForm
          method={method}
          account={editing}
          defaultSite={site}
          onClose={() => setEditing(undefined)}
        />
      )}
    </Dialog>
  );
}
