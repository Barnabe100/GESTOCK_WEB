import { Button } from 'primereact/button';
import { Card } from 'primereact/card';
import { TabPanel, TabView } from 'primereact/tabview';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatDateTime } from '@/shared/lib/format';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { LoadingState } from '@/shared/ui/LoadingState';
import { PageHeader } from '@/shared/ui/PageHeader';
import { ActiveBadge } from '@/shared/ui/StatusBadge';

import { useSupplier } from './api';
import {
  SupplierHistory,
  SupplierMainArticles,
  SupplierReceivedArticles,
  SupplierReceptions,
  SupplierSummaryMetrics,
} from './SupplierActivity';
import { SupplierDialog } from './SupplierDialog';
import { useSupplierStatus } from './useSupplierStatus';

/**
 * Fiche fournisseur (Lot 3-E) : coordonnées, suivi, synthèse et réceptions (`stock.entry.view`,
 * sites visibles), articles reçus et articles dont il est le fournisseur principal
 * (`catalog.article.view`), chronologie (`audit.log.view`). Lecture seule hors des actions
 * existantes (modifier, activer / désactiver). Chiffres et coûts calculés et filtrés par le
 * serveur : l'interface ne fait que masquer pour l'ergonomie.
 */
export default function SupplierDetailPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { id } = useParams();
  const { can, capabilities, hasModule } = useCapabilities();
  const supplier = useSupplier(id);
  const { toggle, pending } = useSupplierStatus();
  const [editing, setEditing] = useState(false);
  const { locale, timezone } = capabilities.tenant;

  if (supplier.isPending) return <LoadingState />;
  if (supplier.isError) {
    return <ErrorMessage error={supplier.error} onRetry={() => void supplier.refetch()} />;
  }
  const s = supplier.data;
  const receptions = hasModule('stock') && can('stock.entry.view');
  const catalog = hasModule('catalog') && can('catalog.article.view');
  const history = can('audit.log.view');
  const contact: [string, string | null][] = [
    [t('suppliers.contact'), s.contact_name],
    [t('suppliers.phone'), s.phone],
    [t('suppliers.email'), s.email],
    [t('suppliers.address'), s.address],
    [t('suppliers.city'), s.city],
    [t('suppliers.country'), s.country],
  ];
  const known = contact.filter(([, value]) => value);
  const tabs = [
    receptions && { key: 'receptions', content: <SupplierReceptions supplierId={s.id} /> },
    receptions && { key: 'received', content: <SupplierReceivedArticles supplierId={s.id} /> },
    catalog && { key: 'main', content: <SupplierMainArticles supplierId={s.id} /> },
    history && { key: 'history', content: <SupplierHistory supplierId={s.id} /> },
  ].filter((tab) => tab !== false);

  return (
    <>
      <PageHeader
        title={s.name}
        breadcrumbs={[{ label: t('suppliers.title'), to: '/suppliers' }, { label: s.name }]}
        actions={
          <div className="sm-tags">
            <ActiveBadge active={s.is_active} />
            {can('suppliers.supplier.update') && (
              <Button
                icon="pi pi-pencil"
                label={t('actions.edit')}
                outlined
                onClick={() => setEditing(true)}
              />
            )}
            {can('suppliers.supplier.status') && (
              <Button
                icon={s.is_active ? 'pi pi-ban' : 'pi pi-check'}
                label={t(s.is_active ? 'actions.deactivate' : 'actions.activate')}
                severity={s.is_active ? 'danger' : undefined}
                outlined
                loading={pending}
                onClick={() => toggle(s)}
              />
            )}
          </div>
        }
      />
      <div className="sm-grid">
        <Card title={t('suppliers.general')}>
          {known.length === 0 ? (
            <p className="sm-muted">{t('suppliers.noContact')}</p>
          ) : (
            <dl className="sm-details">
              {known.map(([label, value]) => (
                <div key={label}>
                  <dt>{label}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
          )}
        </Card>
        <Card title={t('suppliers.tracking')}>
          <dl className="sm-details">
            <div>
              <dt>{t('suppliers.status')}</dt>
              <dd>{t(s.is_active ? 'common.active' : 'common.inactive')}</dd>
            </div>
            <div>
              <dt>{t('suppliers.createdAt')}</dt>
              <dd>{formatDateTime(s.created_at, locale, timezone)}</dd>
            </div>
            <div>
              <dt>{t('suppliers.updatedAt')}</dt>
              <dd>{formatDateTime(s.updated_at, locale, timezone)}</dd>
            </div>
          </dl>
          {!s.is_active && <p className="sm-help">{t('suppliers.inactiveHelp')}</p>}
        </Card>
      </div>
      {s.notes && (
        <Card title={t('suppliers.notes')} className="sm-block">
          <p className="sm-pre-line">{s.notes}</p>
        </Card>
      )}
      {receptions && <SupplierSummaryMetrics supplierId={s.id} />}
      {tabs.length > 0 && (
        <section className="sm-block" aria-label={t('suppliers.activity')}>
          {/* Onglets autorisés seulement ; seul l'onglet actif est rendu (et interroge le serveur). */}
          <TabView>
            {tabs.map((tab) => (
              <TabPanel key={tab.key} header={t(`suppliers.tabs.${tab.key}`)}>
                {tab.content}
              </TabPanel>
            ))}
          </TabView>
        </section>
      )}
      <div className="sm-dialog-actions">
        <Button label={t('actions.back')} text onClick={() => void navigate('/suppliers')} />
      </div>
      {editing && <SupplierDialog supplier={s} onClose={() => setEditing(false)} />}
    </>
  );
}
