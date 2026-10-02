import { Button } from 'primereact/button';
import { Card } from 'primereact/card';
import { Column } from 'primereact/column';
import { DataTable } from 'primereact/datatable';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatQuantity } from '@/shared/lib/decimal';
import { formatDate, formatDateTime } from '@/shared/lib/format';
import { INITIAL_TABLE, toQueryString, type TableState } from '@/shared/lib/serverTable';
import { EmptyState } from '@/shared/ui/EmptyState';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { LoadingState } from '@/shared/ui/LoadingState';
import { PageHeader } from '@/shared/ui/PageHeader';
import { ServerTable } from '@/shared/ui/ServerTable';
import { DocumentStatusBadge } from '@/shared/ui/StatusBadge';

import {
  useDocuments,
  useLot,
  useMovements,
  type Movement,
  type StockEntry,
  type StockLotDetail,
} from './api';
import { LotStateTag } from './ui';

/** Réceptions du lot (``GET /stock/entries?lot_id=``, ``stock.entry.view``). */
function LotReceptions({ lotId }: { lotId: string }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { capabilities } = useCapabilities();
  const [table, setTable] = useState<TableState>({ ...INITIAL_TABLE, rows: 10 });
  const entries = useDocuments<StockEntry>('entries', toQueryString(table, { lot_id: lotId }));
  return (
    <section className="sm-block" aria-labelledby="lot-receptions-title">
      <div className="sm-section-header">
        <h2 id="lot-receptions-title">{t('lots.receptions')}</h2>
      </div>
      <ServerTable
        query={entries}
        table={table}
        onTableChange={setTable}
        minWidth="30rem"
        onRowClick={(e) => void navigate(`/stock/entries/${e.id}`)}
        empty={<EmptyState icon="pi pi-sign-in" title={t('lots.noReceptions')} />}
      >
        <Column field="number" header={t('stock.number')} />
        <Column
          header={t('stock.date')}
          body={(e: StockEntry) => formatDate(e.operation_date, capabilities.tenant.locale, 'UTC')}
        />
        <Column field="site_name" header={t('layout.site')} />
        <Column
          header={t('entries.supplierOrKind')}
          body={(e: StockEntry) =>
            e.kind === 'INITIAL_STOCK' ? t('entries.kinds.INITIAL_STOCK') : e.supplier_name
          }
        />
        <Column
          header={t('stock.status')}
          body={(e: StockEntry) => <DocumentStatusBadge status={e.status} />}
        />
      </ServerTable>
    </section>
  );
}

/** Mouvements du lot (``GET /stock/movements?lot_id=``, ``stock.movement.view``). */
function LotMovements({ lotId }: { lotId: string }) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const { locale, timezone } = capabilities.tenant;
  const [table, setTable] = useState<TableState>({ ...INITIAL_TABLE, rows: 10 });
  const movements = useMovements(toQueryString(table, { lot_id: lotId }));
  return (
    <section className="sm-block" aria-labelledby="lot-movements-title">
      <div className="sm-section-header">
        <h2 id="lot-movements-title">{t('lots.movements')}</h2>
      </div>
      <ServerTable
        query={movements}
        table={table}
        onTableChange={setTable}
        minWidth="30rem"
        empty={<EmptyState icon="pi pi-history" title={t('lots.noMovements')} />}
      >
        <Column
          header={t('stock.date')}
          body={(m: Movement) => formatDateTime(m.occurred_at, locale, timezone)}
        />
        <Column field="site_name" header={t('layout.site')} />
        <Column
          header={t('stock.movementType')}
          body={(m: Movement) => t(`stock.movementTypes.${m.movement_type}`)}
        />
        <Column field="document_number" header={t('stock.document')} />
        <Column
          header={t('stock.quantity')}
          headerClassName="sm-num"
          bodyClassName="sm-num"
          body={(m: Movement) =>
            `${m.quantity.startsWith('-') ? '' : '+'}${formatQuantity(m.quantity, locale)} ${m.unit}`
          }
        />
      </ServerTable>
    </section>
  );
}

/** Fiche lot (Lot 3-G) : informations figées, état de péremption, soldes par site visible. */
export default function LotDetailPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { id } = useParams();
  const { can, capabilities } = useCapabilities();
  const { locale } = capabilities.tenant;
  const lot = useLot(id);

  if (lot.isPending) return <LoadingState />;
  if (lot.isError) return <ErrorMessage error={lot.error} onRetry={() => void lot.refetch()} />;
  const l = lot.data;
  const rows: [string, string | null][] = [
    [t('stock.article'), `${l.article_reference} — ${l.article_designation}`],
    [t('lots.expiryDate'), l.expiry_date ? formatDate(l.expiry_date, locale, 'UTC') : null],
    [
      t('lots.manufacturingDate'),
      l.manufacturing_date ? formatDate(l.manufacturing_date, locale, 'UTC') : null,
    ],
    [t('lots.totalQuantity'), `${formatQuantity(l.quantity, locale)} ${l.unit}`],
  ];

  return (
    <>
      <PageHeader
        title={t('lots.lotNumberShort', { number: l.number })}
        breadcrumbs={[{ label: t('lots.title'), to: '/stock/lots' }, { label: l.number }]}
        actions={
          <div className="sm-tags">
            <LotStateTag state={l.state} />
            {can('catalog.article.view') && (
              <Button
                icon="pi pi-box"
                label={t('lots.openArticle')}
                outlined
                onClick={() => void navigate(`/catalog/articles/${l.article_id}`)}
              />
            )}
          </div>
        }
      />
      <Card className="sm-block">
        <dl className="sm-details">
          {rows
            .filter(([, value]) => value)
            .map(([label, value]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd>{value}</dd>
              </div>
            ))}
        </dl>
        <p className="sm-help">{t('lots.frozenHelp')}</p>
      </Card>
      <section className="sm-block" aria-labelledby="lot-balances-title">
        <div className="sm-section-header">
          <h2 id="lot-balances-title">{t('lots.balances')}</h2>
        </div>
        <Card>
          <DataTable
            className="sm-table"
            value={l.balances}
            dataKey="site_id"
            emptyMessage={<EmptyState icon="pi pi-warehouse" title={t('lots.noBalance')} />}
          >
            <Column field="site_name" header={t('layout.site')} />
            <Column
              header={t('stock.quantity')}
              headerClassName="sm-num"
              bodyClassName="sm-num"
              body={(b: StockLotDetail['balances'][number]) =>
                `${formatQuantity(b.quantity, locale)} ${l.unit}`
              }
            />
          </DataTable>
        </Card>
      </section>
      {can('stock.entry.view') && <LotReceptions lotId={l.id} />}
      {can('stock.movement.view') && <LotMovements lotId={l.id} />}
    </>
  );
}
