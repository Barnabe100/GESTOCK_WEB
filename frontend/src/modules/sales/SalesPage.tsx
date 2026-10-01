import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { ToggleButton } from 'primereact/togglebutton';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatMoney } from '@/shared/lib/decimal';
import { formatDate } from '@/shared/lib/format';
import {
  INITIAL_TABLE,
  toQueryString,
  useDebouncedValue,
  type TableState,
} from '@/shared/lib/serverTable';
import { PageHeader } from '@/shared/ui/PageHeader';
import { SearchInput } from '@/shared/ui/SearchInput';
import { DocumentStatusBadge } from '@/shared/ui/StatusBadge';
import { ServerTable } from '@/shared/ui/ServerTable';
import { DateRangeFilter, FilterBar } from '@/shared/ui/FilterBar';
import { ListEmpty } from '@/shared/ui/EmptyState';
import { RowActions } from '@/shared/ui/RowActions';
import { ExportMenu } from '@/shared/ui/ExportMenu';
import { ArticlePicker, type ArticleOption } from '@/modules/stock/ArticlePicker';

import {
  SALE_CHANNELS,
  SALE_PAYMENT_STATUSES,
  SALE_STATUSES,
  SALES_EXPORT_FORMATS,
  useSales,
  useSellers,
  type Sale,
  type SaleChannel,
  type SalePaymentStatus,
  type SaleStatus,
} from './api';
import { CustomerPicker, type CustomerOption } from './CustomerPicker';
import { SalePaymentBadge } from './ui';

/** Tri par défaut : la plus récente d'abord (date de création), jamais l'ordre du numéro. */
export const DEFAULT_SALES_SORT = { sortField: 'created_at', sortOrder: -1 as const };

export default function SalesPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { can, capabilities } = useCapabilities();
  const [table, setTable] = useState<TableState>({ ...INITIAL_TABLE, ...DEFAULT_SALES_SORT });
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<SaleStatus | null>(null);
  const [siteId, setSiteId] = useState<string | null>(null);
  const [paymentStatus, setPaymentStatus] = useState<SalePaymentStatus | null>(null);
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [channel, setChannel] = useState<SaleChannel | null>(null);
  const [sellerId, setSellerId] = useState<string | null>(null);
  const [mine, setMine] = useState(false);
  const [customer, setCustomer] = useState<CustomerOption | null>(null);
  const [article, setArticle] = useState<ArticleOption | null>(null);
  const [articleReference, setArticleReference] = useState('');
  const [paymentReference, setPaymentReference] = useState('');
  const [more, setMore] = useState(false);
  const debounced = useDebouncedValue(search);
  const debouncedArticleReference = useDebouncedValue(articleReference);
  const debouncedPaymentReference = useDebouncedValue(paymentReference);
  const sellers = useSellers();
  // Filtres de la liste : l'export reçoit EXACTEMENT les mêmes (même périmètre côté serveur).
  const filters = {
    search: debounced,
    status,
    site_id: siteId,
    payment_status: paymentStatus,
    date_from: dateFrom,
    date_to: dateTo,
    channel,
    seller_id: sellerId,
    mine: mine ? 'true' : null,
    customer_id: customer?.id ?? null,
    article_id: article?.id ?? null,
    article_reference: debouncedArticleReference.trim(),
    payment_reference: debouncedPaymentReference.trim(),
  };
  const sales = useSales(toQueryString(table, filters));
  const exportPath = (format: string) => {
    const params = new URLSearchParams({ format });
    if (table.sortField && table.sortOrder) {
      params.set('sort', `${table.sortOrder === -1 ? '-' : ''}${table.sortField}`);
    }
    for (const [key, value] of Object.entries(filters)) {
      if (value) params.set(key, value);
    }
    return `/sales/export?${params.toString()}`;
  };
  const { currency, locale } = capabilities.tenant;
  const multiSite = capabilities.site === null && capabilities.sites.length > 1;

  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));
  const advanced =
    channel !== null ||
    customer !== null ||
    article !== null ||
    articleReference !== '' ||
    paymentReference !== '';
  const filtered =
    search !== '' ||
    status !== null ||
    siteId !== null ||
    paymentStatus !== null ||
    dateFrom !== '' ||
    dateTo !== '' ||
    sellerId !== null ||
    mine ||
    advanced;
  const resetFilters = () => {
    setSearch('');
    setStatus(null);
    setSiteId(null);
    setPaymentStatus(null);
    setDateFrom('');
    setDateTo('');
    setChannel(null);
    setSellerId(null);
    setMine(false);
    setCustomer(null);
    setArticle(null);
    setArticleReference('');
    setPaymentReference('');
    resetPage();
  };

  return (
    <>
      <PageHeader
        title={t('sales.title')}
        description={t('sales.subtitle')}
        actions={
          <>
            {can('sales.sale.export') && (
              <ExportMenu formats={SALES_EXPORT_FORMATS} path={exportPath} fallbackName="ventes" />
            )}
            {can('sales.sale.create') && (
              <Button
                icon="pi pi-plus"
                label={t('sales.new')}
                onClick={() => void navigate('/sales/new')}
              />
            )}
          </>
        }
      />
      <FilterBar onReset={resetFilters} active={filtered}>
        <SearchInput
          value={search}
          placeholder={t('sales.search')}
          onChange={(v) => {
            setSearch(v);
            resetPage();
          }}
        />
        <Dropdown
          value={status}
          onChange={(e) => {
            setStatus((e.value as SaleStatus | undefined) ?? null);
            resetPage();
          }}
          options={SALE_STATUSES.map((v) => ({ value: v, label: t(`sales.statuses.${v}`) }))}
          placeholder={t('sales.allStatuses')}
          showClear
          aria-label={t('sales.status')}
        />
        <Dropdown
          value={paymentStatus}
          onChange={(e) => {
            setPaymentStatus((e.value as SalePaymentStatus | undefined) ?? null);
            resetPage();
          }}
          options={SALE_PAYMENT_STATUSES.map((v) => ({
            value: v,
            label: t(`sales.paymentStatus.${v}`),
          }))}
          placeholder={t('sales.allPaymentStatuses')}
          showClear
          aria-label={t('sales.payment')}
        />
        {multiSite && (
          <Dropdown
            value={siteId}
            onChange={(e) => {
              setSiteId((e.value as string | undefined) ?? null);
              resetPage();
            }}
            options={capabilities.sites.map((s) => ({ value: s.id, label: s.name }))}
            placeholder={t('sales.allSites')}
            showClear
            aria-label={t('layout.site')}
          />
        )}
        <DateRangeFilter
          from={dateFrom}
          to={dateTo}
          onChange={({ from, to }) => {
            setDateFrom(from);
            setDateTo(to);
            resetPage();
          }}
        />
        <Dropdown
          value={sellerId}
          onChange={(e) => {
            setSellerId((e.value as string | undefined) ?? null);
            resetPage();
          }}
          options={(sellers.data ?? []).map((u) => ({ value: u.id, label: u.name }))}
          placeholder={t('sales.allSellers')}
          showClear
          filter
          aria-label={t('sales.seller')}
        />
        <ToggleButton
          checked={mine}
          onChange={(e) => {
            setMine(e.value);
            resetPage();
          }}
          onLabel={t('sales.mine')}
          offLabel={t('sales.mine')}
          onIcon="pi pi-check"
          offIcon="pi pi-user"
          aria-label={t('sales.mine')}
        />
        <Button
          type="button"
          text
          icon={more || advanced ? 'pi pi-chevron-up' : 'pi pi-sliders-h'}
          label={t(more || advanced ? 'sales.lessFilters' : 'sales.moreFilters')}
          aria-expanded={more || advanced}
          onClick={() => setMore((v) => !v)}
          disabled={advanced}
        />
      </FilterBar>
      {(more || advanced) && (
        <FilterBar>
          <Dropdown
            value={channel}
            onChange={(e) => {
              setChannel((e.value as SaleChannel | undefined) ?? null);
              resetPage();
            }}
            options={SALE_CHANNELS.map((v) => ({ value: v, label: t(`sales.channels.${v}`) }))}
            placeholder={t('sales.allChannels')}
            showClear
            aria-label={t('sales.channel')}
          />
          {can('customers.customer.view') && (
            <CustomerPicker
              id="sales-filter-customer"
              value={customer}
              includeInactive
              placeholder={t('sales.allCustomers')}
              ariaLabel={t('sales.customer')}
              onChange={(value) => {
                setCustomer(value);
                resetPage();
              }}
            />
          )}
          {can('catalog.article.view') && (
            <ArticlePicker
              id="sales-filter-article"
              value={article}
              ariaLabel={t('sales.article')}
              onChange={(value) => {
                setArticle(value);
                resetPage();
              }}
            />
          )}
          <InputText
            value={articleReference}
            placeholder={t('sales.articleReference')}
            title={t('sales.articleReferenceHelp')}
            aria-label={t('sales.articleReference')}
            onChange={(e) => {
              setArticleReference(e.target.value);
              resetPage();
            }}
          />
          <InputText
            value={paymentReference}
            placeholder={t('sales.paymentReference')}
            title={t('sales.paymentReferenceHelp')}
            aria-label={t('sales.paymentReference')}
            onChange={(e) => {
              setPaymentReference(e.target.value);
              resetPage();
            }}
          />
        </FilterBar>
      )}
      <ServerTable
        query={sales}
        table={table}
        onTableChange={setTable}
        onRowClick={(s: Sale) => void navigate(`/sales/${s.id}`)}
        empty={
          <ListEmpty
            filtered={filtered}
            title={t('sales.empty')}
            action={
              can('sales.sale.create') && (
                <Button
                  icon="pi pi-plus"
                  label={t('sales.new')}
                  outlined
                  onClick={() => void navigate('/sales/new')}
                />
              )
            }
          />
        }
      >
        <Column
          field="number"
          header={t('sales.number')}
          sortable
          bodyClassName="sm-nowrap"
          body={(s: Sale) => s.number ?? t('sales.draftNumber')}
        />
        <Column
          field="sale_date"
          header={t('sales.date')}
          sortable
          body={(s: Sale) => formatDate(s.sale_date, locale, 'UTC')}
        />
        {multiSite && <Column field="site_name" header={t('layout.site')} />}
        <Column
          header={t('sales.customer')}
          body={(s: Sale) => s.customer_name ?? t('sales.anonymousShort')}
        />
        <Column
          field="total"
          header={t('sales.total')}
          sortable
          headerClassName="sm-num"
          bodyClassName="sm-num"
          body={(s: Sale) => formatMoney(s.total, currency, locale)}
        />
        <Column
          header={t('sales.status')}
          body={(s: Sale) => <DocumentStatusBadge labels="sales.statuses" status={s.status} />}
        />
        <Column
          header={t('sales.payment')}
          body={(s: Sale) =>
            s.payment_status ? <SalePaymentBadge status={s.payment_status} /> : '—'
          }
        />
        <Column header={t('sales.channel')} body={(s: Sale) => t(`sales.channels.${s.channel}`)} />
        <Column field="created_by_name" header={t('sales.seller')} />
        <Column
          header={t('common.actions')}
          body={(s: Sale) => {
            const editable = s.status === 'DRAFT' && can('sales.sale.update');
            return (
              <RowActions
                actions={[
                  {
                    key: 'open',
                    label: t(editable ? 'actions.edit' : 'sales.open'),
                    icon: editable ? 'pi pi-pencil' : 'pi pi-eye',
                    onClick: () => void navigate(`/sales/${s.id}`),
                  },
                ]}
              />
            );
          }}
        />
      </ServerTable>
    </>
  );
}
