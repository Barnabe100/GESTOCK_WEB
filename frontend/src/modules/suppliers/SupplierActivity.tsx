import { Column } from 'primereact/column';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { COST_VIEW, useArticles, type Article } from '@/modules/catalog/api';
import {
  useDocuments,
  useSupplierReceivedArticles,
  useSupplierReceptionSummary,
  type StockEntry,
  type SupplierReceivedArticle,
} from '@/modules/stock/api';
import { formatCost, formatMoney, formatQuantity } from '@/shared/lib/decimal';
import { formatDate, formatDateTime } from '@/shared/lib/format';
import { INITIAL_TABLE, toQueryString, type TableState } from '@/shared/lib/serverTable';
import { EmptyState } from '@/shared/ui/EmptyState';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { LoadingState } from '@/shared/ui/LoadingState';
import { MetricCard } from '@/shared/ui/MetricCard';
import { ServerTable } from '@/shared/ui/ServerTable';
import { ActiveBadge, DocumentStatusBadge } from '@/shared/ui/StatusBadge';

import { useSupplierHistory, type SupplierEvent } from './api';

/** Dates d'opération (jour métier, sans heure) : affichées telles quelles. */
const day = (value: string, locale: string) => formatDate(value, locale, 'UTC');

/**
 * Synthèse des réceptions VALIDÉES du fournisseur (sites visibles), calculée par le serveur ;
 * brouillons et réceptions annulées exclus. Total : seulement avec cost_view (absent sinon).
 */
export function SupplierSummaryMetrics({ supplierId }: { supplierId: string }) {
  const { t } = useTranslation();
  const { can, capabilities } = useCapabilities();
  const { currency, locale } = capabilities.tenant;
  const summary = useSupplierReceptionSummary(supplierId, true);
  if (summary.isError) {
    return <ErrorMessage error={summary.error} onRetry={() => void summary.refetch()} />;
  }
  const data = summary.data;
  return (
    <section className="sm-block" aria-labelledby="supplier-summary-title">
      <div className="sm-section-header">
        <h2 id="supplier-summary-title">{t('suppliers.summary.title')}</h2>
      </div>
      <p className="sm-help">{t('suppliers.summary.help')}</p>
      <div className="sm-metrics" role="group" aria-label={t('suppliers.summary.title')}>
        <MetricCard
          icon="pi pi-inbox"
          value={data ? data.validated_count : '—'}
          label={t('suppliers.summary.count')}
        />
        <MetricCard
          icon="pi pi-calendar"
          value={data?.last_received_on ? day(data.last_received_on, locale) : '—'}
          label={t('suppliers.summary.last')}
        />
        {can(COST_VIEW) && data?.received_total !== undefined && (
          <MetricCard
            icon="pi pi-wallet"
            value={formatMoney(data.received_total, currency, locale)}
            label={t('suppliers.summary.total')}
          />
        )}
      </div>
    </section>
  );
}

/** Réceptions du fournisseur : toutes, brouillons et annulées compris (statut affiché). */
export function SupplierReceptions({ supplierId }: { supplierId: string }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { can, capabilities } = useCapabilities();
  const { currency, locale } = capabilities.tenant;
  const [table, setTable] = useState<TableState>({
    ...INITIAL_TABLE,
    sortField: 'operation_date',
    sortOrder: -1,
  });
  const receptions = useDocuments<StockEntry>(
    'entries',
    toQueryString(table, { supplier_id: supplierId, kind: 'PURCHASE' }),
  );
  const showSite = capabilities.site === null && capabilities.sites.length > 1;
  return (
    <>
      <p className="sm-help">{t('suppliers.receptions.help')}</p>
      <ServerTable
        query={receptions}
        table={table}
        onTableChange={setTable}
        onRowClick={(e: StockEntry) => void navigate(`/stock/entries/${e.id}`)}
        empty={<EmptyState icon="pi pi-inbox" title={t('suppliers.receptions.empty')} />}
        minWidth="40rem"
      >
        <Column field="number" header={t('stock.number')} sortable />
        <Column
          field="operation_date"
          header={t('stock.date')}
          sortable
          body={(e: StockEntry) => day(e.operation_date, locale)}
        />
        {showSite && <Column field="site_name" header={t('layout.site')} />}
        <Column
          header={t('suppliers.receptions.reference')}
          body={(e: StockEntry) => e.document_reference ?? '—'}
        />
        <Column
          header={t('stock.status')}
          body={(e: StockEntry) => <DocumentStatusBadge status={e.status} />}
        />
        {can(COST_VIEW) && (
          <Column
            header={t('stock.total')}
            headerClassName="sm-num"
            bodyClassName="sm-num"
            body={(e: StockEntry) =>
              e.total_amount ? formatMoney(e.total_amount, currency, locale) : '—'
            }
          />
        )}
      </ServerTable>
    </>
  );
}

/** Articles ayant au moins une réception VALIDÉE du fournisseur (calculés par le serveur). */
export function SupplierReceivedArticles({ supplierId }: { supplierId: string }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { can, capabilities } = useCapabilities();
  const { currency, locale } = capabilities.tenant;
  const [table, setTable] = useState<TableState>({
    ...INITIAL_TABLE,
    sortField: 'last_received_on',
    sortOrder: -1,
  });
  const articles = useSupplierReceivedArticles(supplierId, toQueryString(table));
  const canOpen = can('catalog.article.view');
  return (
    <>
      <p className="sm-help">{t('suppliers.received.help')}</p>
      <ServerTable
        query={articles}
        table={table}
        onTableChange={setTable}
        dataKey="article_id"
        onRowClick={
          canOpen
            ? (a: SupplierReceivedArticle) => void navigate(`/catalog/articles/${a.article_id}`)
            : undefined
        }
        empty={<EmptyState icon="pi pi-box" title={t('suppliers.received.empty')} />}
        minWidth="44rem"
      >
        <Column
          header={t('suppliers.articles.article')}
          sortField="designation"
          sortable
          body={(a: SupplierReceivedArticle) => (
            <div>
              <div>{a.article_designation}</div>
              <small className="sm-muted">{a.article_reference}</small>
            </div>
          )}
        />
        <Column
          field="last_received_on"
          header={t('suppliers.received.last')}
          sortable
          body={(a: SupplierReceivedArticle) => (
            <div>
              <div>{day(a.last_received_on, locale)}</div>
              <small className="sm-muted">{a.last_entry_number}</small>
            </div>
          )}
        />
        <Column
          field="receipt_count"
          header={t('suppliers.received.count')}
          sortable
          headerClassName="sm-num"
          bodyClassName="sm-num"
        />
        <Column
          field="received_base_quantity"
          header={t('suppliers.received.quantity')}
          sortable
          headerClassName="sm-num"
          bodyClassName="sm-num"
          body={(a: SupplierReceivedArticle) =>
            `${formatQuantity(a.received_base_quantity, locale)} ${a.unit}`
          }
        />
        {can(COST_VIEW) && (
          <Column
            header={t('suppliers.received.lastCost')}
            headerClassName="sm-num"
            bodyClassName="sm-num"
            body={(a: SupplierReceivedArticle) =>
              a.last_unit_cost === undefined
                ? '—'
                : t('suppliers.received.costPer', {
                    cost: formatCost(a.last_unit_cost, currency, locale),
                    unit: a.unit,
                  })
            }
          />
        )}
      </ServerTable>
    </>
  );
}

/** Articles dont ce fournisseur est le fournisseur principal (catalogue). */
export function SupplierMainArticles({ supplierId }: { supplierId: string }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [table, setTable] = useState<TableState>({
    ...INITIAL_TABLE,
    sortField: 'designation',
    sortOrder: 1,
  });
  const articles = useArticles(toQueryString(table, { supplier_id: supplierId }));
  return (
    <>
      <p className="sm-help">{t('suppliers.main.help')}</p>
      <ServerTable
        query={articles}
        table={table}
        onTableChange={setTable}
        onRowClick={(a: Article) => void navigate(`/catalog/articles/${a.id}`)}
        empty={<EmptyState icon="pi pi-box" title={t('suppliers.main.empty')} />}
        minWidth="36rem"
      >
        <Column field="reference" header={t('articles.reference')} sortable />
        <Column field="designation" header={t('articles.designation')} sortable />
        <Column field="category_name" header={t('articles.category')} />
        <Column
          header={t('articles.status')}
          body={(a: Article) => <ActiveBadge active={a.is_active} />}
        />
      </ServerTable>
    </>
  );
}

const FIELD_LABELS: Record<string, string> = {
  name: 'suppliers.name',
  contact_name: 'suppliers.contact',
  phone: 'suppliers.phone',
  email: 'suppliers.email',
  address: 'suppliers.address',
  city: 'suppliers.city',
  country: 'suppliers.country',
  notes: 'suppliers.notes',
};

/**
 * Chronologie du fournisseur (`audit.log.view`) : uniquement les évènements réellement
 * journalisés (création, modifications, changements de statut), du plus ancien au plus récent.
 */
export function SupplierHistory({ supplierId }: { supplierId: string }) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const { locale, timezone } = capabilities.tenant;
  const history = useSupplierHistory(supplierId);

  const details = (event: SupplierEvent): string | null => {
    if (event.action !== 'supplier.updated') return null;
    const fields = Object.keys(event.data).map((key) =>
      FIELD_LABELS[key] ? t(FIELD_LABELS[key]) : key,
    );
    return fields.length ? t('suppliers.history.changed', { fields: fields.join(', ') }) : null;
  };

  if (history.isPending) return <LoadingState />;
  if (history.isError) {
    return <ErrorMessage error={history.error} onRetry={() => void history.refetch()} />;
  }
  return (
    <>
      <p className="sm-help">{t('suppliers.history.help')}</p>
      {history.data.length === 0 ? (
        <EmptyState icon="pi pi-history" title={t('suppliers.history.empty')} />
      ) : (
        <ol className="sm-timeline" aria-label={t('suppliers.tabs.history')}>
          {history.data.map((event) => (
            <li key={event.id} className="sm-timeline-item">
              <time dateTime={event.occurred_at} className="sm-help">
                {formatDateTime(event.occurred_at, locale, timezone)}
              </time>
              <strong>
                {t(`suppliers.history.actions.${event.action}`, { defaultValue: event.action })}
              </strong>
              <span>{event.user_name ?? t('suppliers.history.system')}</span>
              {details(event) && <span className="sm-help">{details(event)}</span>}
            </li>
          ))}
        </ol>
      )}
    </>
  );
}
