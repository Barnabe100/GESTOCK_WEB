import { Button } from 'primereact/button';
import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { Message } from 'primereact/message';
import { SelectButton } from 'primereact/selectbutton';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { MENU_VIEW } from '@/modules/restaurant_menu/api';
import { CustomerPicker, type CustomerOption } from '@/modules/sales/CustomerPicker';
import { isWholeQuantity } from '@/shared/lib/decimal';
import { FormField } from '@/shared/ui/FormField';
import { PageHeader } from '@/shared/ui/PageHeader';

import { newKey, SERVICE_MODES, useOrderMutations, type ServiceMode } from './api';
import { MenuCart, orderError, toLineInputs, type CartLine } from './ui';

/**
 * Prise de commande (T1) par le personnel : mode de service obligatoire, nom d'appel et client
 * facultatifs, lignes choisies parmi les éléments COMMANDABLES du menu du site. Le serveur fige
 * les prix, attribue le numéro du jour et revérifie tout ; ni vente, ni paiement, ni stock. Une
 * clé d'idempotence par saisie : une double soumission ne crée qu'une commande.
 */
export default function OrderCreatePage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { can, capabilities, hasModule, siteId: selected } = useCapabilities();
  const only = capabilities.sites.length === 1 ? (capabilities.sites[0]?.id ?? null) : null;
  const [siteId, setSiteId] = useState<string | null>(selected ?? only);
  const [mode, setMode] = useState<ServiceMode>('ON_SITE');
  const [callName, setCallName] = useState('');
  const [customer, setCustomer] = useState<CustomerOption | null>(null);
  const [lines, setLines] = useState<CartLine[]>([]);
  const [key] = useState(newKey);
  const [error, setError] = useState<string | null>(null);
  const { create } = useOrderMutations();
  const withCustomers = hasModule('customers') && can('customers.customer.view');
  const invalidQuantity = lines.some((l) => l.quantity === '' || Number(l.quantity) <= 0);

  const submit = () => {
    setError(null);
    create.mutate(
      {
        site_id: siteId,
        service_mode: mode,
        customer_id: customer?.id ?? null,
        call_name: callName.trim() || null,
        lines: toLineInputs(lines),
        idempotency_key: key,
      },
      {
        onSuccess: (order) => void navigate(`/restaurant/orders/${order.id}?created=1`),
        // Refus (élément épuisé entre-temps, quantité…) : rien n'a été créé, la saisie reste.
        onError: (failure) => setError(orderError(t, failure)),
      },
    );
  };

  return (
    <>
      <PageHeader
        title={t('restaurantOrders.newOrder')}
        description={t('restaurantOrders.newOrderHelp')}
        breadcrumbs={[
          { label: t('restaurantOrders.title'), to: '/restaurant/orders' },
          { label: t('restaurantOrders.newOrder') },
        ]}
      />
      {!can(MENU_VIEW) ? (
        <Message severity="warn" text={t('restaurantOrders.menuRequired')} />
      ) : (
        <div className="sm-form">
          <div className="sm-form-grid">
            {selected === null && capabilities.sites.length > 1 && (
              <FormField id="order-site" label={t('layout.site')} required>
                <Dropdown
                  inputId="order-site"
                  value={siteId}
                  onChange={(e) => {
                    setSiteId(e.value as string | null);
                    setLines([]);
                  }}
                  options={capabilities.sites.map((s) => ({ value: s.id, label: s.name }))}
                  placeholder={t('stock.chooseSite')}
                />
              </FormField>
            )}
            <FormField id="order-mode" label={t('restaurantOrders.serviceMode')} required>
              <SelectButton
                id="order-mode"
                value={mode}
                allowEmpty={false}
                onChange={(e) => setMode(e.value as ServiceMode)}
                options={SERVICE_MODES.map((m) => ({
                  value: m,
                  label: t(`restaurantOrders.serviceModes.${m}`),
                }))}
              />
            </FormField>
            <FormField
              id="order-call-name"
              label={t('restaurantOrders.callName')}
              help={t('restaurantOrders.callNameHelp')}
            >
              <InputText
                id="order-call-name"
                maxLength={40}
                value={callName}
                onChange={(e) => setCallName(e.target.value)}
              />
            </FormField>
            {withCustomers && (
              <FormField
                id="order-customer"
                label={t('restaurantOrders.customer')}
                help={t('restaurantOrders.customerHelp')}
              >
                <CustomerPicker id="order-customer" value={customer} onChange={setCustomer} />
              </FormField>
            )}
          </div>
          <MenuCart siteId={siteId} lines={lines} onChange={setLines} />
          {lines.some((l) => !isWholeQuantity(l.quantity || '0')) && (
            <p className="sm-help">{t('restaurantOrders.decimalHint')}</p>
          )}
          {error && <Message severity="error" text={error} />}
          <div className="sm-form-actions">
            <Button
              type="button"
              label={t('actions.cancel')}
              text
              onClick={() => void navigate('/restaurant/orders')}
            />
            <Button
              type="button"
              icon="pi pi-check"
              label={t('restaurantOrders.saveOrder')}
              disabled={siteId === null || lines.length === 0 || invalidQuantity}
              loading={create.isPending}
              onClick={submit}
            />
          </div>
        </div>
      )}
    </>
  );
}
