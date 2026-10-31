import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { InputNumber } from 'primereact/inputnumber';
import { InputText } from 'primereact/inputtext';
import { TabPanel, TabView } from 'primereact/tabview';
import { useState } from 'react';
import { Controller, useForm, useWatch } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
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
  SEATS_MAX,
  SORT_ORDER_MAX,
  TABLE_MANAGE,
  useTableMutations,
  useTables,
  useZones,
  type Table,
  type Zone,
} from './api';

/** Valeur du filtre de zone désignant les tables sans zone (`without_zone=true`). */
const WITHOUT_ZONE = '__without_zone__';

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

function SortOrderInput({
  id,
  value,
  onChange,
}: {
  id: string;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <InputNumber
      inputId={id}
      value={value}
      min={0}
      max={SORT_ORDER_MAX}
      onValueChange={(e) => onChange(e.value ?? 0)}
    />
  );
}

// --- Zones --------------------------------------------------------------------------------------

const zoneSchema = z.object({
  site_id: z.string().nullable(),
  name: z.string().trim().min(1).max(100),
  sort_order: z.number().int().min(0).max(SORT_ORDER_MAX),
});
type ZoneValues = z.infer<typeof zoneSchema>;

function ZoneDialog({ zone, onClose }: { zone: Zone | null; onClose: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { siteId, chooseSite } = useDefaultSite();
  const { createZone, updateZone } = useTableMutations();
  const needSite = zone === null && chooseSite;
  const form = useForm<ZoneValues>({
    resolver: zodResolver(
      needSite ? zoneSchema.refine((v) => v.site_id !== null, { path: ['site_id'] }) : zoneSchema,
    ),
    defaultValues: { site_id: siteId, name: zone?.name ?? '', sort_order: zone?.sort_order ?? 0 },
  });
  const errors = form.formState.errors;
  const done = {
    onSuccess: () => {
      toast.success(t('restaurantTables.zoneSaved'));
      onClose();
    },
    onError: (error: unknown) => toast.error(translateError(t, error)),
  };
  const onSubmit = form.handleSubmit((values) =>
    zone
      ? updateZone.mutate(
          { id: zone.id, input: { name: values.name, sort_order: values.sort_order } },
          done,
        )
      : createZone.mutate(values, done),
  );

  return (
    <Dialog
      header={t(zone ? 'restaurantTables.editZone' : 'restaurantTables.newZone')}
      visible
      onHide={onClose}
      className="sm-dialog"
    >
      <form onSubmit={onSubmit} className="sm-form" noValidate>
        {needSite && (
          <FormField
            id="zone-site"
            label={t('layout.site')}
            required
            error={errors.site_id && t('validation.required')}
          >
            <Controller
              control={form.control}
              name="site_id"
              render={({ field }) => (
                <SiteDropdown id="zone-site" value={field.value} onChange={field.onChange} />
              )}
            />
          </FormField>
        )}
        <FormField
          id="zone-name"
          label={t('restaurantTables.zoneName')}
          required
          help={t('restaurantTables.zoneNameHelp')}
          error={errors.name && t('validation.required')}
        >
          <InputText id="zone-name" maxLength={100} {...form.register('name')} autoFocus />
        </FormField>
        <FormField
          id="zone-order"
          label={t('restaurantTables.sortOrder')}
          help={t('restaurantTables.sortOrderHelp')}
        >
          <Controller
            control={form.control}
            name="sort_order"
            render={({ field }) => (
              <SortOrderInput id="zone-order" value={field.value} onChange={field.onChange} />
            )}
          />
        </FormField>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button
            type="submit"
            label={t('actions.save')}
            loading={createZone.isPending || updateZone.isPending}
          />
        </div>
      </form>
    </Dialog>
  );
}

function ZonesTab({ onEdit, onNew }: { onEdit: (zone: Zone) => void; onNew?: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { can, capabilities } = useCapabilities();
  const canManage = can(TABLE_MANAGE);
  const [table, setTable] = useState<TableState>(INITIAL_TABLE);
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<StatusFilterValue>('all');
  const debounced = useDebouncedValue(search);
  const zones = useZones(toQueryString(table, { search: debounced, status }));
  const { setZoneActive } = useTableMutations();
  const showSite = capabilities.site === null && capabilities.sites.length > 1;
  const filtered = search !== '' || status !== 'all';
  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));

  const toggle = (zone: Zone) => {
    const run = () =>
      setZoneActive.mutate(
        { id: zone.id, active: !zone.is_active },
        {
          onSuccess: () => toast.success(t('restaurantTables.statusChanged')),
          onError: (error) => toast.error(translateError(t, error)),
        },
      );
    if (!zone.is_active) return run();
    confirmAction(t, {
      header: t('restaurantTables.deactivateZoneTitle'),
      message: t('restaurantTables.deactivateZoneConfirm', { name: zone.name }),
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
          resetPage();
        }}
        active={filtered}
      >
        <SearchInput
          value={search}
          placeholder={t('restaurantTables.searchZones')}
          onChange={(v) => {
            setSearch(v);
            resetPage();
          }}
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
        query={zones}
        table={table}
        onTableChange={setTable}
        minWidth="36rem"
        empty={
          <ListEmpty
            filtered={filtered}
            title={t('restaurantTables.noZones')}
            action={
              canManage &&
              onNew && (
                <Button
                  icon="pi pi-plus"
                  label={t('restaurantTables.newZone')}
                  outlined
                  onClick={onNew}
                />
              )
            }
          />
        }
      >
        <Column field="name" header={t('restaurantTables.zoneName')} sortable />
        {showSite && (
          <Column field="site_name" sortField="site" header={t('layout.site')} sortable />
        )}
        <Column
          field="sort_order"
          sortField="position"
          header={t('restaurantTables.sortOrder')}
          sortable
          headerClassName="sm-num"
          bodyClassName="sm-num"
        />
        <Column
          header={t('restaurantTables.activeTables')}
          headerClassName="sm-num"
          bodyClassName="sm-num"
          body={(z: Zone) =>
            t('restaurantTables.activeOfTotal', {
              active: z.active_table_count,
              total: z.table_count,
            })
          }
        />
        <Column
          header={t('restaurantTables.status')}
          body={(z: Zone) => <ActiveBadge active={z.is_active} />}
        />
        {canManage && (
          <Column
            header={t('common.actions')}
            body={(z: Zone) => (
              <RowActions
                actions={[
                  {
                    key: 'edit',
                    label: t('actions.edit'),
                    icon: 'pi pi-pencil',
                    onClick: () => onEdit(z),
                  },
                  {
                    key: 'status',
                    label: t(z.is_active ? 'actions.deactivate' : 'actions.activate'),
                    icon: z.is_active ? 'pi pi-ban' : 'pi pi-check-circle',
                    danger: z.is_active,
                    onClick: () => toggle(z),
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

// --- Tables -------------------------------------------------------------------------------------

/** Zones actives du site (la zone actuelle d'une table est conservée même désactivée : le
 *  serveur accepte de la garder, il refuse seulement d'en choisir une autre inactive). */
function ZoneDropdown({
  id,
  siteId,
  value,
  onChange,
  current,
}: {
  id: string;
  siteId: string | null;
  value: string | null;
  onChange: (value: string | null) => void;
  current?: { id: string; name: string; active: boolean } | null;
}) {
  const { t } = useTranslation();
  const query = new URLSearchParams({ status: 'active', limit: '100', sort: 'position' });
  if (siteId) query.set('site_id', siteId);
  const zones = useZones(query.toString(), siteId !== null);
  const options = (zones.data?.items ?? []).map((z) => ({ value: z.id, label: z.name }));
  if (current && !options.some((o) => o.value === current.id)) {
    options.push({
      value: current.id,
      label: current.active
        ? current.name
        : t('restaurantTables.inactiveZoneOption', { name: current.name }),
    });
  }
  return (
    <Dropdown
      inputId={id}
      value={value}
      onChange={(e) => onChange((e.value as string | undefined) ?? null)}
      options={options}
      placeholder={t('restaurantTables.noZone')}
      emptyMessage={t('restaurantTables.noActiveZone')}
      showClear
      disabled={siteId === null}
    />
  );
}

const tableSchema = z.object({
  site_id: z.string().nullable(),
  zone_id: z.string().nullable(),
  label: z.string().trim().min(1).max(40),
  seats: z.number().int().min(1).max(SEATS_MAX).nullable(),
  sort_order: z.number().int().min(0).max(SORT_ORDER_MAX),
});
type TableValues = z.infer<typeof tableSchema>;

function TableDialog({ table, onClose }: { table: Table | null; onClose: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { siteId, chooseSite } = useDefaultSite();
  const { createTable, updateTable } = useTableMutations();
  const needSite = table === null && chooseSite;
  const form = useForm<TableValues>({
    resolver: zodResolver(
      needSite ? tableSchema.refine((v) => v.site_id !== null, { path: ['site_id'] }) : tableSchema,
    ),
    defaultValues: {
      site_id: table?.site_id ?? siteId,
      zone_id: table?.zone_id ?? null,
      label: table?.label ?? '',
      seats: table?.seats ?? null,
      sort_order: table?.sort_order ?? 0,
    },
  });
  const site = useWatch({ control: form.control, name: 'site_id' });
  const errors = form.formState.errors;
  const done = {
    onSuccess: () => {
      toast.success(t('restaurantTables.tableSaved'));
      onClose();
    },
    onError: (error: unknown) => toast.error(translateError(t, error)),
  };
  const onSubmit = form.handleSubmit((values) => {
    const fields = {
      zone_id: values.zone_id,
      label: values.label,
      seats: values.seats,
      sort_order: values.sort_order,
    };
    if (table) updateTable.mutate({ id: table.id, input: fields }, done);
    else createTable.mutate({ site_id: values.site_id, ...fields }, done);
  });
  const current =
    table?.zone_id && table.zone_name
      ? { id: table.zone_id, name: table.zone_name, active: table.zone_active !== false }
      : null;

  return (
    <Dialog
      header={t(table ? 'restaurantTables.editTable' : 'restaurantTables.newTable')}
      visible
      onHide={onClose}
      className="sm-dialog"
    >
      <form onSubmit={onSubmit} className="sm-form" noValidate>
        {needSite && (
          <FormField
            id="table-site"
            label={t('layout.site')}
            required
            error={errors.site_id && t('validation.required')}
          >
            <Controller
              control={form.control}
              name="site_id"
              render={({ field }) => (
                <SiteDropdown
                  id="table-site"
                  value={field.value}
                  onChange={(value) => {
                    field.onChange(value);
                    // Une zone appartient à UN site : changer de site efface la zone choisie.
                    form.setValue('zone_id', null);
                  }}
                />
              )}
            />
          </FormField>
        )}
        <FormField
          id="table-label"
          label={t('restaurantTables.label')}
          required
          help={t('restaurantTables.labelHelp')}
          error={errors.label && t('validation.required')}
        >
          <InputText id="table-label" maxLength={40} {...form.register('label')} autoFocus />
        </FormField>
        <FormField
          id="table-zone"
          label={t('restaurantTables.zone')}
          help={t('restaurantTables.zoneHelp')}
        >
          <Controller
            control={form.control}
            name="zone_id"
            render={({ field }) => (
              <ZoneDropdown
                id="table-zone"
                siteId={site}
                value={field.value}
                onChange={field.onChange}
                current={current}
              />
            )}
          />
        </FormField>
        <FormField
          id="table-seats"
          label={t('restaurantTables.seats')}
          help={t('restaurantTables.seatsHelp', { max: SEATS_MAX })}
          error={errors.seats && t('restaurantTables.seatsInvalid', { max: SEATS_MAX })}
        >
          <Controller
            control={form.control}
            name="seats"
            render={({ field }) => (
              <InputNumber
                inputId="table-seats"
                value={field.value}
                min={1}
                max={SEATS_MAX}
                onValueChange={(e) => field.onChange(e.value ?? null)}
              />
            )}
          />
        </FormField>
        <FormField
          id="table-order"
          label={t('restaurantTables.sortOrder')}
          help={t('restaurantTables.sortOrderHelp')}
        >
          <Controller
            control={form.control}
            name="sort_order"
            render={({ field }) => (
              <SortOrderInput id="table-order" value={field.value} onChange={field.onChange} />
            )}
          />
        </FormField>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button
            type="submit"
            label={t('actions.save')}
            loading={createTable.isPending || updateTable.isPending}
          />
        </div>
      </form>
    </Dialog>
  );
}

function ZoneCell({ table }: { table: Table }) {
  const { t } = useTranslation();
  if (table.zone_name === null)
    return <span className="sm-help">{t('restaurantTables.noZone')}</span>;
  return (
    <div className="sm-stack-xs">
      <span>{table.zone_name}</span>
      {table.zone_active === false && (
        <StatusBadge tone="warning" label={t('restaurantTables.zoneInactive')} />
      )}
    </div>
  );
}

function TablesTab({ onEdit, onNew }: { onEdit: (table: Table) => void; onNew?: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { can, capabilities } = useCapabilities();
  const canManage = can(TABLE_MANAGE);
  const [table, setTable] = useState<TableState>(INITIAL_TABLE);
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<StatusFilterValue>('all');
  const [zone, setZone] = useState<string | null>(null);
  const debounced = useDebouncedValue(search);
  const tables = useTables(
    toQueryString(table, {
      search: debounced,
      status,
      zone_id: zone !== null && zone !== WITHOUT_ZONE ? zone : null,
      without_zone: zone === WITHOUT_ZONE ? 'true' : null,
    }),
  );
  const zones = useZones('limit=100&sort=position');
  const { setTableActive } = useTableMutations();
  const showSite = capabilities.site === null && capabilities.sites.length > 1;
  const filtered = search !== '' || status !== 'all' || zone !== null;
  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));

  const toggle = (row: Table) => {
    const run = () =>
      setTableActive.mutate(
        { id: row.id, active: !row.is_active },
        {
          onSuccess: () => toast.success(t('restaurantTables.statusChanged')),
          onError: (error) => toast.error(translateError(t, error)),
        },
      );
    if (!row.is_active) return run();
    confirmAction(t, {
      header: t('restaurantTables.deactivateTableTitle'),
      message: t('restaurantTables.deactivateTableConfirm', { name: row.label }),
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
          setZone(null);
          resetPage();
        }}
        active={filtered}
      >
        <SearchInput
          value={search}
          placeholder={t('restaurantTables.searchTables')}
          onChange={(v) => {
            setSearch(v);
            resetPage();
          }}
        />
        <Dropdown
          value={zone}
          onChange={(e) => {
            setZone((e.value as string | undefined) ?? null);
            resetPage();
          }}
          options={[
            { value: WITHOUT_ZONE, label: t('restaurantTables.withoutZone') },
            ...(zones.data?.items ?? []).map((z) => ({
              value: z.id,
              label: showSite ? `${z.name} (${z.site_name})` : z.name,
            })),
          ]}
          placeholder={t('restaurantTables.allZones')}
          showClear
          aria-label={t('restaurantTables.zone')}
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
        query={tables}
        table={table}
        onTableChange={setTable}
        minWidth="44rem"
        empty={
          <ListEmpty
            filtered={filtered}
            title={t('restaurantTables.noTables')}
            action={
              canManage &&
              onNew && (
                <Button
                  icon="pi pi-plus"
                  label={t('restaurantTables.newTable')}
                  outlined
                  onClick={onNew}
                />
              )
            }
          />
        }
      >
        <Column field="label" header={t('restaurantTables.label')} sortable />
        <Column
          header={t('restaurantTables.zone')}
          sortField="zone"
          sortable
          body={(row: Table) => <ZoneCell table={row} />}
        />
        {showSite && (
          <Column field="site_name" sortField="site" header={t('layout.site')} sortable />
        )}
        <Column
          header={t('restaurantTables.seats')}
          sortField="seats"
          sortable
          headerClassName="sm-num"
          bodyClassName="sm-num"
          body={(row: Table) => row.seats ?? '—'}
        />
        <Column
          field="sort_order"
          sortField="position"
          header={t('restaurantTables.sortOrder')}
          sortable
          headerClassName="sm-num"
          bodyClassName="sm-num"
        />
        <Column
          header={t('restaurantTables.status')}
          body={(row: Table) => <ActiveBadge active={row.is_active} />}
        />
        {canManage && (
          <Column
            header={t('common.actions')}
            body={(row: Table) => (
              <RowActions
                actions={[
                  {
                    key: 'edit',
                    label: t('actions.edit'),
                    icon: 'pi pi-pencil',
                    onClick: () => onEdit(row),
                  },
                  {
                    key: 'status',
                    label: t(row.is_active ? 'actions.deactivate' : 'actions.activate'),
                    icon: row.is_active ? 'pi pi-ban' : 'pi pi-check-circle',
                    danger: row.is_active,
                    onClick: () => toggle(row),
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

/**
 * Zones et tables des sites de restauration (palier R5-C1, ADR-0049) : configuration d'UN site
 * (libellé de table et nom de zone uniques par site, zone facultative, places de 1 à 999, ordre
 * d'affichage). Consultation : `restaurant.tables.table.view` ; gestion :
 * `restaurant.tables.table.manage`. Le serveur fait foi : unicités, zone du même site et active,
 * désactivations protégées (table avec commandes en cours, zone avec tables actives).
 */
export default function TablesPage() {
  const { t } = useTranslation();
  const { can } = useCapabilities();
  const canManage = can(TABLE_MANAGE);
  const [zone, setZone] = useState<Zone | null | undefined>(undefined);
  const [table, setTable] = useState<Table | null | undefined>(undefined);

  return (
    <>
      <PageHeader
        title={t('restaurantTables.title')}
        description={t('restaurantTables.subtitle')}
        actions={
          canManage && (
            <>
              <Button
                icon="pi pi-plus"
                label={t('restaurantTables.newZone')}
                outlined
                onClick={() => setZone(null)}
              />
              <Button
                icon="pi pi-plus"
                label={t('restaurantTables.newTable')}
                onClick={() => setTable(null)}
              />
            </>
          )
        }
      />
      <TabView>
        <TabPanel header={t('restaurantTables.tablesTab')}>
          <TablesTab onEdit={setTable} onNew={() => setTable(null)} />
        </TabPanel>
        <TabPanel header={t('restaurantTables.zonesTab')}>
          <ZonesTab onEdit={setZone} onNew={() => setZone(null)} />
        </TabPanel>
      </TabView>
      {zone !== undefined && <ZoneDialog zone={zone} onClose={() => setZone(undefined)} />}
      {table !== undefined && <TableDialog table={table} onClose={() => setTable(undefined)} />}
    </>
  );
}
