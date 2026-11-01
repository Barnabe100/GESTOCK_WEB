import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { InputNumber } from 'primereact/inputnumber';
import { InputText } from 'primereact/inputtext';
import { Message } from 'primereact/message';
import { TabPanel, TabView } from 'primereact/tabview';
import { useState } from 'react';
import { Controller, useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { useMenuItems, useMenuSections, type MenuItem } from '@/modules/restaurant_menu/api';
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
import { LoadingState } from '@/shared/ui/LoadingState';
import { PageHeader } from '@/shared/ui/PageHeader';
import { RowActions } from '@/shared/ui/RowActions';
import { SearchInput } from '@/shared/ui/SearchInput';
import { ServerTable } from '@/shared/ui/ServerTable';
import { StatusFilter } from '@/shared/ui/StatusFilter';
import { ActiveBadge } from '@/shared/ui/StatusBadge';
import { confirmAction } from '@/shared/ui/confirm';
import { useToast } from '@/shared/ui/toast';

import {
  SORT_ORDER_MAX,
  STATION_MANAGE,
  useKitchenMutations,
  useRoutes,
  useStations,
  type Station,
  type StationRoute,
} from './api';
import { SiteDropdown, useDefaultSite } from './sites';

/**
 * Postes de préparation (palier R6, ADR-0050 K1-K2) : postes d'UN site (nom unique par site,
 * ordre d'affichage, désactivation protégée) et affectations du menu (section ou élément ; un
 * élément sans affectation suit sa section, sinon « Sans poste »). Le serveur fait foi.
 */

const stationSchema = z.object({
  site_id: z.string().nullable(),
  name: z.string().trim().min(1).max(100),
  sort_order: z.number().int().min(0).max(SORT_ORDER_MAX),
});
type StationValues = z.infer<typeof stationSchema>;

function StationDialog({ station, onClose }: { station: Station | null; onClose: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { siteId, chooseSite } = useDefaultSite();
  const { createStation, updateStation } = useKitchenMutations();
  const needSite = station === null && chooseSite;
  const form = useForm<StationValues>({
    resolver: zodResolver(
      needSite
        ? stationSchema.refine((v) => v.site_id !== null, { path: ['site_id'] })
        : stationSchema,
    ),
    defaultValues: {
      site_id: siteId,
      name: station?.name ?? '',
      sort_order: station?.sort_order ?? 0,
    },
  });
  const errors = form.formState.errors;
  const done = {
    onSuccess: () => {
      toast.success(t('restaurantKitchen.stationSaved'));
      onClose();
    },
    onError: (error: unknown) => toast.error(translateError(t, error)),
  };
  const onSubmit = form.handleSubmit((values) =>
    station
      ? updateStation.mutate(
          { id: station.id, input: { name: values.name, sort_order: values.sort_order } },
          done,
        )
      : createStation.mutate(values, done),
  );
  return (
    <Dialog
      header={t(station ? 'restaurantKitchen.editStation' : 'restaurantKitchen.newStation')}
      visible
      onHide={onClose}
      className="sm-dialog"
    >
      <form onSubmit={onSubmit} className="sm-form" noValidate>
        {needSite && (
          <FormField
            id="station-site"
            label={t('layout.site')}
            required
            error={errors.site_id && t('validation.required')}
          >
            <Controller
              control={form.control}
              name="site_id"
              render={({ field }) => (
                <SiteDropdown id="station-site" value={field.value} onChange={field.onChange} />
              )}
            />
          </FormField>
        )}
        <FormField
          id="station-name"
          label={t('restaurantKitchen.stationName')}
          required
          help={t('restaurantKitchen.stationNameHelp')}
          error={errors.name && t('validation.required')}
        >
          <InputText id="station-name" maxLength={100} {...form.register('name')} autoFocus />
        </FormField>
        <FormField
          id="station-order"
          label={t('restaurantKitchen.sortOrder')}
          help={t('restaurantKitchen.sortOrderHelp')}
        >
          <Controller
            control={form.control}
            name="sort_order"
            render={({ field }) => (
              <InputNumber
                inputId="station-order"
                value={field.value}
                min={0}
                max={SORT_ORDER_MAX}
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
            loading={createStation.isPending || updateStation.isPending}
          />
        </div>
      </form>
    </Dialog>
  );
}

function StationsTab({ onEdit, onNew }: { onEdit: (s: Station) => void; onNew: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { can, capabilities } = useCapabilities();
  const canManage = can(STATION_MANAGE);
  const [table, setTable] = useState<TableState>(INITIAL_TABLE);
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<StatusFilterValue>('all');
  const debounced = useDebouncedValue(search);
  const stations = useStations(toQueryString(table, { search: debounced, status }));
  const { setStationActive } = useKitchenMutations();
  const showSite = capabilities.site === null && capabilities.sites.length > 1;
  const filtered = search !== '' || status !== 'all';
  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));
  const toggle = (station: Station) => {
    const run = () =>
      setStationActive.mutate(
        { id: station.id, active: !station.is_active },
        {
          onSuccess: () => toast.success(t('restaurantKitchen.statusChanged')),
          onError: (error) => toast.error(translateError(t, error)),
        },
      );
    if (!station.is_active) return run();
    confirmAction(t, {
      header: t('restaurantKitchen.deactivateTitle'),
      message: t('restaurantKitchen.deactivateConfirm', { name: station.name }),
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
          placeholder={t('restaurantKitchen.searchStations')}
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
        query={stations}
        table={table}
        onTableChange={setTable}
        minWidth="34rem"
        empty={
          <ListEmpty
            filtered={filtered}
            title={t('restaurantKitchen.noStations')}
            action={
              canManage && (
                <Button
                  icon="pi pi-plus"
                  label={t('restaurantKitchen.newStation')}
                  outlined
                  onClick={onNew}
                />
              )
            }
          />
        }
      >
        <Column field="name" header={t('restaurantKitchen.stationName')} sortable />
        {showSite && (
          <Column field="site_name" sortField="site" header={t('layout.site')} sortable />
        )}
        <Column
          field="sort_order"
          sortField="position"
          header={t('restaurantKitchen.sortOrder')}
          sortable
          headerClassName="sm-num"
          bodyClassName="sm-num"
        />
        <Column
          field="route_count"
          header={t('restaurantKitchen.routeCount')}
          headerClassName="sm-num"
          bodyClassName="sm-num"
        />
        <Column
          header={t('restaurantKitchen.status')}
          body={(s: Station) => <ActiveBadge active={s.is_active} />}
        />
        {canManage && (
          <Column
            header={t('common.actions')}
            body={(s: Station) => (
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

/** Choix du poste d'une cible : poste actif du site, ou aucun (héritage / « Sans poste »). */
function RoutePicker({
  id,
  label,
  current,
  stations,
  emptyLabel,
  disabled,
  onChange,
}: {
  id: string;
  label: string;
  current: StationRoute | undefined;
  stations: Station[];
  emptyLabel: string;
  disabled: boolean;
  onChange: (stationId: string | null) => void;
}) {
  const { t } = useTranslation();
  const options = stations
    .filter((s) => s.is_active || s.id === current?.station_id)
    .map((s) => ({
      value: s.id,
      label: s.is_active ? s.name : t('restaurantKitchen.inactiveStation', { name: s.name }),
    }));
  return (
    <Dropdown
      inputId={id}
      aria-label={label}
      value={current?.station_id ?? null}
      options={options}
      placeholder={emptyLabel}
      showClear
      disabled={disabled}
      onChange={(e) => onChange((e.value as string | undefined) ?? null)}
    />
  );
}

function RoutesTab() {
  const { t } = useTranslation();
  const toast = useToast();
  const { can } = useCapabilities();
  const canManage = can(STATION_MANAGE);
  const { siteId: defaultSite, chooseSite } = useDefaultSite();
  const [site, setSite] = useState<string | null>(defaultSite);
  const query = (extra: Record<string, string>) =>
    new URLSearchParams({ site_id: site ?? '', limit: '100', ...extra }).toString();
  const sections = useMenuSections(query({ sort: 'position' }), site !== null);
  const items = useMenuItems(query({ sort: 'position' }), site !== null);
  const stations = useStations(query({ sort: 'position' }), site !== null);
  const routes = useRoutes(site);
  const { setRoute } = useKitchenMutations();
  const bySection = new Map(
    (routes.data ?? []).filter((r) => r.section_id).map((r) => [r.section_id, r]),
  );
  const byItem = new Map(
    (routes.data ?? []).filter((r) => r.menu_item_id).map((r) => [r.menu_item_id, r]),
  );
  const allStations = stations.data?.items ?? [];
  const save = (target: { section_id?: string; menu_item_id?: string }, stationId: string | null) =>
    setRoute.mutate(
      { site_id: site ?? '', station_id: stationId, ...target },
      {
        onSuccess: () => toast.success(t('restaurantKitchen.routeSaved')),
        onError: (error) => toast.error(translateError(t, error)),
      },
    );
  const itemsOf = (sectionId: string): MenuItem[] =>
    (items.data?.items ?? []).filter((i) => i.section_id === sectionId);
  return (
    <div className="sm-form">
      <p className="sm-help">{t('restaurantKitchen.routesHelp')}</p>
      {chooseSite && (
        <FormField id="routes-site" label={t('layout.site')} required>
          <SiteDropdown id="routes-site" value={site} onChange={setSite} />
        </FormField>
      )}
      {site === null ? (
        <Message severity="info" text={t('restaurantKitchen.chooseSite')} />
      ) : sections.isPending || routes.isPending || stations.isPending ? (
        <LoadingState />
      ) : allStations.length === 0 ? (
        <Message severity="info" text={t('restaurantKitchen.noStationsForRoutes')} />
      ) : (sections.data?.items ?? []).length === 0 ? (
        <Message severity="info" text={t('restaurantKitchen.noMenu')} />
      ) : (
        (sections.data?.items ?? []).map((section) => {
          const sectionRoute = bySection.get(section.id);
          return (
            <section key={section.id} className="sm-kitchen-routes" aria-label={section.name}>
              <div className="sm-kitchen-route-row sm-kitchen-route-section">
                <strong>{section.name}</strong>
                <RoutePicker
                  id={`route-section-${section.id}`}
                  label={t('restaurantKitchen.stationOf', { name: section.name })}
                  current={sectionRoute}
                  stations={allStations}
                  emptyLabel={t('restaurantKitchen.noStation')}
                  disabled={!canManage || setRoute.isPending}
                  onChange={(stationId) => save({ section_id: section.id }, stationId)}
                />
              </div>
              <ul>
                {itemsOf(section.id).map((item) => {
                  const name = item.display_name ?? item.designation;
                  const label = item.packaging_name ? `${name} (${item.packaging_name})` : name;
                  return (
                    <li key={item.id} className="sm-kitchen-route-row">
                      <span>{label}</span>
                      <RoutePicker
                        id={`route-item-${item.id}`}
                        label={t('restaurantKitchen.stationOf', { name: label })}
                        current={byItem.get(item.id)}
                        stations={allStations}
                        emptyLabel={
                          sectionRoute
                            ? t('restaurantKitchen.inherited', { name: sectionRoute.station_name })
                            : t('restaurantKitchen.noStation')
                        }
                        disabled={!canManage || setRoute.isPending}
                        onChange={(stationId) => save({ menu_item_id: item.id }, stationId)}
                      />
                    </li>
                  );
                })}
              </ul>
            </section>
          );
        })
      )}
    </div>
  );
}

export default function StationsPage() {
  const { t } = useTranslation();
  const { can } = useCapabilities();
  const [station, setStation] = useState<Station | null | undefined>(undefined);
  return (
    <>
      <PageHeader
        title={t('restaurantKitchen.stationsTitle')}
        description={t('restaurantKitchen.stationsSubtitle')}
        actions={
          can(STATION_MANAGE) && (
            <Button
              icon="pi pi-plus"
              label={t('restaurantKitchen.newStation')}
              onClick={() => setStation(null)}
            />
          )
        }
      />
      <TabView>
        <TabPanel header={t('restaurantKitchen.stationsTab')}>
          <StationsTab onEdit={setStation} onNew={() => setStation(null)} />
        </TabPanel>
        <TabPanel header={t('restaurantKitchen.routesTab')}>
          <RoutesTab />
        </TabPanel>
      </TabView>
      {station !== undefined && (
        <StationDialog station={station} onClose={() => setStation(undefined)} />
      )}
    </>
  );
}
