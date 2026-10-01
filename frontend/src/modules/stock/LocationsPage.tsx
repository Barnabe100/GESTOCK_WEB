import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { useState } from 'react';
import { Controller, useForm } from 'react-hook-form';
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
import { ActiveBadge } from '@/shared/ui/StatusBadge';
import { confirmAction } from '@/shared/ui/confirm';
import { useToast } from '@/shared/ui/toast';

import {
  LOCATION_MANAGE,
  useLocationMutations,
  useStockLocations,
  type StockLocation,
} from './api';

const schema = z.object({
  site_id: z.string().nullable(),
  name: z.string().trim().min(1).max(100),
});
type FormValues = z.infer<typeof schema>;

/** Création (site choisi si aucun n'est sélectionné) ou renommage d'un emplacement. */
function LocationDialog({
  location,
  onClose,
}: {
  location: StockLocation | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const { capabilities, siteId } = useCapabilities();
  const { create, rename } = useLocationMutations();
  const chooseSite = location === null && siteId === null && capabilities.sites.length > 1;
  const form = useForm<FormValues>({
    resolver: zodResolver(
      chooseSite ? schema.refine((v) => v.site_id !== null, { path: ['site_id'] }) : schema,
    ),
    defaultValues: {
      site_id:
        siteId ?? (capabilities.sites.length === 1 ? (capabilities.sites[0]?.id ?? null) : null),
      name: location?.name ?? '',
    },
  });
  const errors = form.formState.errors;
  const pending = create.isPending || rename.isPending;
  const done = (message: string) => ({
    onSuccess: () => {
      toast.success(message);
      onClose();
    },
    onError: (error: unknown) => toast.error(translateError(t, error)),
  });

  const onSubmit = form.handleSubmit((values) =>
    location
      ? rename.mutate({ id: location.id, name: values.name }, done(t('locations.renamed')))
      : create.mutate(
          { site_id: values.site_id, name: values.name },
          done(t('locations.created', { name: values.name.trim() })),
        ),
  );

  return (
    <Dialog
      header={t(location ? 'locations.rename' : 'locations.new')}
      visible
      onHide={onClose}
      className="sm-dialog"
    >
      <form onSubmit={onSubmit} className="sm-form" noValidate>
        {chooseSite && (
          <FormField
            id="location-site"
            label={t('layout.site')}
            required
            error={errors.site_id && t('validation.required')}
          >
            <Controller
              control={form.control}
              name="site_id"
              render={({ field }) => (
                <Dropdown
                  inputId="location-site"
                  value={field.value}
                  onChange={(e) => field.onChange(e.value)}
                  options={capabilities.sites.map((s) => ({ value: s.id, label: s.name }))}
                  placeholder={t('stock.chooseSite')}
                />
              )}
            />
          </FormField>
        )}
        {location && (
          <FormField id="location-site-ro" label={t('layout.site')}>
            <InputText id="location-site-ro" value={location.site_name} disabled />
          </FormField>
        )}
        <FormField
          id="location-name"
          label={t('locations.name')}
          required
          help={t('locations.nameHelp')}
          error={errors.name && t('validation.required')}
        >
          <InputText id="location-name" maxLength={100} {...form.register('name')} autoFocus />
        </FormField>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button type="submit" label={t('actions.save')} loading={pending} />
        </div>
      </form>
    </Dialog>
  );
}

/**
 * Emplacements physiques des sites (Lot 3-F) : rayons, étagères, réserves… Un emplacement
 * appartient à UN site ; jamais supprimé (désactivé, il n'est plus affectable). Liste limitée
 * aux sites visibles par le serveur ; gestion avec `stock.location.manage`.
 */
export default function LocationsPage() {
  const { t } = useTranslation();
  const toast = useToast();
  const { can, capabilities } = useCapabilities();
  const canManage = can(LOCATION_MANAGE);
  const [table, setTable] = useState<TableState>({
    ...INITIAL_TABLE,
    sortField: 'name',
    sortOrder: 1,
  });
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<StatusFilterValue>('all');
  const [editing, setEditing] = useState<StockLocation | null | undefined>(undefined);
  const debounced = useDebouncedValue(search);
  const locations = useStockLocations(toQueryString(table, { search: debounced, status }));
  const { setActive } = useLocationMutations();
  const showSite = capabilities.site === null && capabilities.sites.length > 1;

  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));
  const filtered = search !== '' || status !== 'all';
  const resetFilters = () => {
    setSearch('');
    setStatus('all');
    resetPage();
  };

  // Désactivation : confirmée (l'emplacement ne sera plus affectable) ; réactivation directe.
  const toggle = (location: StockLocation) => {
    const run = () =>
      setActive.mutate(
        { id: location.id, active: !location.is_active },
        {
          onSuccess: () => toast.success(t('locations.statusChanged')),
          onError: (error) => toast.error(translateError(t, error)),
        },
      );
    if (!location.is_active) return run();
    confirmAction(t, {
      header: t('locations.deactivateTitle'),
      message: t('locations.deactivateConfirm', { name: location.name }),
      acceptLabel: t('actions.deactivate'),
      danger: true,
      onAccept: run,
    });
  };

  const newButton = (outlined = false) =>
    canManage && (
      <Button
        icon="pi pi-plus"
        label={t('locations.new')}
        outlined={outlined}
        onClick={() => setEditing(null)}
      />
    );

  return (
    <>
      <PageHeader
        title={t('locations.title')}
        description={t('locations.subtitle')}
        actions={newButton()}
      />
      <FilterBar onReset={resetFilters} active={filtered}>
        <SearchInput
          value={search}
          placeholder={t('locations.search')}
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
        query={locations}
        table={table}
        onTableChange={setTable}
        minWidth="36rem"
        empty={
          <ListEmpty filtered={filtered} title={t('locations.empty')} action={newButton(true)} />
        }
      >
        <Column field="name" header={t('locations.name')} sortable />
        {showSite && (
          <Column field="site_name" sortField="site" header={t('layout.site')} sortable />
        )}
        <Column
          field="article_count"
          header={t('locations.articleCount')}
          sortable
          headerClassName="sm-num"
          bodyClassName="sm-num"
        />
        <Column
          header={t('locations.status')}
          body={(l: StockLocation) => <ActiveBadge active={l.is_active} />}
        />
        {canManage && (
          <Column
            header={t('common.actions')}
            body={(l: StockLocation) => (
              <RowActions
                actions={[
                  {
                    key: 'rename',
                    label: t('locations.rename'),
                    icon: 'pi pi-pencil',
                    onClick: () => setEditing(l),
                  },
                  {
                    key: 'status',
                    label: t(l.is_active ? 'actions.deactivate' : 'actions.activate'),
                    icon: l.is_active ? 'pi pi-ban' : 'pi pi-check-circle',
                    danger: l.is_active,
                    onClick: () => toggle(l),
                  },
                ]}
              />
            )}
          />
        )}
      </ServerTable>
      {editing !== undefined && (
        <LocationDialog location={editing} onClose={() => setEditing(undefined)} />
      )}
    </>
  );
}
