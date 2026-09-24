import { Button } from 'primereact/button';
import { Card } from 'primereact/card';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatMoney } from '@/shared/lib/decimal';
import { formatDateTime } from '@/shared/lib/format';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { LoadingState } from '@/shared/ui/LoadingState';
import { PageHeader } from '@/shared/ui/PageHeader';
import { ActiveBadge } from '@/shared/ui/StatusBadge';

import { useCustomer } from './api';
import { CustomerDialog } from './CustomerDialog';
import { useCustomerStatus } from './useCustomerStatus';

/**
 * Fiche client. Les indicateurs commerciaux (achats, montant dû, dernière vente…) seront
 * ajoutés par les modules Ventes / Créances quand ces données existeront : aucun chiffre fictif.
 */
export default function CustomerDetailPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { id } = useParams();
  const { can, capabilities } = useCapabilities();
  const customer = useCustomer(id);
  const { toggle, pending } = useCustomerStatus();
  const [editing, setEditing] = useState(false);
  const { currency, locale, timezone } = capabilities.tenant;

  if (customer.isPending) {
    return <LoadingState />;
  }
  if (customer.isError) {
    return <ErrorMessage error={customer.error} onRetry={() => void customer.refetch()} />;
  }
  const c = customer.data;
  const general: [string, string | null][] = [
    [t('customers.code'), c.code],
    [t('customers.type'), t(`customers.types.${c.customer_type}`)],
    [t('customers.legalName'), c.legal_name],
    [t('customers.tax_id'), c.tax_id],
    [t('customers.phone'), c.phone],
    [t('customers.phone2'), c.phone2],
    [t('customers.email'), c.email],
    [t('customers.address'), c.address],
    [t('customers.city'), c.city],
    [t('customers.country'), c.country],
    [
      t('customers.creditLimit'),
      c.credit_limit === null ? null : formatMoney(c.credit_limit, currency, locale),
    ],
  ];

  return (
    <>
      <PageHeader
        title={c.name}
        breadcrumbs={[{ label: t('customers.title'), to: '/customers' }, { label: c.name }]}
        actions={
          <div className="sm-tags">
            <ActiveBadge active={c.is_active} />
            {can('customers.customer.update') && (
              <Button
                icon="pi pi-pencil"
                label={t('actions.edit')}
                outlined
                onClick={() => setEditing(true)}
              />
            )}
            {can('customers.customer.status') && (
              <Button
                icon={c.is_active ? 'pi pi-ban' : 'pi pi-check'}
                label={t(c.is_active ? 'actions.deactivate' : 'actions.activate')}
                severity={c.is_active ? 'danger' : undefined}
                outlined
                loading={pending}
                onClick={() => toggle(c)}
              />
            )}
          </div>
        }
      />
      <div className="sm-grid">
        <Card title={t('customers.general')}>
          <dl className="sm-details">
            {general
              .filter(([, value]) => value)
              .map(([label, value]) => (
                <div key={label}>
                  <dt>{label}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
          </dl>
        </Card>
        <Card title={t('customers.tracking')}>
          <dl className="sm-details">
            <div>
              <dt>{t('customers.status')}</dt>
              <dd>{t(c.is_active ? 'common.active' : 'common.inactive')}</dd>
            </div>
            <div>
              <dt>{t('customers.createdAt')}</dt>
              <dd>{formatDateTime(c.created_at, locale, timezone)}</dd>
            </div>
            <div>
              <dt>{t('customers.updatedAt')}</dt>
              <dd>{formatDateTime(c.updated_at, locale, timezone)}</dd>
            </div>
          </dl>
          {!c.is_active && <p className="sm-help">{t('customers.inactiveHelp')}</p>}
        </Card>
      </div>
      {c.notes && (
        <Card title={t('customers.notes')} className="sm-block">
          <p className="sm-pre-line">{c.notes}</p>
        </Card>
      )}
      <div className="sm-dialog-actions">
        <Button label={t('actions.back')} text onClick={() => void navigate('/customers')} />
      </div>
      {editing && <CustomerDialog customer={c} onClose={() => setEditing(false)} />}
    </>
  );
}
