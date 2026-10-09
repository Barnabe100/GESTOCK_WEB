import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { InputNumber } from 'primereact/inputnumber';
import { InputText } from 'primereact/inputtext';
import { InputTextarea } from 'primereact/inputtextarea';
import { TabPanel, TabView } from 'primereact/tabview';
import { useState } from 'react';
import { Controller, useForm, useWatch } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { PackagingSelect, type SalePackaging } from '@/modules/sales/PackagingSelect';
import { ArticlePicker, type ArticleOption } from '@/modules/stock/ArticlePicker';
import { formatMoney } from '@/shared/lib/decimal';
import { translateError } from '@/shared/lib/errors';
import {
  INITIAL_TABLE,
  toQueryString,
  useDebouncedValue,
  type StatusFilterValue,
  type TableState,
} from '@/shared/lib/serverTable';
import { ListEmpty } from '@/shared/ui/EmptyState';
import { FilterBar } from '@/shared/ui/FilterBar';
import { FormField } from '@/shared/ui/FormField';
import { PageHeader } from '@/shared/ui/PageHeader';
import { RowActions } from '@/shared/ui/RowActions';
import { SearchInput } from '@/shared/ui/SearchInput';
import { ServerTable } from '@/shared/ui/ServerTable';
import { StatusFilter } from '@/shared/ui/StatusFilter';
import { ActiveBadge, StatusBadge } from '@/shared/ui/StatusBadge';
import { confirmAction } from '@/shared/ui/confirm';
import { useToast } from '@/shared/ui/toast';

import {
  MENU_AVAILABILITY,
  MENU_MANAGE,
  itemLabel,
  useMenuItems,
  useMenuMutations,
  useMenuSections,
  type MenuItem,
  type MenuSection,
} from './api';

type AvailabilityValue = 'all' | 'available' | 'unavailable';

/** Site d'une nouvelle saisie : le site sélectionné, sinon le seul site, sinon à choisir. */
function useDefaultSite(): { siteId: string | null; chooseSite: boolean } {
  const { capabilities, siteId } = useCapabilities();
  const only = capabilities.sites.length === 1 ? (capabilities.sites[0]?.id ?? null) : null;
  return { siteId: siteId ?? only, chooseSite: siteId === null && capabilities.sites.length > 1 };
}

function SiteDropdown({
  id,
  value,
  onChange,
}: {
  id: string;
  value: string | null;
  onChange: (value: string | null) => void;
}) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  return (
    <Dropdown
      inputId={id}
      value={value}
      onChange={(e) => onChange(e.value as string | null)}
      options={capabilities.sites.map((s) => ({ value: s.id, label: s.name }))}
      placeholder={t('stock.chooseSite')}
    />
  );
}

// --- Sections -----------------------------------------------------------------------------------

const sectionSchema = z.object({
  site_id: z.string().nullable(),
  name: z.string().trim().min(1).max(100),
  sort_order: z.number().int().min(0).max(100000),
});
type SectionValues = z.infer<typeof sectionSchema>;

function SectionDialog({ section, onClose }: { section: MenuSection | null; onClose: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { siteId, chooseSite } = useDefaultSite();
  const { createSection, updateSection } = useMenuMutations();
  const needSite = section === null && chooseSite;
  const form = useForm<SectionValues>({
    resolver: zodResolver(
      needSite
        ? sectionSchema.refine((v) => v.site_id !== null, { path: ['site_id'] })
        : sectionSchema,
    ),
    defaultValues: {
      site_id: siteId,
      name: section?.name ?? '',
      sort_order: section?.sort_order ?? 0,
    },
  });
  const errors = form.formState.errors;
  const done = (message: string) => ({
    onSuccess: () => {
      toast.success(message);
      onClose();
    },
    onError: (error: unknown) => toast.error(translateError(t, error)),
  });
  const onSubmit = form.handleSubmit((values) =>
    section
      ? updateSection.mutate(
          { id: section.id, input: values },
          done(t('restaurantMenu.sectionSaved')),
        )
      : createSection.mutate(values, done(t('restaurantMenu.sectionSaved'))),
  );

  return (
    <Dialog
      header={t(section ? 'restaurantMenu.editSection' : 'restaurantMenu.newSection')}
      visible
      onHide={onClose}
      className="sm-dialog"
    >
      <form onSubmit={onSubmit} className="sm-form" noValidate>
        {needSite && (
          <FormField
            id="menu-section-site"
            label={t('layout.site')}
            required
            error={errors.site_id && t('validation.required')}
          >
            <Controller
              control={form.control}
              name="site_id"
              render={({ field }) => (
                <SiteDropdown
                  id="menu-section-site"
                  value={field.value}
                  onChange={field.onChange}
                />
              )}
            />
          </FormField>
        )}
        <FormField
          id="menu-section-name"
          label={t('restaurantMenu.sectionName')}
          required
          help={t('restaurantMenu.sectionNameHelp')}
          error={errors.name && t('validation.required')}
        >
          <InputText id="menu-section-name" maxLength={100} {...form.register('name')} autoFocus />
        </FormField>
        <FormField
          id="menu-section-order"
          label={t('restaurantMenu.sortOrder')}
          help={t('restaurantMenu.sortOrderHelp')}
        >
          <Controller
            control={form.control}
            name="sort_order"
            render={({ field }) => (
              <InputNumber
                inputId="menu-section-order"
                value={field.value}
                min={0}
                max={100000}
                onValueChange={(e) => field.onChange(e.value ?? 0)}
              />
            )}
          />
        </FormField>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button
            type="submit"
            label={t('actions.save')}
            loading={createSection.isPending || updateSection.isPending}
          />
        </div>
      </form>
    </Dialog>
  );
}

function SectionsTab({ onEdit }: { onEdit: (section: MenuSection) => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { can, capabilities } = useCapabilities();
  const canManage = can(MENU_MANAGE);
  const [table, setTable] = useState<TableState>(INITIAL_TABLE);
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<StatusFilterValue>('all');
  const debounced = useDebouncedValue(search);
  const sections = useMenuSections(toQueryString(table, { search: debounced, status }));
  const { setSectionActive } = useMenuMutations();
  const showSite = capabilities.site === null && capabilities.sites.length > 1;
  const filtered = search !== '' || status !== 'all';

  const toggle = (section: MenuSection) => {
    const run = () =>
      setSectionActive.mutate(
        { id: section.id, active: !section.is_active },
        {
          onSuccess: () => toast.success(t('restaurantMenu.statusChanged')),
          onError: (error) => toast.error(translateError(t, error)),
        },
      );
    if (!section.is_active) return run();
    confirmAction(t, {
      header: t('restaurantMenu.deactivateSectionTitle'),
      message: t('restaurantMenu.deactivateSectionConfirm', { name: section.name }),
      acceptLabel: t('actions.deactivate'),
      danger: true,
      onAccept: run,
    });
  };

  return (
    <>
      <FilterBar
        onReset={() => {
          setSearch('');
          setStatus('all');
        }}
        active={filtered}
      >
        <SearchInput
          value={search}
          placeholder={t('restaurantMenu.searchSections')}
          onChange={(v) => {
            setSearch(v);
            setTable((s) => ({ ...s, first: 0 }));
          }}
        />
        <StatusFilter value={status} onChange={setStatus} />
      </FilterBar>
      <ServerTable
        query={sections}
        table={table}
        onTableChange={setTable}
        minWidth="36rem"
        empty={<ListEmpty filtered={filtered} title={t('restaurantMenu.noSections')} />}
      >
        <Column field="name" header={t('restaurantMenu.sectionName')} sortable />
        {showSite && (
          <Column field="site_name" sortField="site" header={t('layout.site')} sortable />
        )}
        <Column
          field="sort_order"
          sortField="position"
          header={t('restaurantMenu.sortOrder')}
          sortable
          headerClassName="sm-num"
          bodyClassName="sm-num"
        />
        <Column
          field="item_count"
          header={t('restaurantMenu.itemCount')}
          headerClassName="sm-num"
          bodyClassName="sm-num"
        />
        <Column
          header={t('restaurantMenu.status')}
          body={(s: MenuSection) => <ActiveBadge active={s.is_active} />}
        />
        {canManage && (
          <Column
            header={t('common.actions')}
            body={(s: MenuSection) => (
              <RowActions
                actions={[
                  {
                    key: 'edit',
                    label: t('actions.edit'),
                    icon: 'pi pi-pencil',
                    onClick: () => onEdit(s),
                  },
                  {
                    key: 'status',
                    label: t(s.is_active ? 'actions.deactivate' : 'actions.activate'),
                    icon: s.is_active ? 'pi pi-ban' : 'pi pi-check-circle',
                    danger: s.is_active,
                    onClick: () => toggle(s),
                  },
                ]}
              />
            )}
          />
        )}
      </ServerTable>
    </>
  );
}

// --- Éléments -----------------------------------------------------------------------------------

function SectionDropdown({
  id,
  siteId,
  value,
  onChange,
  invalid,
}: {
  id: string;
  siteId: string | null;
  value: string | null;
  onChange: (value: string | null) => void;
  invalid?: boolean;
}) {
  const { t } = useTranslation();
  const query = new URLSearchParams({ status: 'active', limit: '100' });
  if (siteId) query.set('site_id', siteId);
  const sections = useMenuSections(query.toString(), siteId !== null);
  return (
    <Dropdown
      inputId={id}
      value={value}
      onChange={(e) => onChange(e.value as string | null)}
      options={(sections.data?.items ?? []).map((s) => ({ value: s.id, label: s.name }))}
      placeholder={t('restaurantMenu.chooseSection')}
      emptyMessage={t('restaurantMenu.noActiveSection')}
      invalid={invalid}
    />
  );
}

interface ItemValues {
  site_id: string | null;
  section_id: string | null;
  article: ArticleOption | null;
  packaging: SalePackaging | null;
  display_name: string;
  description: string;
  sort_order: number;
}

const itemSchema = z.object({
  site_id: z.string().nullable(),
  section_id: z.string().nullable(),
  article: z.custom<ArticleOption | null>(),
  packaging: z.custom<SalePackaging | null>(),
  display_name: z.string().max(150),
  description: z.string().max(500),
  sort_order: z.number().int().min(0).max(100000),
});

/** Ajout (présentation : article de l'assortiment du site + unité de base ou conditionnement
 *  au prix configuré) ou modification (section, libellé, description, ordre). Le serveur
 *  revérifie tout : assortiment, article actif, conditionnement, prix, unicité. */
function ItemDialog({ item, onClose }: { item: MenuItem | null; onClose: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { siteId, chooseSite } = useDefaultSite();
  const { createItem, updateItem } = useMenuMutations();
  const creating = item === null;
  const form = useForm<ItemValues>({
    resolver: zodResolver(
      itemSchema
        .refine((v) => v.section_id !== null, { path: ['section_id'] })
        .refine((v) => !creating || v.article !== null, { path: ['article'] })
        .refine((v) => !creating || !chooseSite || v.site_id !== null, { path: ['site_id'] }),
    ),
    defaultValues: {
      site_id: item?.site_id ?? siteId,
      section_id: item?.section_id ?? null,
      article: null,
      packaging: null,
      display_name: item?.display_name ?? '',
      description: item?.description ?? '',
      sort_order: item?.sort_order ?? 0,
    },
  });
  const errors = form.formState.errors;
  const site = useWatch({ control: form.control, name: 'site_id' });
  const article = useWatch({ control: form.control, name: 'article' });
  const done = {
    onSuccess: () => {
      toast.success(t('restaurantMenu.itemSaved'));
      onClose();
    },
    onError: (error: unknown) => toast.error(translateError(t, error)),
  };
  const onSubmit = form.handleSubmit((values) => {
    const details = {
      section_id: values.section_id ?? '',
      display_name: values.display_name.trim() || null,
      description: values.description.trim() || null,
      sort_order: values.sort_order,
    };
    if (item) return updateItem.mutate({ id: item.id, input: details }, done);
    createItem.mutate(
      {
        ...details,
        site_id: values.site_id,
        article_id: values.article?.id ?? '',
        packaging_id: values.packaging?.id ?? null,
      },
      done,
    );
  });

  return (
    <Dialog
      header={t(creating ? 'restaurantMenu.newItem' : 'restaurantMenu.editItem')}
      visible
      onHide={onClose}
      className="sm-dialog"
    >
      <form onSubmit={onSubmit} className="sm-form" noValidate>
        {creating && chooseSite && (
          <FormField
            id="menu-item-site"
            label={t('layout.site')}
            required
            error={errors.site_id && t('validation.required')}
          >
            <Controller
              control={form.control}
              name="site_id"
              render={({ field }) => (
                <SiteDropdown
                  id="menu-item-site"
                  value={field.value}
                  onChange={(v) => {
                    field.onChange(v);
                    form.setValue('section_id', null);
                  }}
                />
              )}
            />
          </FormField>
        )}
        {creating ? (
          <>
            <FormField
              id="menu-item-article"
              label={t('restaurantMenu.article')}
              required
              help={t('restaurantMenu.articleHelp')}
              error={errors.article && t('validation.required')}
            >
              <Controller
                control={form.control}
                name="article"
                render={({ field }) => (
                  <ArticlePicker
                    id="menu-item-article"
                    value={field.value}
                    siteId={site}
                    invalid={!!errors.article}
                    onChange={(v) => {
                      field.onChange(v);
                      form.setValue('packaging', null);
                    }}
                  />
                )}
              />
            </FormField>
            {article && (
              <FormField
                id="menu-item-presentation"
                label={t('restaurantMenu.presentation')}
                help={t('restaurantMenu.presentationHelp')}
              >
                <Controller
                  control={form.control}
                  name="packaging"
                  render={({ field }) => (
                    <PackagingSelect
                      id="menu-item-presentation"
                      articleId={article.id}
                      unit={article.unit}
                      basePrice={article.sale_price}
                      value={field.value}
                      onChange={field.onChange}
                    />
                  )}
                />
              </FormField>
            )}
          </>
        ) : (
          <FormField id="menu-item-presentation-ro" label={t('restaurantMenu.presentation')}>
            <InputText
              id="menu-item-presentation-ro"
              value={`${item.reference} — ${item.designation}${
                item.packaging_name ? ` — ${item.packaging_name}` : ''
              }`}
              disabled
            />
          </FormField>
        )}
        <FormField
          id="menu-item-section"
          label={t('restaurantMenu.section')}
          required
          error={errors.section_id && t('validation.required')}
        >
          <Controller
            control={form.control}
            name="section_id"
            render={({ field }) => (
              <SectionDropdown
                id="menu-item-section"
                siteId={site}
                value={field.value}
                onChange={field.onChange}
                invalid={!!errors.section_id}
              />
            )}
          />
        </FormField>
        <FormField
          id="menu-item-name"
          label={t('restaurantMenu.displayName')}
          help={t('restaurantMenu.displayNameHelp')}
        >
          <InputText id="menu-item-name" maxLength={150} {...form.register('display_name')} />
        </FormField>
        <FormField id="menu-item-description" label={t('restaurantMenu.description')}>
          <InputTextarea
            id="menu-item-description"
            maxLength={500}
            rows={2}
            {...form.register('description')}
          />
        </FormField>
        <FormField
          id="menu-item-order"
          label={t('restaurantMenu.sortOrder')}
          help={t('restaurantMenu.sortOrderHelp')}
        >
          <Controller
            control={form.control}
            name="sort_order"
            render={({ field }) => (
              <InputNumber
                inputId="menu-item-order"
                value={field.value}
                min={0}
                max={100000}
                onValueChange={(e) => field.onChange(e.value ?? 0)}
              />
            )}
          />
        </FormField>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button
            type="submit"
            label={t('actions.save')}
            loading={createItem.isPending || updateItem.isPending}
          />
        </div>
      </form>
    </Dialog>
  );
}

/** « Épuisé » manuel, avec un motif facultatif (aucun effet sur le stock). */
function UnavailableDialog({ item, onClose }: { item: MenuItem; onClose: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { setAvailability } = useMenuMutations();
  const [reason, setReason] = useState('');
  const submit = () =>
    setAvailability.mutate(
      { id: item.id, available: false, reason: reason.trim() || null },
      {
        onSuccess: () => {
          toast.success(t('restaurantMenu.markedUnavailable', { name: itemLabel(item) }));
          onClose();
        },
        onError: (error) => toast.error(translateError(t, error)),
      },
    );
  return (
    <Dialog
      header={t('restaurantMenu.markUnavailable')}
      visible
      onHide={onClose}
      className="sm-dialog"
    >
      <div className="sm-form">
        <p>{t('restaurantMenu.unavailableHelp', { name: itemLabel(item) })}</p>
        <FormField id="menu-unavailable-reason" label={t('restaurantMenu.unavailableReason')}>
          <InputText
            id="menu-unavailable-reason"
            maxLength={200}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            autoFocus
          />
        </FormField>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button
            type="button"
            label={t('restaurantMenu.markUnavailable')}
            severity="danger"
            loading={setAvailability.isPending}
            onClick={submit}
          />
        </div>
      </div>
    </Dialog>
  );
}

function OrderableCell({ item }: { item: MenuItem }) {
  const { t } = useTranslation();
  if (item.orderable) {
    return <StatusBadge tone="success" label={t('restaurantMenu.orderable')} />;
  }
  return (
    <div className="sm-stack-xs">
      <StatusBadge tone="warning" label={t('restaurantMenu.notOrderable')} />
      <small className="sm-help">
        {item.blockers.map((b) => t(`restaurantMenu.blockers.${b}`)).join(' · ')}
      </small>
    </div>
  );
}

function ItemsTab({ onEdit, onNew }: { onEdit: (item: MenuItem) => void; onNew?: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { can, capabilities } = useCapabilities();
  const { currency, locale } = capabilities.tenant;
  const canManage = can(MENU_MANAGE);
  const canAvailability = can(MENU_AVAILABILITY);
  const [table, setTable] = useState<TableState>(INITIAL_TABLE);
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<StatusFilterValue>('all');
  const [availability, setAvailability] = useState<AvailabilityValue>('all');
  const [section, setSection] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState<MenuItem | null>(null);
  const debounced = useDebouncedValue(search);
  const items = useMenuItems(
    toQueryString(table, {
      search: debounced,
      status,
      availability: availability === 'all' ? null : availability,
      section_id: section,
    }),
  );
  const sections = useMenuSections('limit=100&sort=position');
  const mutations = useMenuMutations();
  const showSite = capabilities.site === null && capabilities.sites.length > 1;
  const filtered = search !== '' || status !== 'all' || availability !== 'all' || section !== null;
  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));
  const notify = {
    onSuccess: () => toast.success(t('restaurantMenu.statusChanged')),
    onError: (error: unknown) => toast.error(translateError(t, error)),
  };

  const toggleActive = (item: MenuItem) => {
    const run = () =>
      mutations.setItemActive.mutate({ id: item.id, active: !item.is_active }, notify);
    if (!item.is_active) return run();
    confirmAction(t, {
      header: t('restaurantMenu.deactivateItemTitle'),
      message: t('restaurantMenu.deactivateItemConfirm', { name: itemLabel(item) }),
      acceptLabel: t('actions.deactivate'),
      danger: true,
      onAccept: run,
    });
  };

  return (
    <>
      <FilterBar
        onReset={() => {
          setSearch('');
          setStatus('all');
          setAvailability('all');
          setSection(null);
          resetPage();
        }}
        active={filtered}
      >
        <SearchInput
          value={search}
          placeholder={t('restaurantMenu.searchItems')}
          onChange={(v) => {
            setSearch(v);
            resetPage();
          }}
        />
        <Dropdown
          value={section}
          onChange={(e) => {
            setSection(e.value as string | null);
            resetPage();
          }}
          options={(sections.data?.items ?? []).map((s) => ({
            value: s.id,
            label: showSite ? `${s.name} (${s.site_name})` : s.name,
          }))}
          placeholder={t('restaurantMenu.allSections')}
          showClear
          aria-label={t('restaurantMenu.section')}
        />
        <Dropdown
          value={availability}
          onChange={(e) => {
            setAvailability(e.value as AvailabilityValue);
            resetPage();
          }}
          options={(['all', 'available', 'unavailable'] as const).map((v) => ({
            value: v,
            label: t(`restaurantMenu.availabilityFilter.${v}`),
          }))}
          aria-label={t('restaurantMenu.availability')}
        />
        <StatusFilter
          value={status}
          onChange={(v) => {
            setStatus(v);
            resetPage();
          }}
        />
      </FilterBar>
      <ServerTable
        query={items}
        table={table}
        onTableChange={setTable}
        minWidth="56rem"
        empty={
          <ListEmpty
            filtered={filtered}
            title={t('restaurantMenu.noItems')}
            action={
              canManage &&
              onNew && (
                <Button
                  icon="pi pi-plus"
                  label={t('restaurantMenu.newItem')}
                  outlined
                  onClick={onNew}
                />
              )
            }
          />
        }
      >
        <Column
          header={t('restaurantMenu.item')}
          sortField="name"
          sortable
          body={(i: MenuItem) => (
            <div className="sm-stack-xs">
              <span>{itemLabel(i)}</span>
              <small className="sm-help">{i.reference}</small>
            </div>
          )}
        />
        <Column
          field="section_name"
          sortField="section"
          header={t('restaurantMenu.section')}
          sortable
        />
        {showSite && <Column field="site_name" header={t('layout.site')} />}
        <Column
          header={t('restaurantMenu.price')}
          headerClassName="sm-num"
          bodyClassName="sm-num"
          body={(i: MenuItem) => (i.price === null ? '—' : formatMoney(i.price, currency, locale))}
        />
        <Column
          header={t('restaurantMenu.availability')}
          body={(i: MenuItem) => (
            <div className="sm-stack-xs">
              <StatusBadge
                tone={i.available ? 'success' : 'danger'}
                label={t(i.available ? 'restaurantMenu.available' : 'restaurantMenu.unavailable')}
              />
              {i.unavailable_reason && <small className="sm-help">{i.unavailable_reason}</small>}
            </div>
          )}
        />
        <Column
          header={t('restaurantMenu.orderableHeader')}
          body={(i: MenuItem) => <OrderableCell item={i} />}
        />
        <Column
          header={t('restaurantMenu.status')}
          body={(i: MenuItem) => <ActiveBadge active={i.is_active} />}
        />
        {(canManage || canAvailability) && (
          <Column
            header={t('common.actions')}
            body={(i: MenuItem) => (
              <RowActions
                actions={[
                  {
                    key: 'availability',
                    label: t(
                      i.available
                        ? 'restaurantMenu.markUnavailable'
                        : 'restaurantMenu.markAvailable',
                    ),
                    icon: i.available ? 'pi pi-times-circle' : 'pi pi-check',
                    hidden: !canAvailability,
                    onClick: () =>
                      i.available
                        ? setUnavailable(i)
                        : mutations.setAvailability.mutate(
                            { id: i.id, available: true, reason: null },
                            notify,
                          ),
                  },
                  {
                    key: 'edit',
                    label: t('actions.edit'),
                    icon: 'pi pi-pencil',
                    hidden: !canManage,
                    onClick: () => onEdit(i),
                  },
                  {
                    key: 'status',
                    label: t(i.is_active ? 'actions.deactivate' : 'actions.activate'),
                    icon: i.is_active ? 'pi pi-ban' : 'pi pi-check-circle',
                    danger: i.is_active,
                    hidden: !canManage,
                    onClick: () => toggleActive(i),
                  },
                ]}
              />
            )}
          />
        )}
      </ServerTable>
      {unavailable && <UnavailableDialog item={unavailable} onClose={() => setUnavailable(null)} />}
    </>
  );
}

/**
 * Menu des sites de restauration (palier R1, ADR-0049) : sections et éléments d'UN site,
 * présentations du catalogue limitées à l'assortiment du site, prix du catalogue (aucun prix
 * par site), « épuisé » manuel actualisé toutes les 15 s, état « commandable » calculé par le
 * serveur. Configuration : `restaurant.menu.manage` ; « épuisé » : `restaurant.menu.availability`.
 */
export default function MenuPage() {
  const { t } = useTranslation();
  const { can } = useCapabilities();
  const canManage = can(MENU_MANAGE);
  const [section, setSection] = useState<MenuSection | null | undefined>(undefined);
  const [item, setItem] = useState<MenuItem | null | undefined>(undefined);

  return (
    <>
      <PageHeader
        title={t('restaurantMenu.title')}
        description={t('restaurantMenu.subtitle')}
        actions={
          canManage && (
            <>
              <Button
                icon="pi pi-plus"
                label={t('restaurantMenu.newSection')}
                outlined
                onClick={() => setSection(null)}
              />
              <Button
                icon="pi pi-plus"
                label={t('restaurantMenu.newItem')}
                onClick={() => setItem(null)}
              />
            </>
          )
        }
      />
      <TabView>
        <TabPanel header={t('restaurantMenu.itemsTab')}>
          <ItemsTab onEdit={setItem} onNew={() => setItem(null)} />
        </TabPanel>
        <TabPanel header={t('restaurantMenu.sectionsTab')}>
          <SectionsTab onEdit={setSection} />
        </TabPanel>
      </TabView>
      {section !== undefined && (
        <SectionDialog section={section} onClose={() => setSection(undefined)} />
      )}
      {item !== undefined && <ItemDialog item={item} onClose={() => setItem(undefined)} />}
    </>
  );
}
