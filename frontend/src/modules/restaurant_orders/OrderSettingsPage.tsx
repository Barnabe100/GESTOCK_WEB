import { Button } from 'primereact/button';
import { Dropdown } from 'primereact/dropdown';
import { InputNumber } from 'primereact/inputnumber';
import { Message } from 'primereact/message';
import { SelectButton } from 'primereact/selectbutton';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { translateError } from '@/shared/lib/errors';
import { formatDateTime } from '@/shared/lib/format';
import { FormField } from '@/shared/ui/FormField';
import { LoadingState } from '@/shared/ui/LoadingState';
import { PageHeader } from '@/shared/ui/PageHeader';
import { useToast } from '@/shared/ui/toast';

import { useOrderMutations, useOrderSettings, type OrderSettings, type PaymentTiming } from './api';

const TIMINGS: PaymentTiming[] = ['AT_END', 'AT_ORDER'];
const MAX_MINUTES = 1440;

/**
 * Réglages des commandes d'UN site (`restaurant.orders.settings.manage`, revérifiée par le
 * serveur pour ce site) : moment du paiement (appliqué aux commandes créées ENSUITE), protection
 * d'une prise en charge et délai entre deux prises. L'acceptation automatique des commandes QR
 * est sans effet avant le palier QR : affichée, non modifiable.
 */
export default function OrderSettingsPage() {
  const { t } = useTranslation();
  const { capabilities, siteId: selected } = useCapabilities();
  const only = capabilities.sites.length === 1 ? (capabilities.sites[0]?.id ?? null) : null;
  const [siteId, setSiteId] = useState<string | null>(selected ?? only);
  const settings = useOrderSettings(siteId);
  return (
    <>
      <PageHeader
        title={t('restaurantOrders.settings.title')}
        description={t('restaurantOrders.settings.help')}
        breadcrumbs={[
          { label: t('restaurantOrders.title'), to: '/restaurant/orders' },
          { label: t('restaurantOrders.settings.title') },
        ]}
      />
      {selected === null && capabilities.sites.length > 1 && (
        <div className="sm-form-grid">
          <FormField id="order-settings-site" label={t('layout.site')} required>
            <Dropdown
              inputId="order-settings-site"
              value={siteId}
              onChange={(e) => setSiteId(e.value as string | null)}
              options={capabilities.sites.map((s) => ({ value: s.id, label: s.name }))}
              placeholder={t('stock.chooseSite')}
            />
          </FormField>
        </div>
      )}
      {siteId === null ? (
        <p className="sm-help">{t('restaurantOrders.chooseSiteFirst')}</p>
      ) : settings.isPending ? (
        <LoadingState />
      ) : settings.isError ? (
        <Message severity="error" text={translateError(t, settings.error)} />
      ) : (
        // Une clé par site et par version : le formulaire repart des valeurs du serveur.
        <SettingsForm key={`${siteId}-${settings.data.updated_at}`} settings={settings.data} />
      )}
    </>
  );
}

function SettingsForm({ settings }: { settings: OrderSettings }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { capabilities } = useCapabilities();
  const { locale, timezone } = capabilities.tenant;
  const { updateSettings } = useOrderMutations();
  const [timing, setTiming] = useState<PaymentTiming>(settings.payment_timing);
  const [protection, setProtection] = useState<number>(settings.claim_protection_minutes);
  const [cooldown, setCooldown] = useState<number>(settings.claim_cooldown_minutes);
  const [error, setError] = useState<string | null>(null);

  const submit = () => {
    setError(null);
    updateSettings.mutate(
      {
        siteId: settings.site_id,
        input: {
          payment_timing: timing,
          claim_protection_minutes: protection,
          claim_cooldown_minutes: cooldown,
        },
      },
      {
        onSuccess: () => toast.success(t('restaurantOrders.settings.saved')),
        onError: (failure) => setError(translateError(t, failure)),
      },
    );
  };

  return (
    <div className="sm-form">
      <div className="sm-form-grid">
        <FormField
          id="order-settings-timing"
          label={t('restaurantOrders.paymentTiming')}
          help={t('restaurantOrders.settings.timingHelp')}
          required
        >
          <SelectButton
            id="order-settings-timing"
            value={timing}
            allowEmpty={false}
            onChange={(e) => setTiming(e.value as PaymentTiming)}
            options={TIMINGS.map((v) => ({
              value: v,
              label: t(`restaurantOrders.paymentTimings.${v}`),
            }))}
          />
        </FormField>
        <FormField
          id="order-settings-protection"
          label={t('restaurantOrders.settings.protection')}
          help={t('restaurantOrders.settings.protectionHelp')}
          required
        >
          <InputNumber
            inputId="order-settings-protection"
            value={protection}
            min={0}
            max={MAX_MINUTES}
            suffix=" min"
            onValueChange={(e) => setProtection(e.value ?? 0)}
          />
        </FormField>
        <FormField
          id="order-settings-cooldown"
          label={t('restaurantOrders.settings.cooldown')}
          help={t('restaurantOrders.settings.cooldownHelp')}
          required
        >
          <InputNumber
            inputId="order-settings-cooldown"
            value={cooldown}
            min={0}
            max={MAX_MINUTES}
            suffix=" min"
            onValueChange={(e) => setCooldown(e.value ?? 0)}
          />
        </FormField>
      </div>
      <dl className="sm-details">
        <div>
          <dt>{t('restaurantOrders.settings.qrAutoAccept')}</dt>
          <dd>
            {settings.qr_auto_accept ? t('common.yes') : t('common.no')}{' '}
            <small className="sm-help">{t('restaurantOrders.settings.qrLater')}</small>
          </dd>
        </div>
        <div>
          <dt>{t('restaurantOrders.settings.updatedAt')}</dt>
          <dd>{formatDateTime(settings.updated_at, locale, timezone)}</dd>
        </div>
      </dl>
      {error && <Message severity="error" text={error} />}
      <div className="sm-form-actions">
        <Button
          type="button"
          icon="pi pi-check"
          label={t('actions.save')}
          loading={updateSettings.isPending}
          onClick={submit}
        />
      </div>
    </div>
  );
}
