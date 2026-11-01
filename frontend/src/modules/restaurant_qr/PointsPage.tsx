import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { InputNumber } from 'primereact/inputnumber';
import { InputText } from 'primereact/inputtext';
import { Message } from 'primereact/message';
import { SelectButton } from 'primereact/selectbutton';
import { TabPanel, TabView } from 'primereact/tabview';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { SiteDropdown, useDefaultSite } from '@/modules/restaurant_kitchen/sites';
import { useTables } from '@/modules/restaurant_tables/api';
import { translateError } from '@/shared/lib/errors';
import { formatDateTime } from '@/shared/lib/format';
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
import { ActiveBadge, StatusBadge } from '@/shared/ui/StatusBadge';
import { confirmAction } from '@/shared/ui/confirm';
import { useToast } from '@/shared/ui/toast';

import {
  CONTACT_MODES,
  POINT_MANAGE,
  QR_SETTINGS_MANAGE,
  useQrMutations,
  useQrPoints,
  useQrSettings,
  type ContactFieldMode,
  type QrPoint,
  type QrPointIssued,
  type QrPointKind,
  type QrSettings,
} from './api';
import { QrIssuedDialog } from './QrIssuedDialog';

/**
 * Points QR des sites (palier R7, ADR-0052) : QR de table (table active du site) ou de
 * comptoir ; un QR Code ne s'affiche qu'à sa création ou à sa régénération (réimprimer =
 * régénérer, ce qui invalide aussitôt les QR imprimés). Réglages QR du site : plafonds et
 * informations demandées au client. Le serveur fait foi pour tout.
 */

function NewPointDialog({
  onClose,
  onIssued,
}: {
  onClose: () => void;
  onIssued: (issued: QrPointIssued) => void;
}) {
  const { t } = useTranslation();
  const { hasModule } = useCapabilities();
  const { siteId: defaultSite, chooseSite } = useDefaultSite();
  const [siteId, setSiteId] = useState<string | null>(defaultSite);
  const tablesEnabled = hasModule('restaurant.tables');
  const [kind, setKind] = useState<QrPointKind>(tablesEnabled ? 'TABLE' : 'COUNTER');
  const [tableId, setTableId] = useState<string | null>(null);
  const [label, setLabel] = useState('');
  const [error, setError] = useState<string | null>(null);
  const tables = useTables(
    `limit=100&sort=position&status=active${siteId ? `&site_id=${siteId}` : ''}`,
    tablesEnabled && kind === 'TABLE',
  );
  const { create } = useQrMutations();
  const valid = siteId !== null && (kind === 'TABLE' ? tableId !== null : label.trim() !== '');
  const submit = () => {
    if (!valid || siteId === null) return;
    setError(null);
    create.mutate(
      kind === 'TABLE'
        ? { site_id: siteId, kind, table_id: tableId }
        : { site_id: siteId, kind, label: label.trim() },
      { onSuccess: onIssued, onError: (failure) => setError(translateError(t, failure)) },
    );
  };
  return (
    <Dialog header={t('restaurantQr.newPoint')} visible onHide={onClose} className="sm-dialog">
      <div className="sm-form">
        {chooseSite && (
          <FormField id="qr-site" label={t('layout.site')} required>
            <SiteDropdown id="qr-site" value={siteId} onChange={setSiteId} />
          </FormField>
        )}
        <FormField id="qr-kind" label={t('restaurantQr.kind')} required>
          <SelectButton
            id="qr-kind"
            value={kind}
            allowEmpty={false}
            onChange={(e) => setKind(e.value as QrPointKind)}
            options={(['TABLE', 'COUNTER'] as const).map((v) => ({
              value: v,
              label: t(`restaurantQr.kinds.${v}`),
              disabled: v === 'TABLE' && !tablesEnabled,
            }))}
          />
        </FormField>
        {kind === 'TABLE' ? (
          <FormField id="qr-table" label={t('restaurantQr.table')} required>
            <Dropdown
              inputId="qr-table"
              value={tableId}
              onChange={(e) => setTableId((e.value as string | undefined) ?? null)}
              options={(tables.data?.items ?? []).map((table) => ({
                value: table.id,
                label: table.zone_name ? `${table.label} (${table.zone_name})` : table.label,
              }))}
              placeholder={t('restaurantQr.chooseTable')}
              filter
            />
          </FormField>
        ) : (
          <FormField
            id="qr-label"
            label={t('restaurantQr.label')}
            help={t('restaurantQr.labelHelp')}
            required
          >
            <InputText
              id="qr-label"
              maxLength={100}
              value={label}
              onChange={(e) => setLabel(e.target.value)}
            />
          </FormField>
        )}
        {error && <Message severity="error" text={error} />}
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button
            type="button"
            icon="pi pi-qrcode"
            label={t('restaurantQr.generate')}
            disabled={!valid}
            loading={create.isPending}
            onClick={submit}
          />
        </div>
      </div>
    </Dialog>
  );
}

function RenameDialog({ point, onClose }: { point: QrPoint; onClose: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const [label, setLabel] = useState(point.label ?? '');
  const { rename } = useQrMutations();
  return (
    <Dialog header={t('restaurantQr.rename')} visible onHide={onClose} className="sm-dialog">
      <div className="sm-form">
        <FormField id="qr-rename" label={t('restaurantQr.label')} required>
          <InputText
            id="qr-rename"
            maxLength={100}
            value={label}
            onChange={(e) => setLabel(e.target.value)}
          />
        </FormField>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button
            type="button"
            label={t('actions.save')}
            disabled={label.trim() === ''}
            loading={rename.isPending}
            onClick={() =>
              rename.mutate(
                { id: point.id, label: label.trim() },
                {
                  onSuccess: () => {
                    toast.success(t('restaurantQr.saved'));
                    onClose();
                  },
                  onError: (error) => toast.error(translateError(t, error)),
                },
              )
            }
          />
        </div>
      </div>
    </Dialog>
  );
}

function PointsTab({ onIssued }: { onIssued: (issued: QrPointIssued) => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { can, capabilities } = useCapabilities();
  const { locale, timezone } = capabilities.tenant;
  const canManage = can(POINT_MANAGE);
  const [table, setTable] = useState<TableState>(INITIAL_TABLE);
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<StatusFilterValue>('all');
  const [creating, setCreating] = useState(false);
  const [renaming, setRenaming] = useState<QrPoint | null>(null);
  const debounced = useDebouncedValue(search);
  const points = useQrPoints(toQueryString(table, { search: debounced, status }));
  const { setActive, regenerate } = useQrMutations();
  const showSite = capabilities.site === null && capabilities.sites.length > 1;
  const filtered = search !== '' || status !== 'all';
  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));
  const failed = (error: unknown) => toast.error(translateError(t, error));
  const toggle = (point: QrPoint) => {
    const run = () =>
      setActive.mutate(
        { id: point.id, active: !point.is_active },
        { onSuccess: () => toast.success(t('restaurantQr.statusChanged')), onError: failed },
      );
    if (!point.is_active) return run();
    confirmAction(t, {
      header: t('restaurantQr.deactivateTitle'),
      message: t('restaurantQr.deactivateConfirm', { name: point.display_name }),
      acceptLabel: t('actions.deactivate'),
      danger: true,
      onAccept: run,
    });
  };
  const renew = (point: QrPoint) =>
    confirmAction(t, {
      header: t('restaurantQr.regenerateTitle'),
      message: t('restaurantQr.regenerateConfirm', { name: point.display_name }),
      acceptLabel: t('restaurantQr.regenerate'),
      danger: true,
      onAccept: () => regenerate.mutate(point.id, { onSuccess: onIssued, onError: failed }),
    });
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
          placeholder={t('restaurantQr.search')}
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
        {canManage && (
          <Button
            icon="pi pi-plus"
            label={t('restaurantQr.newPoint')}
            onClick={() => setCreating(true)}
          />
        )}
      </FilterBar>
      <ServerTable
        query={points}
        table={table}
        onTableChange={setTable}
        minWidth="40rem"
        empty={<ListEmpty filtered={filtered} title={t('restaurantQr.noPoints')} />}
      >
        <Column
          header={t('restaurantQr.point')}
          sortField="label"
          sortable
          body={(p: QrPoint) => <strong>{p.display_name}</strong>}
        />
        <Column
          header={t('restaurantQr.kind')}
          sortField="kind"
          sortable
          body={(p: QrPoint) => (
            <StatusBadge
              tone="neutral"
              icon={p.kind === 'TABLE' ? 'pi pi-table' : 'pi pi-shop'}
              label={t(`restaurantQr.kinds.${p.kind}`)}
            />
          )}
        />
        {showSite && (
          <Column field="site_name" sortField="site" header={t('layout.site')} sortable />
        )}
        <Column
          header={t('restaurantQr.generatedAt')}
          sortField="token_generated_at"
          sortable
          body={(p: QrPoint) => formatDateTime(p.token_generated_at, locale, timezone)}
        />
        <Column
          header={t('restaurantQr.status')}
          body={(p: QrPoint) =>
            p.table_active === false ? (
              <StatusBadge tone="warning" label={t('restaurantQr.tableInactive')} />
            ) : (
              <ActiveBadge active={p.is_active} />
            )
          }
        />
        {canManage && (
          <Column
            header={t('common.actions')}
            body={(p: QrPoint) => (
              <RowActions
                actions={[
                  {
                    key: 'regenerate',
                    label: t('restaurantQr.regenerate'),
                    icon: 'pi pi-refresh',
                    onClick: () => renew(p),
                  },
                  ...(p.kind === 'COUNTER'
                    ? [
                        {
                          key: 'rename',
                          label: t('restaurantQr.rename'),
                          icon: 'pi pi-pencil',
                          onClick: () => setRenaming(p),
                        },
                      ]
                    : []),
                  {
                    key: 'status',
                    label: t(p.is_active ? 'actions.deactivate' : 'actions.activate'),
                    icon: p.is_active ? 'pi pi-ban' : 'pi pi-check-circle',
                    danger: p.is_active,
                    onClick: () => toggle(p),
                  },
                ]}
              />
            )}
          />
        )}
      </ServerTable>
      {creating && (
        <NewPointDialog
          onClose={() => setCreating(false)}
          onIssued={(issued) => {
            setCreating(false);
            onIssued(issued);
          }}
        />
      )}
      {renaming && <RenameDialog point={renaming} onClose={() => setRenaming(null)} />}
    </>
  );
}

function SettingsForm({ settings }: { settings: QrSettings }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { can } = useCapabilities();
  const editable = can(QR_SETTINGS_MANAGE);
  const { updateSettings } = useQrMutations();
  const [lines, setLines] = useState(settings.max_lines);
  const [quantity, setQuantity] = useState(settings.max_quantity_per_line);
  const [pending, setPending] = useState(settings.max_pending_per_point);
  const [nameMode, setNameMode] = useState<ContactFieldMode>(settings.customer_name_mode);
  const [phoneMode, setPhoneMode] = useState<ContactFieldMode>(settings.customer_phone_mode);
  const [error, setError] = useState<string | null>(null);
  const modes = CONTACT_MODES.map((v) => ({ value: v, label: t(`restaurantQr.modes.${v}`) }));
  const limit = (
    id: string,
    value: number,
    max: number,
    onChange: (value: number) => void,
    label: string,
  ) => (
    <FormField id={id} label={label} help={t('restaurantQr.platformMax', { max })} required>
      <InputNumber
        inputId={id}
        value={value}
        min={1}
        max={max}
        disabled={!editable}
        onValueChange={(e) => onChange(e.value ?? 1)}
      />
    </FormField>
  );
  return (
    <div className="sm-form">
      <div className="sm-form-grid">
        {limit(
          'qr-max-lines',
          lines,
          settings.platform_max_lines,
          setLines,
          t('restaurantQr.maxLines'),
        )}
        {limit(
          'qr-max-quantity',
          quantity,
          settings.platform_max_quantity_per_line,
          setQuantity,
          t('restaurantQr.maxQuantity'),
        )}
        {limit(
          'qr-max-pending',
          pending,
          settings.platform_max_pending_per_point,
          setPending,
          t('restaurantQr.maxPending'),
        )}
        <FormField id="qr-name-mode" label={t('restaurantQr.nameMode')} required>
          <SelectButton
            id="qr-name-mode"
            value={nameMode}
            allowEmpty={false}
            disabled={!editable}
            onChange={(e) => setNameMode(e.value as ContactFieldMode)}
            options={modes}
          />
        </FormField>
        <FormField
          id="qr-phone-mode"
          label={t('restaurantQr.phoneMode')}
          help={t('restaurantQr.phoneModeHelp')}
          required
        >
          <SelectButton
            id="qr-phone-mode"
            value={phoneMode}
            allowEmpty={false}
            disabled={!editable}
            onChange={(e) => setPhoneMode(e.value as ContactFieldMode)}
            options={modes}
          />
        </FormField>
      </div>
      {error && <Message severity="error" text={error} />}
      {editable && (
        <div className="sm-form-actions">
          <Button
            type="button"
            icon="pi pi-check"
            label={t('actions.save')}
            loading={updateSettings.isPending}
            onClick={() => {
              setError(null);
              updateSettings.mutate(
                {
                  siteId: settings.site_id,
                  input: {
                    max_lines: lines,
                    max_quantity_per_line: quantity,
                    max_pending_per_point: pending,
                    customer_name_mode: nameMode,
                    customer_phone_mode: phoneMode,
                  },
                },
                {
                  onSuccess: () => toast.success(t('restaurantQr.settingsSaved')),
                  onError: (failure) => setError(translateError(t, failure)),
                },
              );
            }}
          />
        </div>
      )}
    </div>
  );
}

function SettingsTab() {
  const { t } = useTranslation();
  const { siteId: defaultSite, chooseSite } = useDefaultSite();
  const [siteId, setSiteId] = useState<string | null>(defaultSite);
  const settings = useQrSettings(siteId);
  return (
    <>
      <p className="sm-help">{t('restaurantQr.settingsHelp')}</p>
      {chooseSite && (
        <div className="sm-form-grid">
          <FormField id="qr-settings-site" label={t('layout.site')} required>
            <SiteDropdown id="qr-settings-site" value={siteId} onChange={setSiteId} />
          </FormField>
        </div>
      )}
      {siteId === null ? null : settings.isPending ? (
        <LoadingState />
      ) : settings.isError ? (
        <Message severity="error" text={translateError(t, settings.error)} />
      ) : (
        <SettingsForm key={`${siteId}-${settings.data.updated_at}`} settings={settings.data} />
      )}
    </>
  );
}

export default function PointsPage() {
  const { t } = useTranslation();
  const [issued, setIssued] = useState<QrPointIssued | null>(null);
  return (
    <>
      <PageHeader title={t('restaurantQr.title')} description={t('restaurantQr.subtitle')} />
      <TabView>
        <TabPanel header={t('restaurantQr.tabs.points')}>
          <PointsTab onIssued={setIssued} />
        </TabPanel>
        <TabPanel header={t('restaurantQr.tabs.settings')}>
          <SettingsTab />
        </TabPanel>
      </TabView>
      {issued && <QrIssuedDialog issued={issued} onClose={() => setIssued(null)} />}
    </>
  );
}
