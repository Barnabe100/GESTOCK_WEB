import { Column } from 'primereact/column';
import { DataTable } from 'primereact/datatable';
import { Dropdown } from 'primereact/dropdown';
import { SelectButton } from 'primereact/selectbutton';
import { TabPanel, TabView } from 'primereact/tabview';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { tenantToday } from '@/modules/dashboard/widgets';
import { SALE_CHANNELS } from '@/modules/sales/api';
import { formatMoney, formatQuantity } from '@/shared/lib/decimal';
import { formatDate } from '@/shared/lib/format';
import { INITIAL_TABLE, toQueryString, type TableState } from '@/shared/lib/serverTable';
import { ListEmpty } from '@/shared/ui/EmptyState';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { ExportMenu } from '@/shared/ui/ExportMenu';
import { DateRangeFilter, FilterBar } from '@/shared/ui/FilterBar';
import { LoadingState } from '@/shared/ui/LoadingState';
import { MetricCard } from '@/shared/ui/MetricCard';
import { PageHeader } from '@/shared/ui/PageHeader';
import { ServerTable } from '@/shared/ui/ServerTable';

import {
  filterParams,
  GRANULARITIES,
  REPORT_EXPORT,
  REPORT_EXPORT_FORMATS,
  useBreakdown,
  usePaymentMethods,
  useRecipeProfitability,
  useSalesSummary,
  type BreakdownRow,
  type Dimension,
  type Granularity,
  type ProfitabilityRow,
  type ReportFilters,
  type SeriesPoint,
} from './api';

const RECIPES_MODULE = 'restaurant.recipes';
const RECIPE_VIEW = 'restaurant.recipes.recipe.view';

/** Premier jour du mois d'une date AAAA-MM-JJ. */
function monthStart(day: string): string {
  return `${day.slice(0, 8)}01`;
}

/**
 * Rapports des ventes (palier R10, ADR-0055) : tout est calculé par le serveur (CA remises
 * déduites, coût réel des sorties, marge) ; coûts et marges affichés seulement s'ils sont
 * présents dans la réponse (`catalog.article.cost_view`). Périmètre : sites où le module est
 * actif et la permission détenue.
 */
export default function ReportsPage() {
  const { t } = useTranslation();
  const { can, capabilities, hasModule } = useCapabilities();
  const { currency, locale, timezone } = capabilities.tenant;
  const today = tenantToday(timezone);
  const [dateFrom, setDateFrom] = useState(monthStart(today));
  const [dateTo, setDateTo] = useState(today);
  const [siteId, setSiteId] = useState<string | null>(null);
  const [channel, setChannel] = useState<string | null>(null);
  const [granularity, setGranularity] = useState<Granularity>('day');
  const multiSite = capabilities.site === null && capabilities.sites.length > 1;
  const filters: ReportFilters = {
    date_from: dateFrom,
    date_to: dateTo,
    site_id: siteId,
    channel,
  };
  const summary = useSalesSummary(filters, granularity);
  const money = (v: string | null | undefined) => (v ? formatMoney(v, currency, locale) : '—');
  const totals = summary.data?.totals;
  const costsVisible = totals?.margin !== undefined;
  const canExport = can(REPORT_EXPORT);
  const recipes = hasModule(RECIPES_MODULE) && can(RECIPE_VIEW);

  const exportPath =
    (report: string, extra: Record<string, string> = {}) =>
    (format: string) => {
      const params = filterParams(filters);
      params.set('report', report);
      params.set('format', format);
      for (const [key, value] of Object.entries(extra)) params.set(key, value);
      return `/reports/export?${params.toString()}`;
    };

  return (
    <>
      <PageHeader
        title={t('reports.title')}
        description={t('reports.subtitle')}
        actions={
          canExport && (
            <ExportMenu
              formats={REPORT_EXPORT_FORMATS}
              path={exportPath('summary', { granularity })}
              fallbackName="rapport-ventes"
            />
          )
        }
      />
      <FilterBar
        active={siteId !== null || channel !== null || dateFrom !== monthStart(today)}
        onReset={() => {
          setDateFrom(monthStart(today));
          setDateTo(today);
          setSiteId(null);
          setChannel(null);
        }}
      >
        <DateRangeFilter
          from={dateFrom}
          to={dateTo}
          onChange={({ from, to }) => {
            setDateFrom(from);
            setDateTo(to);
          }}
        />
        {multiSite && (
          <Dropdown
            value={siteId}
            onChange={(e) => setSiteId((e.value as string | undefined) ?? null)}
            options={capabilities.sites.map((s) => ({ value: s.id, label: s.name }))}
            placeholder={t('sales.allSites')}
            showClear
            aria-label={t('layout.site')}
          />
        )}
        <Dropdown
          value={channel}
          onChange={(e) => setChannel((e.value as string | undefined) ?? null)}
          options={SALE_CHANNELS.map((v) => ({ value: v, label: t(`sales.channels.${v}`) }))}
          placeholder={t('sales.allChannels')}
          showClear
          aria-label={t('sales.channel')}
        />
      </FilterBar>

      {summary.isError ? (
        <ErrorMessage error={summary.error} />
      ) : !totals ? (
        <LoadingState />
      ) : (
        <>
          <div className="sm-metrics" aria-label={t('reports.indicators')}>
            <MetricCard
              icon="pi pi-chart-line"
              tone="info"
              label={t('reports.revenue')}
              value={money(totals.revenue)}
              hint={t('reports.salesCount', { count: totals.sales_count })}
            />
            <MetricCard
              icon="pi pi-shopping-bag"
              label={t('reports.averageBasket')}
              value={money(totals.average_basket)}
            />
            <MetricCard
              icon="pi pi-percentage"
              tone={/[1-9]/.test(totals.discounts) ? 'warning' : 'neutral'}
              label={t('reports.discounts')}
              value={money(totals.discounts)}
            />
            <MetricCard
              icon="pi pi-wallet"
              label={t('reports.paid')}
              value={money(totals.paid)}
              hint={t('reports.remaining', { amount: money(totals.remaining) })}
            />
            {costsVisible && (
              <MetricCard
                icon="pi pi-chart-bar"
                tone="success"
                label={t('reports.margin')}
                value={money(totals.margin)}
                hint={
                  totals.margin_rate
                    ? t('reports.marginRate', { rate: totals.margin_rate })
                    : undefined
                }
              />
            )}
          </div>
          {costsVisible && <p className="sm-help">{t('reports.costHelp')}</p>}
          <section className="sm-block" aria-labelledby="reports-series">
            <div className="sm-section-header">
              <h2 id="reports-series">{t('reports.series')}</h2>
              <SelectButton
                value={granularity}
                allowEmpty={false}
                onChange={(e) => setGranularity(e.value as Granularity)}
                options={GRANULARITIES.map((g) => ({
                  value: g,
                  label: t(`reports.granularities.${g}`),
                }))}
                aria-label={t('reports.granularity')}
              />
            </div>
            <SeriesTable
              series={summary.data?.series ?? []}
              costsVisible={costsVisible}
              money={money}
              locale={locale}
            />
          </section>
        </>
      )}

      <TabView className="sm-block">
        {(
          [
            ['article', t('reports.dimensions.article')],
            ['category', t('reports.dimensions.category')],
            ...(multiSite ? [['site', t('reports.dimensions.site')]] : []),
            ['seller', t('reports.dimensions.seller')],
            ['channel', t('reports.dimensions.channel')],
            ['customer', t('reports.dimensions.customer')],
          ] as [Dimension, string][]
        ).map(([dimension, header]) => (
          <TabPanel key={dimension} header={header}>
            <BreakdownTab
              dimension={dimension}
              filters={filters}
              costsVisible={costsVisible}
              money={money}
              exportMenu={
                canExport && (
                  <ExportMenu
                    formats={REPORT_EXPORT_FORMATS}
                    path={exportPath('breakdown', { dimension })}
                    fallbackName={`rapport-ventes-${dimension}`}
                  />
                )
              }
            />
          </TabPanel>
        ))}
        <TabPanel header={t('reports.dimensions.payment')}>
          <PaymentsTab
            filters={filters}
            money={money}
            exportMenu={
              canExport && (
                <ExportMenu
                  formats={REPORT_EXPORT_FORMATS}
                  path={exportPath('payments')}
                  fallbackName="rapport-paiements"
                />
              )
            }
          />
        </TabPanel>
        {recipes && (
          <TabPanel header={t('reports.dimensions.recipes')}>
            <RecipesTab filters={filters} money={money} />
          </TabPanel>
        )}
      </TabView>
    </>
  );
}

type Money = (v: string | null | undefined) => string;

function SeriesTable({
  series,
  costsVisible,
  money,
  locale,
}: {
  series: SeriesPoint[];
  costsVisible: boolean;
  money: Money;
  locale: string;
}) {
  const { t } = useTranslation();
  const max = Math.max(0, ...series.map((p) => Number(p.revenue)));
  return (
    <DataTable
      value={series}
      dataKey="period"
      size="small"
      scrollable
      scrollHeight="360px"
      aria-label={t('reports.series')}
      emptyMessage={t('reports.empty')}
    >
      <Column
        header={t('reports.period')}
        body={(p: SeriesPoint) => formatDate(p.period, locale, 'UTC')}
      />
      <Column
        header={t('reports.sales')}
        headerClassName="sm-num"
        bodyClassName="sm-num"
        field="sales_count"
      />
      <Column
        header={t('reports.revenue')}
        headerClassName="sm-num"
        bodyClassName="sm-num"
        body={(p: SeriesPoint) => (
          <span className="sm-report-bar-cell">
            <span
              className="sm-report-bar"
              aria-hidden="true"
              // Largeur proportionnelle au CA du point (visuel seulement, chiffre affiché).
              style={{ width: `${max > 0 ? (Number(p.revenue) / max) * 100 : 0}%` }}
            />
            <span>{money(p.revenue)}</span>
          </span>
        )}
      />
      <Column
        header={t('reports.discounts')}
        headerClassName="sm-num"
        bodyClassName="sm-num"
        body={(p: SeriesPoint) => money(p.discounts)}
      />
      {costsVisible && (
        <Column
          header={t('reports.margin')}
          headerClassName="sm-num"
          bodyClassName="sm-num"
          body={(p: SeriesPoint) => money(p.margin)}
        />
      )}
    </DataTable>
  );
}

function BreakdownTab({
  dimension,
  filters,
  costsVisible,
  money,
  exportMenu,
}: {
  dimension: Dimension;
  filters: ReportFilters;
  costsVisible: boolean;
  money: Money;
  exportMenu: React.ReactNode;
}) {
  const { t } = useTranslation();
  const [table, setTable] = useState<TableState>(INITIAL_TABLE);
  const extra = Object.fromEntries(filterParams(filters).entries());
  const query = useBreakdown(dimension, toQueryString(table, extra));
  const isLine = dimension === 'article' || dimension === 'category';
  const label = (row: BreakdownRow) =>
    dimension === 'channel'
      ? t(`sales.channels.${row.key ?? ''}`)
      : (row.label ?? (dimension === 'customer' ? t('sales.anonymousShort') : '—'));
  return (
    <>
      <div className="sm-section-header">
        <p className="sm-help">
          {isLine ? t('reports.lineAmountsHelp') : t('reports.saleAmountsHelp')}
        </p>
        {exportMenu}
      </div>
      <ServerTable
        query={query}
        table={table}
        onTableChange={setTable}
        dataKey={(row: BreakdownRow) => row.key ?? 'none'}
        empty={<ListEmpty filtered={false} title={t('reports.empty')} />}
      >
        <Column
          field="label"
          header={t(`reports.dimensions.${dimension}`)}
          sortable
          body={(row: BreakdownRow) =>
            row.reference ? `${row.reference} — ${label(row)}` : label(row)
          }
        />
        <Column
          field="sales_count"
          header={t('reports.sales')}
          sortable
          headerClassName="sm-num"
          bodyClassName="sm-num"
        />
        {dimension === 'article' && (
          <Column
            field="quantity"
            header={t('reports.quantity')}
            sortable
            headerClassName="sm-num"
            bodyClassName="sm-num"
            body={(row: BreakdownRow) =>
              row.quantity ? `${formatQuantity(row.quantity)} ${row.unit ?? ''}` : '—'
            }
          />
        )}
        <Column
          field="revenue"
          header={isLine ? t('reports.lineAmounts') : t('reports.revenue')}
          sortable
          headerClassName="sm-num"
          bodyClassName="sm-num"
          body={(row: BreakdownRow) => money(row.revenue)}
        />
        {!isLine && (
          <Column
            header={t('reports.discounts')}
            headerClassName="sm-num"
            bodyClassName="sm-num"
            body={(row: BreakdownRow) => money(row.discounts)}
          />
        )}
        {costsVisible && (
          <Column
            field="cost"
            header={t('reports.cost')}
            sortable
            headerClassName="sm-num"
            bodyClassName="sm-num"
            body={(row: BreakdownRow) => money(row.cost)}
          />
        )}
        {costsVisible && (
          <Column
            field="margin"
            header={t('reports.margin')}
            sortable
            headerClassName="sm-num"
            bodyClassName="sm-num"
            body={(row: BreakdownRow) =>
              row.margin_rate ? `${money(row.margin)} (${row.margin_rate} %)` : money(row.margin)
            }
          />
        )}
        <Column
          header={t('reports.share')}
          headerClassName="sm-num"
          bodyClassName="sm-num"
          body={(row: BreakdownRow) => (row.share ? `${row.share} %` : '—')}
        />
      </ServerTable>
    </>
  );
}

function PaymentsTab({
  filters,
  money,
  exportMenu,
}: {
  filters: ReportFilters;
  money: Money;
  exportMenu: React.ReactNode;
}) {
  const { t } = useTranslation();
  const query = usePaymentMethods(filters);
  if (query.isError) return <ErrorMessage error={query.error} />;
  return (
    <>
      <div className="sm-section-header">
        <p className="sm-help">{t('reports.paymentsHelp')}</p>
        {exportMenu}
      </div>
      <DataTable
        value={query.data ?? []}
        loading={query.isLoading}
        dataKey="method_label"
        size="small"
        aria-label={t('reports.dimensions.payment')}
        emptyMessage={t('reports.empty')}
      >
        <Column field="method_label" header={t('reports.method')} />
        <Column
          field="payment_count"
          header={t('reports.payments')}
          headerClassName="sm-num"
          bodyClassName="sm-num"
        />
        <Column
          header={t('reports.amount')}
          headerClassName="sm-num"
          bodyClassName="sm-num"
          body={(row: { amount: string }) => money(row.amount)}
        />
        <Column
          header={t('reports.share')}
          headerClassName="sm-num"
          bodyClassName="sm-num"
          body={(row: { share: string | null }) => (row.share ? `${row.share} %` : '—')}
        />
      </DataTable>
    </>
  );
}

function RecipesTab({ filters, money }: { filters: ReportFilters; money: Money }) {
  const { t } = useTranslation();
  const [table, setTable] = useState<TableState>(INITIAL_TABLE);
  const extra = Object.fromEntries(filterParams({ ...filters, channel: null }).entries());
  const query = useRecipeProfitability(toQueryString(table, extra), true);
  const costsVisible = query.data?.items[0]?.ingredient_cost !== undefined;
  return (
    <>
      <p className="sm-help">{t('reports.recipesHelp')}</p>
      <ServerTable
        query={query}
        table={table}
        onTableChange={setTable}
        dataKey="article_id"
        empty={<ListEmpty filtered={false} title={t('reports.empty')} />}
      >
        <Column
          field="designation"
          header={t('reports.dish')}
          sortable
          body={(row: ProfitabilityRow) => `${row.reference} — ${row.designation}`}
        />
        <Column
          field="portions"
          header={t('reports.portions')}
          sortable
          headerClassName="sm-num"
          bodyClassName="sm-num"
          body={(row: ProfitabilityRow) => formatQuantity(row.portions)}
        />
        <Column
          field="revenue"
          header={t('reports.revenue')}
          sortable
          headerClassName="sm-num"
          bodyClassName="sm-num"
          body={(row: ProfitabilityRow) => money(row.revenue)}
        />
        {costsVisible && (
          <Column
            field="ingredient_cost"
            header={t('reports.ingredientCost')}
            sortable
            headerClassName="sm-num"
            bodyClassName="sm-num"
            body={(row: ProfitabilityRow) => money(row.ingredient_cost)}
          />
        )}
        {costsVisible && (
          <Column
            header={t('reports.portionCost')}
            headerClassName="sm-num"
            bodyClassName="sm-num"
            body={(row: ProfitabilityRow) => money(row.portion_cost)}
          />
        )}
        {costsVisible && (
          <Column
            field="margin"
            header={t('reports.margin')}
            sortable
            headerClassName="sm-num"
            bodyClassName="sm-num"
            body={(row: ProfitabilityRow) =>
              row.margin_rate ? `${money(row.margin)} (${row.margin_rate} %)` : money(row.margin)
            }
          />
        )}
      </ServerTable>
    </>
  );
}
