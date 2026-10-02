import { Card } from 'primereact/card';
import { Column } from 'primereact/column';
import { DataTable } from 'primereact/datatable';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatQuantity } from '@/shared/lib/decimal';
import { formatDate } from '@/shared/lib/format';
import { EmptyState } from '@/shared/ui/EmptyState';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { LoadingState } from '@/shared/ui/LoadingState';

import { useLots, type StockLot } from './api';
import { LotStateTag } from './ui';

/**
 * Fiche article — lots de l'article (Lot 3-G) : solde sur les sites visibles (le serveur limite
 * les sites), péremption et état calculés par le serveur ; échéance la plus proche d'abord.
 */
export default function ArticleLotsPanel({ articleId }: { articleId: string }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { capabilities } = useCapabilities();
  const { locale } = capabilities.tenant;
  const lots = useLots(
    new URLSearchParams({ article_id: articleId, sort: 'expiry_date', limit: '100' }).toString(),
  );

  return (
    <section className="sm-block" aria-labelledby="article-lots-title">
      <div className="sm-section-header">
        <h2 id="article-lots-title">{t('lots.title')}</h2>
      </div>
      {lots.isPending ? (
        <LoadingState />
      ) : lots.isError ? (
        <ErrorMessage error={lots.error} onRetry={() => void lots.refetch()} />
      ) : (
        <Card>
          <DataTable
            className="sm-table"
            value={lots.data.items}
            dataKey="id"
            onRowClick={(e) => void navigate(`/stock/lots/${(e.data as StockLot).id}`)}
            rowClassName={() => 'sm-row-clickable'}
            emptyMessage={<EmptyState icon="pi pi-box" title={t('lots.emptyArticle')} />}
          >
            <Column field="number" header={t('lots.lotNumber')} />
            <Column
              header={t('lots.expiryDate')}
              body={(l: StockLot) =>
                l.expiry_date ? formatDate(l.expiry_date, locale, 'UTC') : '—'
              }
            />
            <Column
              header={t('lots.state')}
              body={(l: StockLot) => <LotStateTag state={l.state} />}
            />
            <Column
              header={t('stock.quantity')}
              headerClassName="sm-num"
              bodyClassName="sm-num"
              body={(l: StockLot) => `${formatQuantity(l.quantity, locale)} ${l.unit}`}
            />
          </DataTable>
        </Card>
      )}
    </section>
  );
}
