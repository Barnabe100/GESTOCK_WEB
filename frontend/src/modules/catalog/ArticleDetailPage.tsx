import { Button } from 'primereact/button';
import { Card } from 'primereact/card';
import { Column } from 'primereact/column';
import { lazy, Suspense, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatMoney, formatQuantity } from '@/shared/lib/decimal';
import { formatDateTime } from '@/shared/lib/format';
import { INITIAL_TABLE, toQueryString, type TableState } from '@/shared/lib/serverTable';
import { EmptyState } from '@/shared/ui/EmptyState';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { LoadingState } from '@/shared/ui/LoadingState';
import { PageHeader } from '@/shared/ui/PageHeader';
import { ServerTable } from '@/shared/ui/ServerTable';
import { ActiveBadge, StatusBadge } from '@/shared/ui/StatusBadge';

import {
  ARTICLE_UPDATE,
  COST_VIEW,
  PRICE_UPDATE,
  useArticle,
  usePriceHistory,
  type Article,
  type PriceChange,
} from './api';
import { ArticleAssortmentSection } from './ArticleAssortmentSection';
import { ArticleDialog } from './ArticleDialog';
import { BarcodesSection } from './BarcodesSection';
import { PackagingsSection } from './PackagingsSection';

// Lot 3-F : stock et emplacement par site (module Stock), chargé seulement si autorisé.
const ArticleSitesPanel = lazy(() => import('@/modules/stock/ArticleSitesPanel'));
// Lot 3-G : lots de l'article (soldes et péremption sur les sites visibles).
const ArticleLotsPanel = lazy(() => import('@/modules/stock/ArticleLotsPanel'));

/**
 * Historique des prix (Lot 3-A) : entrées du journal d'audit existant, servies par le serveur
 * (habilités aux prix ou au journal d'audit ; prix d'achat seulement avec `cost_view`).
 */
function PriceHistory({ article }: { article: Article }) {
  const { t } = useTranslation();
  const { can, capabilities } = useCapabilities();
  const { currency, locale, timezone } = capabilities.tenant;
  const [table, setTable] = useState<TableState>({ ...INITIAL_TABLE, rows: 10 });
  const history = usePriceHistory(article.id, toQueryString(table), true);
  const money = (value: string | null | undefined) =>
    value === null || value === undefined ? '—' : formatMoney(value, currency, locale);
  const change = (before: string | null | undefined, after: string | null | undefined) =>
    after === null || after === undefined ? '—' : `${money(before)} → ${money(after)}`;

  return (
    <section className="sm-block" aria-labelledby="price-history-title">
      <div className="sm-section-header">
        <h2 id="price-history-title">{t('articles.priceHistory.title')}</h2>
      </div>
      <p className="sm-help">{t('articles.priceHistory.help')}</p>
      <ServerTable
        query={history}
        table={table}
        onTableChange={setTable}
        empty={<EmptyState icon="pi pi-history" title={t('articles.priceHistory.empty')} />}
      >
        <Column
          header={t('articles.priceHistory.date')}
          body={(c: PriceChange) => formatDateTime(c.occurred_at, locale, timezone)}
        />
        <Column
          header={t('articles.priceHistory.user')}
          body={(c: PriceChange) => c.user_name ?? '—'}
        />
        <Column
          header={t('articles.salePrice')}
          body={(c: PriceChange) => change(c.sale_price_before, c.sale_price_after)}
        />
        {can(COST_VIEW) && (
          <Column
            header={t('articles.purchasePrice')}
            body={(c: PriceChange) => change(c.purchase_price_before, c.purchase_price_after)}
          />
        )}
      </ServerTable>
    </section>
  );
}

/** Fiche article : informations, prix (coûts selon la permission), stock, historique des prix. */
export default function ArticleDetailPage() {
  const { t } = useTranslation();
  const { id } = useParams();
  const { can, capabilities, hasModule } = useCapabilities();
  const article = useArticle(id);
  const [editing, setEditing] = useState(false);
  const { currency, locale, timezone } = capabilities.tenant;

  if (article.isPending) return <LoadingState />;
  if (article.isError) {
    return <ErrorMessage error={article.error} onRetry={() => void article.refetch()} />;
  }
  const a = article.data;
  const general: [string, string | null][] = [
    [t('articles.reference'), a.reference],
    [t('articles.category'), a.category_name],
    [
      t('articles.unit'),
      `${a.unit} · ${t(a.decimal_quantity_allowed ? 'articles.decimalQuantity' : 'articles.wholeQuantityOnly')}`,
    ],
    [t('articles.barcode'), a.barcode],
    ...((a.lot_tracked
      ? [
          [
            t('lots.tracking'),
            t(a.expiry_tracked ? 'lots.trackingWithExpiry' : 'lots.trackingWithoutExpiry'),
          ],
        ]
      : []) as [string, string | null][]),
    [t('articles.supplier'), a.main_supplier_name],
    [t('articles.description'), a.description],
  ];
  const prices: [string, string | null][] = [
    [t('articles.salePrice'), formatMoney(a.sale_price, currency, locale)],
    // Prix d'achat : absent de la réponse sans cost_view (contrôle serveur).
    ...(a.purchase_price !== undefined
      ? [[t('articles.purchasePrice'), formatMoney(a.purchase_price, currency, locale)]]
      : []),
  ] as [string, string | null][];
  const stock: [string, string | null][] = a.stock_managed
    ? [
        [t('articles.minStock'), formatQuantity(a.min_stock, locale)],
        [t('articles.maxStock'), a.max_stock === null ? null : formatQuantity(a.max_stock, locale)],
      ]
    : [];
  const details = (rows: [string, string | null][]) => (
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
  );

  return (
    <>
      <PageHeader
        title={a.designation}
        breadcrumbs={[
          { label: t('articles.title'), to: '/catalog/articles' },
          { label: a.reference },
        ]}
        actions={
          <div className="sm-tags">
            <ActiveBadge active={a.is_active} />
            {!a.stock_managed && <StatusBadge tone="info" label={t('articles.notStockManaged')} />}
            {(can(ARTICLE_UPDATE) || can(PRICE_UPDATE)) && (
              <Button
                icon="pi pi-pencil"
                label={t('actions.edit')}
                outlined
                onClick={() => setEditing(true)}
              />
            )}
          </div>
        }
      />
      <div className="sm-grid">
        <Card title={t('articles.general')}>{details(general)}</Card>
        <Card title={t('articles.prices')}>
          {details(prices)}
          <p className="sm-help">
            {t(a.stock_managed ? 'articles.stockManagedHelp' : 'articles.notStockManagedHelp')}
          </p>
          {details(stock)}
          <p className="sm-help">
            {t('articles.updatedAt', { date: formatDateTime(a.updated_at, locale, timezone) })}
          </p>
        </Card>
      </div>
      {/* Recette, étape 1 : sites qui proposent l'article (catalogue ≠ assortiment ≠ stock). */}
      <ArticleAssortmentSection article={a} />
      {a.stock_managed && hasModule('stock') && can('stock.level.view') && (
        <Suspense fallback={<LoadingState />}>
          <ArticleSitesPanel articleId={a.id} reference={a.reference} />
        </Suspense>
      )}
      {a.lot_tracked && hasModule('stock') && can('stock.level.view') && (
        <Suspense fallback={<LoadingState />}>
          <ArticleLotsPanel articleId={a.id} />
        </Suspense>
      )}
      <PackagingsSection article={a} />
      <BarcodesSection article={a} />
      {(can(PRICE_UPDATE) || can('audit.log.view')) && <PriceHistory article={a} />}
      {editing && <ArticleDialog article={a} onClose={() => setEditing(false)} />}
    </>
  );
}
