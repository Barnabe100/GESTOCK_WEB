import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { SelectButton } from 'primereact/selectbutton';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { translateError } from '@/shared/lib/errors';
import { formatDateTime } from '@/shared/lib/format';
import {
  INITIAL_TABLE,
  toQueryString,
  useDebouncedValue,
  type TableState,
} from '@/shared/lib/serverTable';
import { EmptyState, ListEmpty } from '@/shared/ui/EmptyState';
import { FilterBar } from '@/shared/ui/FilterBar';
import { FormField } from '@/shared/ui/FormField';
import { PageHeader } from '@/shared/ui/PageHeader';
import { RowActions } from '@/shared/ui/RowActions';
import { SearchInput } from '@/shared/ui/SearchInput';
import { ServerTable } from '@/shared/ui/ServerTable';
import { StatusBadge } from '@/shared/ui/StatusBadge';
import { confirmAction } from '@/shared/ui/confirm';
import { useToast } from '@/shared/ui/toast';

import {
  ASSORTMENT_MANAGE,
  useArticles,
  useAssortmentMutations,
  useCategories,
  useSiteArticles,
  type Article,
  type AssortmentChange,
  type AssortmentStatusFilter,
  type RemovalBlocked,
  type SiteArticle,
} from './api';
import { OPTIONS_QUERY } from './ArticleDialog';
import { AssortmentStateBadge, RemovalBlockers, removalBlocked } from './assortment';

function useChangeSummary() {
  const { t } = useTranslation();
  return (result: AssortmentChange) =>
    t('assortment.changeSummary', {
      added: result.added,
      reactivated: result.reactivated,
      unchanged: result.unchanged,
    });
}

/** Articles du catalogue pas encore proposés par le site (jamais associés ou retirés). */
function AddArticlesDialog({
  siteId,
  siteName,
  onClose,
}: {
  siteId: string;
  siteName: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const summary = useChangeSummary();
  const [table, setTable] = useState<TableState>({
    ...INITIAL_TABLE,
    rows: 10,
    sortField: 'reference',
    sortOrder: 1,
  });
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<Article[]>([]);
  const debounced = useDebouncedValue(search);
  const candidates = useArticles(
    toQueryString(table, {
      search: debounced,
      status: 'active',
      site_id: siteId,
      in_site_assortment: 'false',
    }),
  );
  const { add } = useAssortmentMutations();

  const submit = () =>
    add.mutate(
      { siteId, articleIds: selected.map((a) => a.id) },
      {
        onSuccess: (result) => {
          toast.success(summary(result));
          onClose();
        },
        onError: (error) => toast.error(translateError(t, error)),
      },
    );

  return (
    <Dialog
      header={t('assortment.addTitle', { site: siteName })}
      visible
      onHide={onClose}
      className="sm-dialog sm-dialog-wide"
    >
      <p className="sm-help">{t('assortment.addHelp')}</p>
      <FilterBar>
        <SearchInput
          value={search}
          onChange={(v) => {
            setSearch(v);
            setTable((s) => ({ ...s, first: 0 }));
          }}
        />
      </FilterBar>
      <ServerTable
        query={candidates}
        table={table}
        onTableChange={setTable}
        minWidth="30rem"
        selection={selected}
        onSelectionChange={setSelected}
        empty={<EmptyState icon="pi pi-check" title={t('assortment.addEmpty')} />}
      >
        <Column selectionMode="multiple" headerStyle={{ width: '3rem' }} />
        <Column field="reference" header={t('articles.reference')} sortable />
        <Column field="designation" header={t('articles.designation')} sortable />
        <Column field="category_name" header={t('articles.category')} />
        <Column
          header={t('assortment.stateLabel')}
          body={(a: Article) => <AssortmentStateBadge state={a.site_assortment ?? 'none'} />}
        />
      </ServerTable>
      <div className="sm-dialog-actions">
        <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
        <Button
          type="button"
          icon="pi pi-plus"
          label={t('assortment.addSelected', { count: selected.length })}
          disabled={selected.length === 0}
          loading={add.isPending}
          onClick={submit}
        />
      </div>
    </Dialog>
  );
}

/** Copie (D6) : ajout seulement, depuis un autre site ; ni stock, ni seuils, ni lots. */
function CopyAssortmentDialog({
  siteId,
  siteName,
  onClose,
}: {
  siteId: string;
  siteName: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const summary = useChangeSummary();
  const { capabilities } = useCapabilities();
  const categories = useCategories(OPTIONS_QUERY);
  const [source, setSource] = useState<string | null>(null);
  const [categoryId, setCategoryId] = useState<string | null>(null);
  const { copy } = useAssortmentMutations();
  const sources = capabilities.sites.filter((s) => s.id !== siteId);

  const submit = () => {
    if (!source) return;
    copy.mutate(
      { siteId, sourceSiteId: source, categoryId },
      {
        onSuccess: (result) => {
          toast.success(summary(result));
          onClose();
        },
        onError: (error) => toast.error(translateError(t, error)),
      },
    );
  };

  return (
    <Dialog
      header={t('assortment.copyTitle', { site: siteName })}
      visible
      onHide={onClose}
      className="sm-dialog"
    >
      <div className="sm-form">
        <p className="sm-help">{t('assortment.copyHelp')}</p>
        <FormField id="copy-source" label={t('assortment.copySource')} required>
          <Dropdown
            inputId="copy-source"
            value={source}
            onChange={(e) => setSource((e.value as string | undefined) ?? null)}
            options={sources.map((s) => ({ value: s.id, label: s.name }))}
            placeholder={t('stock.chooseSite')}
          />
        </FormField>
        <FormField id="copy-category" label={t('assortment.copyCategory')}>
          <Dropdown
            inputId="copy-category"
            value={categoryId}
            onChange={(e) => setCategoryId((e.value as string | undefined) ?? null)}
            options={(categories.data?.items ?? []).map((c) => ({ value: c.id, label: c.name }))}
            placeholder={t('articles.allCategories')}
            showClear
            filter
          />
        </FormField>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button
            type="button"
            icon="pi pi-copy"
            label={t('assortment.copy')}
            disabled={!source}
            loading={copy.isPending}
            onClick={submit}
          />
        </div>
      </div>
    </Dialog>
  );
}

/**
 * Assortiment d'un site (Recette, étape 1, ADR-0046) : articles du catalogue que ce site
 * propose — CATALOGUE ≠ ASSORTIMENT ≠ STOCK. Ajout, retrait (désactivation, jamais une
 * suppression), réactivation, copie depuis un autre site ; gestion avec
 * `catalog.assortment.manage`, revérifiée par le serveur pour CE site.
 */
export default function AssortmentPage() {
  const { t } = useTranslation();
  const toast = useToast();
  const navigate = useNavigate();
  const { can, capabilities, siteId: selectedSite } = useCapabilities();
  const { locale, timezone } = capabilities.tenant;
  const canManage = can(ASSORTMENT_MANAGE);
  // Site sélectionné dans l'en-tête : la page le suit (le serveur refuse un autre site).
  const [chosenSite, setChosenSite] = useState<string | null>(
    selectedSite ?? capabilities.sites[0]?.id ?? null,
  );
  const siteId = selectedSite ?? chosenSite;
  const site = capabilities.sites.find((s) => s.id === siteId);
  const [table, setTable] = useState<TableState>({
    ...INITIAL_TABLE,
    sortField: 'reference',
    sortOrder: 1,
  });
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<AssortmentStatusFilter>('active');
  const [categoryId, setCategoryId] = useState<string | null>(null);
  const [selected, setSelected] = useState<SiteArticle[]>([]);
  const [dialog, setDialog] = useState<'add' | 'copy' | null>(null);
  const [blocked, setBlocked] = useState<RemovalBlocked[] | null>(null);
  const debounced = useDebouncedValue(search);
  const categories = useCategories(OPTIONS_QUERY);
  const articles = useSiteArticles(
    siteId,
    toQueryString(table, { search: debounced, status, category_id: categoryId }),
  );
  const { add, remove } = useAssortmentMutations();

  const resetPage = () => {
    setTable((s) => ({ ...s, first: 0 }));
    setSelected([]);
  };
  const filtered = search !== '' || status !== 'active' || categoryId !== null;
  const resetFilters = () => {
    setSearch('');
    setStatus('active');
    setCategoryId(null);
    resetPage();
  };

  // Retrait : confirmé ; tout ou rien — le serveur détaille les articles qui le bloquent.
  const removeArticles = (rows: SiteArticle[]) => {
    if (!siteId || rows.length === 0) return;
    confirmAction(t, {
      header: t('assortment.removeTitle'),
      message:
        rows.length === 1
          ? t('assortment.removeConfirmOne', { name: rows[0]?.designation, site: site?.name })
          : t('assortment.removeConfirmMany', { count: rows.length, site: site?.name }),
      acceptLabel: t('assortment.remove'),
      danger: true,
      onAccept: () =>
        remove.mutate(
          { siteId, articleIds: rows.map((r) => r.article_id) },
          {
            onSuccess: (result) => {
              setSelected([]);
              toast.success(t('assortment.removed', { count: result.removed }));
            },
            onError: (error) => {
              const details = removalBlocked(error);
              if (details) setBlocked(details);
              else toast.error(translateError(t, error));
            },
          },
        ),
    });
  };

  const reactivate = (row: SiteArticle) => {
    if (!siteId) return;
    add.mutate(
      { siteId, articleIds: [row.article_id] },
      {
        onSuccess: () => toast.success(t('assortment.reactivated', { name: row.designation })),
        onError: (error) => toast.error(translateError(t, error)),
      },
    );
  };

  const selectable = selected.filter((r) => r.state === 'active');

  return (
    <>
      <PageHeader
        title={t('assortment.title')}
        description={t('assortment.subtitle')}
        actions={
          canManage &&
          siteId && (
            <div className="sm-tags">
              {capabilities.sites.length > 1 && (
                <Button
                  icon="pi pi-copy"
                  label={t('assortment.copy')}
                  outlined
                  onClick={() => setDialog('copy')}
                />
              )}
              <Button
                icon="pi pi-plus"
                label={t('assortment.add')}
                onClick={() => setDialog('add')}
              />
            </div>
          )
        }
      />
      <FilterBar onReset={resetFilters} active={filtered}>
        {capabilities.sites.length > 1 && (
          <Dropdown
            value={siteId}
            onChange={(e) => {
              setChosenSite((e.value as string | undefined) ?? null);
              resetPage();
            }}
            options={capabilities.sites.map((s) => ({ value: s.id, label: s.name }))}
            disabled={selectedSite !== null}
            aria-label={t('layout.site')}
          />
        )}
        <SearchInput
          value={search}
          onChange={(v) => {
            setSearch(v);
            resetPage();
          }}
        />
        <Dropdown
          value={categoryId}
          onChange={(e) => {
            setCategoryId((e.value as string | undefined) ?? null);
            resetPage();
          }}
          options={(categories.data?.items ?? []).map((c) => ({ value: c.id, label: c.name }))}
          placeholder={t('articles.allCategories')}
          showClear
          aria-label={t('articles.category')}
          filter
        />
        <SelectButton
          value={status}
          onChange={(e) => {
            if (e.value) {
              setStatus(e.value as AssortmentStatusFilter);
              resetPage();
            }
          }}
          options={(['active', 'removed', 'all'] as const).map((value) => ({
            value,
            label: t(`assortment.filter.${value}`),
          }))}
          aria-label={t('assortment.stateLabel')}
        />
      </FilterBar>
      {canManage && selectable.length > 0 && (
        <div className="sm-bulk-bar" role="region" aria-label={t('assortment.bulk')}>
          <span>{t('assortment.selectedCount', { count: selectable.length })}</span>
          <Button
            icon="pi pi-minus-circle"
            label={t('assortment.removeSelected')}
            severity="danger"
            outlined
            size="small"
            loading={remove.isPending}
            onClick={() => removeArticles(selectable)}
          />
        </div>
      )}
      {siteId ? (
        <ServerTable
          query={articles}
          table={table}
          onTableChange={setTable}
          dataKey="article_id"
          minWidth="40rem"
          selection={canManage ? selected : undefined}
          onSelectionChange={canManage ? setSelected : undefined}
          empty={
            <ListEmpty
              filtered={filtered}
              icon="pi pi-th-large"
              title={t('assortment.empty')}
              action={
                canManage && (
                  <Button
                    icon="pi pi-plus"
                    label={t('assortment.add')}
                    outlined
                    onClick={() => setDialog('add')}
                  />
                )
              }
            />
          }
        >
          {canManage && <Column selectionMode="multiple" headerStyle={{ width: '3rem' }} />}
          <Column field="reference" header={t('articles.reference')} sortable />
          <Column field="designation" header={t('articles.designation')} sortable />
          <Column
            field="category_name"
            sortField="category"
            header={t('articles.category')}
            sortable
          />
          <Column
            header={t('assortment.stateLabel')}
            body={(r: SiteArticle) => (
              <div className="sm-tags">
                <AssortmentStateBadge state={r.state} />
                {!r.article_active && (
                  <StatusBadge tone="neutral" label={t('assortment.articleInactive')} />
                )}
                {!r.stock_managed && (
                  <StatusBadge tone="info" label={t('articles.notStockManaged')} />
                )}
              </div>
            )}
          />
          <Column
            field="added_at"
            sortField="added_at"
            sortable
            header={t('assortment.lastChange')}
            body={(r: SiteArticle) =>
              r.state === 'removed' && r.removed_at
                ? t('assortment.removedOn', {
                    date: formatDateTime(r.removed_at, locale, timezone),
                    user: r.removed_by_name ?? '—',
                  })
                : t('assortment.addedOn', {
                    date: formatDateTime(r.added_at, locale, timezone),
                    user: r.added_by_name ?? t('assortment.initial'),
                  })
            }
          />
          <Column
            header={t('common.actions')}
            body={(r: SiteArticle) => (
              <RowActions
                actions={[
                  {
                    key: 'open',
                    label: t('articles.open'),
                    icon: 'pi pi-eye',
                    onClick: () => void navigate(`/catalog/articles/${r.article_id}`),
                  },
                  {
                    key: 'remove',
                    label: t('assortment.remove'),
                    icon: 'pi pi-minus-circle',
                    danger: true,
                    onClick: () => removeArticles([r]),
                    hidden: !canManage || r.state !== 'active',
                  },
                  {
                    key: 'reactivate',
                    label: t('assortment.reactivate'),
                    icon: 'pi pi-replay',
                    onClick: () => reactivate(r),
                    hidden: !canManage || r.state !== 'removed' || !r.article_active,
                  },
                ]}
              />
            )}
          />
        </ServerTable>
      ) : (
        <EmptyState icon="pi pi-building" title={t('assortment.noSite')} />
      )}
      {dialog === 'add' && siteId && (
        <AddArticlesDialog
          siteId={siteId}
          siteName={site?.name ?? ''}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === 'copy' && siteId && (
        <CopyAssortmentDialog
          siteId={siteId}
          siteName={site?.name ?? ''}
          onClose={() => setDialog(null)}
        />
      )}
      {blocked && (
        <Dialog
          header={t('assortment.removeTitle')}
          visible
          onHide={() => setBlocked(null)}
          className="sm-dialog"
        >
          <RemovalBlockers blocked={blocked} />
          <div className="sm-dialog-actions">
            <Button type="button" label={t('actions.close')} onClick={() => setBlocked(null)} />
          </div>
        </Dialog>
      )}
    </>
  );
}
