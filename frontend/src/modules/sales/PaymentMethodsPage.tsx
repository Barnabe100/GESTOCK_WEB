import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Checkbox } from 'primereact/checkbox';
import { Column } from 'primereact/column';
import { DataTable } from 'primereact/datatable';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { InputSwitch } from 'primereact/inputswitch';
import { InputText } from 'primereact/inputtext';
import { Message } from 'primereact/message';
import { useState } from 'react';
import { Controller, useForm, useWatch } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { z } from 'zod';

import { usePublicCountries } from '@/core/api/geo';
import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { translateError } from '@/shared/lib/errors';
import { confirmAction } from '@/shared/ui/confirm';
import { ListEmpty } from '@/shared/ui/EmptyState';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { FormField } from '@/shared/ui/FormField';
import { PageHeader } from '@/shared/ui/PageHeader';
import { RowActions } from '@/shared/ui/RowActions';
import { ActiveBadge } from '@/shared/ui/StatusBadge';
import { useToast } from '@/shared/ui/toast';

import {
  PAYMENT_METHODS,
  usePaymentMethodMutations,
  usePaymentMethods,
  usePaymentProviders,
  type ConfiguredPaymentMethod,
  type PaymentMethod,
} from './api';
import { PaymentAccountsDialog } from './PaymentAccountsDialog';

/** Opérateur : aucun, du catalogue (code) ou personnalisé (saisi) — jamais obligatoire. */
const NO_PROVIDER = '__none';
const CUSTOM_PROVIDER = '__custom';
const INTERNATIONAL = '__intl';

const schema = z
  .object({
    label: z.string().trim().min(1).max(60),
    kind: z.enum(['CASH', 'MOBILE_MONEY', 'CARD', 'BANK_TRANSFER', 'OTHER']),
    reference_required: z.boolean(),
    sort_order: z.string().regex(/^\d{1,4}$/),
    provider: z.string(),
    provider_name: z.string().trim().max(100),
    country: z.string(),
    currency: z.string().regex(/^[A-Z]{3}$/),
  })
  .refine((v) => v.provider !== CUSTOM_PROVIDER || v.provider_name.length > 0, {
    path: ['provider_name'],
  });
type FormValues = z.infer<typeof schema>;

function MethodDialog({
  method,
  onClose,
}: {
  method: ConfiguredPaymentMethod | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const { capabilities } = useCapabilities();
  const tenant = capabilities.tenant;
  const { save } = usePaymentMethodMutations();
  const countries = usePublicCountries();
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      label: method?.label ?? '',
      kind: method?.kind ?? 'MOBILE_MONEY',
      reference_required: method?.reference_required ?? false,
      sort_order: String(method?.sort_order ?? 100),
      provider: method?.provider_code ?? (method?.provider_name ? CUSTOM_PROVIDER : NO_PROVIDER),
      provider_name: method?.provider_name ?? '',
      country:
        method === null
          ? (tenant.country_code ?? INTERNATIONAL)
          : (method.country_code ?? INTERNATIONAL),
      currency: method?.currency ?? tenant.currency,
    },
  });
  const errors = form.formState.errors;
  const [kind, country, currency, provider] = useWatch({
    control: form.control,
    name: ['kind', 'country', 'currency', 'provider'],
  });
  // Catalogue (vide au départ) filtré par le serveur : pays (ou international), devise, type.
  const providers = usePaymentProviders(
    { country: country === INTERNATIONAL ? null : country, currency, kind },
    kind !== 'CASH',
  );
  const currencies = [
    ...new Set([tenant.currency, ...(countries.data ?? []).map((c) => c.currency)]),
  ].sort();
  const onSubmit = form.handleSubmit((values) =>
    save.mutate(
      {
        id: method?.id,
        input: {
          label: values.label.trim(),
          kind: values.kind,
          reference_required: values.reference_required,
          sort_order: Number(values.sort_order),
          provider_code:
            values.kind === 'CASH' ||
            values.provider === NO_PROVIDER ||
            values.provider === CUSTOM_PROVIDER
              ? null
              : values.provider,
          provider_name:
            values.kind !== 'CASH' && values.provider === CUSTOM_PROVIDER
              ? values.provider_name.trim()
              : null,
          country_code: values.country === INTERNATIONAL ? null : values.country,
          currency: values.currency,
        },
      },
      {
        onSuccess: (saved) => {
          toast.success(
            t(method ? 'paymentMethods.updated' : 'paymentMethods.created', {
              label: saved.label,
            }),
          );
          onClose();
        },
        onError: (error) => toast.error(translateError(t, error)),
      },
    ),
  );

  return (
    <Dialog
      header={t(method ? 'paymentMethods.edit' : 'paymentMethods.new')}
      visible
      onHide={onClose}
      className="sm-dialog"
    >
      <form onSubmit={onSubmit} className="sm-form" noValidate>
        <FormField
          id="pm-label"
          label={t('paymentMethods.label')}
          required
          error={errors.label && t('validation.required')}
        >
          <InputText
            id="pm-label"
            maxLength={60}
            placeholder={t('paymentMethods.labelPlaceholder')}
            {...form.register('label')}
            autoFocus
          />
        </FormField>
        <FormField
          id="pm-kind"
          label={t('paymentMethods.kind')}
          required
          help={t('paymentMethods.kindHelp')}
        >
          <Controller
            control={form.control}
            name="kind"
            render={({ field }) => (
              <Dropdown
                inputId="pm-kind"
                value={field.value}
                disabled={method !== null}
                onChange={(e) => field.onChange(e.value as PaymentMethod)}
                options={PAYMENT_METHODS.map((k) => ({
                  value: k,
                  label: t(`payment.method.${k}`),
                }))}
              />
            )}
          />
        </FormField>
        {kind !== 'CASH' && (
          <FormField
            id="pm-provider"
            label={t('receiving.provider')}
            help={t('receiving.providerHelp')}
          >
            <Controller
              control={form.control}
              name="provider"
              render={({ field }) => (
                <Dropdown
                  inputId="pm-provider"
                  value={field.value}
                  onChange={(e) => field.onChange(e.value as string)}
                  options={[
                    { value: NO_PROVIDER, label: t('receiving.noProvider') },
                    ...(providers.data ?? []).map((p) => ({ value: p.code, label: p.name })),
                    // Opérateur du moyen absent du filtre (pays ou devise changés) : conservé.
                    ...(method?.provider_code &&
                    !(providers.data ?? []).some((p) => p.code === method.provider_code)
                      ? [
                          {
                            value: method.provider_code,
                            label: method.provider_label ?? method.provider_code,
                          },
                        ]
                      : []),
                    { value: CUSTOM_PROVIDER, label: t('receiving.customProvider') },
                  ]}
                />
              )}
            />
          </FormField>
        )}
        {kind !== 'CASH' && provider === CUSTOM_PROVIDER && (
          <FormField
            id="pm-provider-name"
            label={t('receiving.customProviderName')}
            required
            error={errors.provider_name && t('validation.required')}
          >
            <InputText
              id="pm-provider-name"
              maxLength={100}
              placeholder={t('payment.providerPlaceholder')}
              {...form.register('provider_name')}
            />
          </FormField>
        )}
        <FormField id="pm-country" label={t('receiving.country')}>
          <Controller
            control={form.control}
            name="country"
            render={({ field }) => (
              <Dropdown
                inputId="pm-country"
                value={field.value}
                filter
                onChange={(e) => field.onChange(e.value as string)}
                options={[
                  { value: INTERNATIONAL, label: t('receiving.international') },
                  ...(countries.data ?? []).map((c) => ({ value: c.code, label: c.name })),
                ]}
              />
            )}
          />
        </FormField>
        <FormField id="pm-currency" label={t('receiving.currency')} required>
          <Controller
            control={form.control}
            name="currency"
            render={({ field }) => (
              <Dropdown
                inputId="pm-currency"
                value={field.value}
                filter
                onChange={(e) => field.onChange(e.value as string)}
                options={currencies.map((c) => ({ value: c, label: c }))}
              />
            )}
          />
        </FormField>
        {currency !== tenant.currency && (
          <Message
            severity="warn"
            text={t('receiving.currencyWarning', { currency, sales: tenant.currency })}
          />
        )}
        <div className="sm-checkbox">
          <Controller
            control={form.control}
            name="reference_required"
            render={({ field }) => (
              <Checkbox
                inputId="pm-reference"
                checked={field.value}
                onChange={(e) => field.onChange(e.checked === true)}
              />
            )}
          />
          <label htmlFor="pm-reference">{t('paymentMethods.referenceRequired')}</label>
        </div>
        <small className="sm-help">{t('paymentMethods.referenceRequiredHelp')}</small>
        <FormField
          id="pm-order"
          label={t('paymentMethods.order')}
          error={errors.sort_order && t('validation.required')}
        >
          <InputText id="pm-order" inputMode="numeric" {...form.register('sort_order')} />
        </FormField>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button type="submit" label={t('actions.save')} loading={save.isPending} />
        </div>
      </form>
    </Dialog>
  );
}

/** Disponibilité par site (sites accessibles ; permission revérifiée par le serveur). */
function SitesDialog({
  method,
  onClose,
}: {
  method: ConfiguredPaymentMethod;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const { capabilities } = useCapabilities();
  const { setSite } = usePaymentMethodMutations();
  const [disabled, setDisabled] = useState<string[]>(method.disabled_site_ids);
  const toggle = (siteId: string, enabled: boolean) =>
    setSite.mutate(
      { id: method.id, siteId, enabled },
      {
        onSuccess: (saved) => {
          setDisabled(saved.disabled_site_ids);
          toast.success(t('paymentMethods.siteUpdated'));
        },
        onError: (error) => toast.error(translateError(t, error)),
      },
    );
  return (
    <Dialog
      header={t('paymentMethods.sitesDialog', { label: method.label })}
      visible
      onHide={onClose}
      className="sm-dialog"
    >
      <ul className="sm-switch-list">
        {capabilities.sites.map((site) => {
          const id = `pm-site-${site.id}`;
          return (
            <li key={site.id} className="sm-checkbox">
              <InputSwitch
                inputId={id}
                checked={!disabled.includes(site.id)}
                disabled={setSite.isPending}
                onChange={(e) => toggle(site.id, e.value === true)}
              />
              <label htmlFor={id}>{site.name}</label>
            </li>
          );
        })}
      </ul>
      <div className="sm-dialog-actions">
        <Button type="button" label={t('actions.close')} onClick={onClose} />
      </div>
    </Dialog>
  );
}

/**
 * Moyens de paiement de l'entreprise (`sales.payment_method.manage`) : libellé libre, type
 * (comportement), référence obligatoire, ordre, activation et disponibilité par site. Aucun
 * moyen supprimé (désactivation) ; saisie manuelle (l'intégration API est une étape future).
 */
export default function PaymentMethodsPage() {
  const { t } = useTranslation();
  const toast = useToast();
  const { capabilities } = useCapabilities();
  const methods = usePaymentMethods();
  const { setActive } = usePaymentMethodMutations();
  const [editing, setEditing] = useState<ConfiguredPaymentMethod | null | undefined>(undefined);
  const [sites, setSites] = useState<ConfiguredPaymentMethod | null>(null);
  const [accounts, setAccounts] = useState<ConfiguredPaymentMethod | null>(null);
  // Venue de la page Sites : identifiants de ce site mis en avant.
  const [search] = useSearchParams();
  const focusSite = capabilities.sites.some((s) => s.id === search.get('site'))
    ? search.get('site')
    : null;
  const siteName = (id: string) => capabilities.sites.find((s) => s.id === id)?.name ?? id;

  const toggle = (m: ConfiguredPaymentMethod) => {
    const run = () =>
      setActive.mutate(
        { id: m.id, active: !m.is_active },
        {
          onSuccess: () =>
            toast.success(
              t(m.is_active ? 'paymentMethods.deactivated' : 'paymentMethods.activated'),
            ),
          onError: (error) => toast.error(translateError(t, error)),
        },
      );
    if (!m.is_active) return run();
    confirmAction(t, {
      header: t('paymentMethods.deactivateTitle'),
      message: t('paymentMethods.deactivateConfirm', { label: m.label }),
      acceptLabel: t('actions.deactivate'),
      danger: true,
      onAccept: run,
    });
  };

  return (
    <>
      <PageHeader
        title={t('paymentMethods.title')}
        description={t('paymentMethods.subtitle')}
        actions={
          <Button
            icon="pi pi-plus"
            label={t('paymentMethods.new')}
            onClick={() => setEditing(null)}
          />
        }
      />
      {focusSite && (
        <Message
          severity="info"
          text={t('receiving.siteFocus', {
            site: capabilities.sites.find((s) => s.id === focusSite)?.name ?? '',
          })}
        />
      )}
      {methods.isError ? (
        <ErrorMessage error={methods.error} onRetry={() => void methods.refetch()} />
      ) : (
        <DataTable
          className="sm-table"
          value={methods.data ?? []}
          loading={methods.isFetching}
          dataKey="id"
          rowHover
          tableStyle={{ minWidth: '44rem' }}
          emptyMessage={<ListEmpty filtered={false} title={t('paymentMethods.empty')} />}
        >
          <Column field="label" header={t('paymentMethods.label')} />
          <Column
            header={t('paymentMethods.kind')}
            body={(m: ConfiguredPaymentMethod) => t(`payment.method.${m.kind}`)}
          />
          <Column
            header={t('receiving.provider')}
            body={(m: ConfiguredPaymentMethod) => m.provider_label ?? '—'}
          />
          <Column
            header={t('receiving.countryCurrency')}
            body={(m: ConfiguredPaymentMethod) =>
              `${m.country_code ?? t('receiving.international')} · ${m.currency}`
            }
          />
          <Column
            header={t('paymentMethods.referenceRequired')}
            body={(m: ConfiguredPaymentMethod) =>
              t(m.reference_required ? 'common.yes' : 'common.no')
            }
          />
          <Column header={t('paymentMethods.mode')} body={() => t('paymentMethods.manual')} />
          <Column
            header={t('paymentMethods.sites')}
            body={(m: ConfiguredPaymentMethod) =>
              m.disabled_site_ids.length === 0
                ? t('paymentMethods.allSites')
                : t('paymentMethods.disabledOn', {
                    sites: m.disabled_site_ids.map(siteName).join(', '),
                  })
            }
          />
          <Column
            header={t('paymentMethods.status')}
            body={(m: ConfiguredPaymentMethod) => <ActiveBadge active={m.is_active} />}
          />
          <Column
            header={t('common.actions')}
            body={(m: ConfiguredPaymentMethod) => (
              <RowActions
                actions={[
                  {
                    key: 'edit',
                    label: t('actions.edit'),
                    icon: 'pi pi-pencil',
                    onClick: () => setEditing(m),
                  },
                  {
                    key: 'accounts',
                    label: t('receiving.accounts'),
                    icon: 'pi pi-id-card',
                    hidden: m.kind === 'CASH',
                    onClick: () => setAccounts(m),
                  },
                  {
                    key: 'sites',
                    label: t('paymentMethods.sites'),
                    icon: 'pi pi-building',
                    onClick: () => setSites(m),
                  },
                  {
                    key: 'status',
                    label: t(m.is_active ? 'actions.deactivate' : 'actions.activate'),
                    icon: m.is_active ? 'pi pi-ban' : 'pi pi-check-circle',
                    danger: m.is_active,
                    onClick: () => toggle(m),
                  },
                ]}
              />
            )}
          />
        </DataTable>
      )}
      {editing !== undefined && (
        <MethodDialog method={editing} onClose={() => setEditing(undefined)} />
      )}
      {sites && <SitesDialog method={sites} onClose={() => setSites(null)} />}
      {accounts && (
        <PaymentAccountsDialog
          method={accounts}
          site={focusSite}
          onClose={() => setAccounts(null)}
        />
      )}
    </>
  );
}
