import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatQuantity } from '@/shared/lib/decimal';
import { translateError } from '@/shared/lib/errors';
import { formatDate } from '@/shared/lib/format';
import {
  INITIAL_TABLE,
  toQueryString,
  useDebouncedValue,
  type TableState,
} from '@/shared/lib/serverTable';
import { ListEmpty } from '@/shared/ui/EmptyState';
import { FilterBar } from '@/shared/ui/FilterBar';
import { FormField } from '@/shared/ui/FormField';
import { PageHeader } from '@/shared/ui/PageHeader';
import { RowActions } from '@/shared/ui/RowActions';
import { SearchInput } from '@/shared/ui/SearchInput';
import { ServerTable } from '@/shared/ui/ServerTable';
import { useToast } from '@/shared/ui/toast';

import {
  useLots,
  useSaveStockSettings,
  useStockSettings,
  type LotStateFilter,
  type StockLot,
} from './api';
import { LotStateTag } from './ui';

const STATES: LotStateFilter[] = ['all', 'expired', 'expiring_soon', 'ok', 'no_expiry'];

/** Seuil « bientôt périmé » du tenant (D16) : réglage commun à toute l'entreprise. */
function ThresholdDialog({ current, onClose }: { current: number; onClose: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const save = useSaveStockSettings();
  const [value, setValue] = useState(String(current));
  const days = Number(value);
  const valid = /^\d{1,3}$/.test(value) && days <= 365;
  return (
    <Dialog header={t('lots.threshold')} visible onHide={onClose} className="sm-dialog">
      <div className="sm-form">
        <FormField
          id="lots-threshold"
          label={t('lots.thresholdDays')}
          help={t('lots.thresholdHelp')}
          required
          error={!valid ? t('lots.thresholdInvalid') : undefined}
        >
          <InputText
            id="lots-threshold"
            inputMode="numeric"
            value={value}
            onChange={(e) => setValue(e.target.value.trim())}
            autoFocus
          />
        </FormField>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button
            label={t('actions.save')}
            disabled={!valid}
            loading={save.isPending}
            onClick={() =>
              save.mutate(days, {
                onSuccess: () => {
                  toast.success(t('lots.thresholdSaved'));
                  onClose();
                },
                onError: (error) => toast.error(translateError(t, error)),
              })
            }
          />
        </div>
      </div>
    </Dialog>
  );
}

/**
 * Lots (Lot 3-G, ADR-0045) : lots ayant un solde sur les sites visibles, état de péremption
 * calculé par le serveur (fuseau et seuil du tenant), échéance la plus proche d'abord. Le
 * référentiel n'est alimenté que par les réceptions validées ; aucun coût par lot.
 */
export default function LotsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { can, capabilities } = useCapabilities();
  const { locale } = capabilities.tenant;
  const [table, setTable] = useState<TableState>({
    ...INITIAL_TABLE,
    sortField: 'expiry_date',
    sortOrder: 1,
  });
  const [search, setSearch] = useState('');
  const [state, setState] = useState<LotStateFilter>('all');
  const [site, setSite] = useState<string | null>(null);
  const [expiresBefore, setExpiresBefore] = useState('');
  const [editingThreshold, setEditingThreshold] = useState(false);
  const debounced = useDebouncedValue(search);
  const lots = useLots(
    toQueryString(table, {
      search: debounced,
      state: state === 'all' ? null : state,
      site_id: site,
      expires_before: expiresBefore || null,
    }),
  );
  const settings = useStockSettings();
  const showSite = capabilities.site === null && capabilities.sites.length > 1;
  const filtered = search !== '' || state !== 'all' || site !== null || expiresBefore !== '';
  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));
  const resetFilters = () => {
    setSearch('');
    setState('all');
    setSite(null);
    setExpiresBefore('');
    resetPage();
  };
  const open = (lot: StockLot) => void navigate(`/stock/lots/${lot.id}`);

  return (
    <>
      <PageHeader title={t('lots.title')} description={t('lots.subtitle')} />
      {settings.data && (
        <p className="sm-help" data-testid="lots-threshold">
          {t('lots.thresholdCurrent', { days: settings.data.expiry_warning_days })}{' '}
          {can('stock.threshold.manage') && (
            <Button
              type="button"
              label={t('lots.thresholdEdit')}
              link
              size="small"
              onClick={() => setEditingThreshold(true)}
            />
          )}
        </p>
      )}
      <FilterBar onReset={resetFilters} active={filtered}>
        <SearchInput
          value={search}
          placeholder={t('lots.search')}
          onChange={(v) => {
            setSearch(v);
            resetPage();
          }}
        />
        <Dropdown
          inputId="lots-state"
          aria-label={t('lots.state')}
          value={state}
          options={STATES.map((s) => ({
            value: s,
            label: s === 'all' ? t('lots.allStates') : t(`lots.states.${s}`),
          }))}
          onChange={(e) => {
            setState(e.value as LotStateFilter);
            resetPage();
          }}
        />
        {showSite && (
          <Dropdown
            inputId="lots-site"
            aria-label={t('layout.site')}
            value={site}
            placeholder={t('layout.allSites')}
            showClear
            options={capabilities.sites.map((s) => ({ value: s.id, label: s.name }))}
            onChange={(e) => {
              setSite((e.value as string | undefined) ?? null);
              resetPage();
            }}
          />
        )}
        <label className="sm-daterange-item">
          <span>{t('lots.expiresBefore')}</span>
          <InputText
            id="lots-expires-before"
            type="date"
            value={expiresBefore}
            onChange={(e) => {
              setExpiresBefore(e.target.value);
              resetPage();
            }}
          />
        </label>
      </FilterBar>
      <ServerTable
        query={lots}
        table={table}
        onTableChange={setTable}
        onRowClick={open}
        minWidth="40rem"
        empty={<ListEmpty filtered={filtered} title={t('lots.empty')} icon="pi pi-box" />}
      >
        <Column field="number" header={t('lots.lotNumber')} sortable />
        <Column
          field="article_reference"
          sortField="article"
          header={t('stock.article')}
          sortable
          body={(l: StockLot) => `${l.article_reference} — ${l.article_designation}`}
        />
        <Column
          field="expiry_date"
          header={t('lots.expiryDate')}
          sortable
          body={(l: StockLot) => (l.expiry_date ? formatDate(l.expiry_date, locale, 'UTC') : '—')}
        />
        <Column header={t('lots.state')} body={(l: StockLot) => <LotStateTag state={l.state} />} />
        <Column
          field="quantity"
          header={t('stock.quantity')}
          sortable
          headerClassName="sm-num"
          bodyClassName="sm-num"
          body={(l: StockLot) => `${formatQuantity(l.quantity, locale)} ${l.unit}`}
        />
        {showSite && (
          <Column
            field="site_count"
            header={t('lots.siteCount')}
            headerClassName="sm-num sm-hide-sm"
            bodyClassName="sm-num sm-hide-sm"
          />
        )}
        <Column
          header={t('common.actions')}
          body={(l: StockLot) => (
            <RowActions
              actions={[
                { key: 'open', label: t('stock.open'), icon: 'pi pi-eye', onClick: () => open(l) },
              ]}
            />
          )}
        />
      </ServerTable>
      {editingThreshold && settings.data && (
        <ThresholdDialog
          current={settings.data.expiry_warning_days}
          onClose={() => setEditingThreshold(false)}
        />
      )}
    </>
  );
}
