import { Column } from 'primereact/column';
import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { useState, type KeyboardEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatCost, formatMoney, formatQuantity, normalizeDecimal } from '@/shared/lib/decimal';
import { translateError } from '@/shared/lib/errors';
import {
  INITIAL_TABLE,
  toQueryString,
  useDebouncedValue,
  type TableState,
} from '@/shared/lib/serverTable';
import { ListEmpty } from '@/shared/ui/EmptyState';
import { FilterBar } from '@/shared/ui/FilterBar';
import { FormField } from '@/shared/ui/FormField';
import { RowActions } from '@/shared/ui/RowActions';
import { SearchInput } from '@/shared/ui/SearchInput';
import { ServerTable } from '@/shared/ui/ServerTable';
import { StatusBadge } from '@/shared/ui/StatusBadge';
import { useToast } from '@/shared/ui/toast';

import {
  useInventoryLines,
  useInventoryMutations,
  useSaveCount,
  type Inventory,
  type InventoryLine,
  type LineState,
} from './api';
import { CandidatePicker } from './CandidatePicker';
import { VarianceBadge } from './ui';

const P = 'inventory_count.inventory';
const STATES: LineState[] = ['all', 'counted', 'uncounted', 'surplus', 'shortage', 'no_variance'];

/** « 95.000 » → « 95 » pour la saisie (le serveur fait foi, aucune arithmétique). */
function toInput(value: string | null): string {
  if (value === null) return '';
  return value.includes('.') ? value.replace(/\.?0+$/, '') : value;
}

/**
 * Saisie d'une quantité physique : enregistrée à la sortie du champ ou avec Entrée (qui passe
 * à la ligne suivante). Aucun dialogue : la saisie reste rapide.
 */
function CountCell({
  line,
  inventoryId,
  index,
}: {
  line: InventoryLine;
  inventoryId: string;
  index: number;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const save = useSaveCount(inventoryId);
  const saved = toInput(line.quantity_physical);
  const [value, setValue] = useState(saved);
  const [invalid, setInvalid] = useState(false);
  // Valeur enregistrée modifiée côté serveur : la saisie affichée la reprend.
  const [previous, setPrevious] = useState(saved);
  if (saved !== previous) {
    setPrevious(saved);
    setValue(saved);
  }
  const errorId = `count-error-${line.id}`;

  const commit = () => {
    const raw = value.trim();
    if (raw === saved) return setInvalid(false);
    const quantity = raw === '' ? null : normalizeDecimal(raw, 3);
    if (raw !== '' && quantity === null) return setInvalid(true);
    setInvalid(false);
    save.mutate(
      { lineId: line.id, quantity },
      { onError: (error) => toast.error(translateError(t, error)) },
    );
  };
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    commit();
    const next = document.querySelector<HTMLInputElement>(`[data-count-index="${index + 1}"]`);
    next?.focus();
    next?.select();
  };

  return (
    <div className="sm-count-cell">
      <InputText
        id={`count-${line.id}`}
        data-count-index={index}
        inputMode="decimal"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onBlur={commit}
        onKeyDown={onKeyDown}
        invalid={invalid}
        aria-invalid={invalid}
        aria-describedby={invalid ? errorId : undefined}
        aria-label={t('inventories.physicalFor', { reference: line.reference })}
        className="sm-count-input"
      />
      {save.isPending && (
        <i className="pi pi-spin pi-spinner" role="status" aria-label={t('inventories.saving')} />
      )}
      {invalid && (
        <small className="p-error" id={errorId} role="alert">
          {t('articles.invalidQuantity')}
        </small>
      )}
    </div>
  );
}

/** Lignes d'un inventaire (pagination, recherche et filtres serveur) selon son statut. */
export function InventoryLinesTable({ inventory }: { inventory: Inventory }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { can, capabilities } = useCapabilities();
  const { update } = useInventoryMutations();
  const [table, setTable] = useState<TableState>({
    ...INITIAL_TABLE,
    rows: 50,
    sortField: 'reference',
    sortOrder: 1,
  });
  const [search, setSearch] = useState('');
  const [state, setState] = useState<LineState>('all');
  const debounced = useDebouncedValue(search);
  const lines = useInventoryLines(
    inventory.id,
    toQueryString(table, { search: debounced, state: state === 'all' ? null : state }),
  );
  const { currency, locale } = capabilities.tenant;
  const { status } = inventory;
  const draft = status === 'DRAFT';
  const counting = status === 'COUNTING' && can(`${P}.count`);
  const review = status === 'COUNTING' || status === 'READY_TO_VALIDATE';
  const final = status === 'VALIDATED';
  const editArticles = draft && inventory.inventory_type === 'TARGETED' && can(`${P}.update`);

  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));
  const filtered = search !== '' || state !== 'all';
  const changeArticles = (input: { add_article_ids?: string[]; remove_article_ids?: string[] }) =>
    update.mutate(
      { id: inventory.id, input: { comment: inventory.comment, ...input } },
      { onError: (error) => toast.error(translateError(t, error)) },
    );
  const qty = (value: string | null, unit: string) =>
    value === null ? '—' : `${formatQuantity(value, locale)} ${unit}`;
  const num = { headerClassName: 'sm-num', bodyClassName: 'sm-num' };

  return (
    <section className="sm-block" aria-label={t('inventories.lines')}>
      {editArticles && (
        <FormField id="draft-article" label={t('inventories.addArticle')}>
          <CandidatePicker
            id="draft-article"
            siteId={inventory.site_id}
            exclude={new Set((lines.data?.items ?? []).map((l) => l.article_id))}
            onSelect={(c) => changeArticles({ add_article_ids: [c.article_id] })}
          />
        </FormField>
      )}
      <FilterBar
        onReset={() => {
          setSearch('');
          setState('all');
          resetPage();
        }}
        active={filtered}
      >
        <SearchInput
          value={search}
          placeholder={t('inventories.lineSearch')}
          onChange={(v) => {
            setSearch(v);
            resetPage();
          }}
        />
        {!draft && (
          <Dropdown
            value={state}
            onChange={(e) => {
              setState(e.value as LineState);
              resetPage();
            }}
            options={STATES.map((v) => ({ value: v, label: t(`inventories.lineStates.${v}`) }))}
            aria-label={t('inventories.lineState')}
          />
        )}
      </FilterBar>
      <div className={counting ? 'sm-count-table' : undefined}>
        <ServerTable
          query={lines}
          table={table}
          onTableChange={setTable}
          // Comptage : saisie visible sans défilement horizontal, même sur mobile.
          minWidth={final ? '60rem' : counting ? '0' : '36rem'}
          empty={
            <ListEmpty filtered={filtered} icon="pi pi-box" title={t('inventories.noLines')} />
          }
        >
          <Column
            field="reference"
            header={t('stock.article')}
            sortable
            body={(l: InventoryLine) => (
              <div>
                <div className="sm-strong">{l.reference}</div>
                <div className="sm-muted">{l.designation}</div>
                {!l.article_active && <StatusBadge tone="neutral" label={t('common.inactive')} />}
              </div>
            )}
          />
          <Column
            header={t('inventories.stockTheoretical')}
            {...num}
            body={(l: InventoryLine) => (
              <div>
                <div>{qty(l.stock_theoretical_initial, l.unit)}</div>
                {review &&
                  l.stock_current !== null &&
                  l.stock_current !== l.stock_theoretical_initial && (
                    <small className="sm-help">
                      {t('inventories.currentStock', {
                        quantity: formatQuantity(l.stock_current, locale),
                      })}
                    </small>
                  )}
              </div>
            )}
          />
          {final && (
            <Column
              header={t('inventories.stockAtValidation')}
              {...num}
              body={(l: InventoryLine) => qty(l.stock_theoretical_at_validation, l.unit)}
            />
          )}
          {!draft && (
            <Column
              header={t('inventories.physical')}
              {...num}
              body={(l: InventoryLine) => {
                if (!counting) return qty(l.quantity_physical, l.unit);
                const index = (lines.data?.items ?? []).findIndex((x) => x.id === l.id);
                return <CountCell line={l} inventoryId={inventory.id} index={index} />;
              }}
            />
          )}
          {!draft && status !== 'CANCELLED' && (
            <Column
              header={t('inventories.variance.title')}
              field="variance"
              sortable
              // Pendant la saisie sur petit écran : écart masqué (revu à l'étape « À valider »).
              headerClassName={counting ? 'sm-hide-sm' : undefined}
              bodyClassName={counting ? 'sm-hide-sm' : undefined}
              body={(l: InventoryLine) => (
                <div>
                  <VarianceBadge
                    value={final ? l.quantity_variance : l.indicative_variance}
                    locale={locale}
                  />
                  {review &&
                    l.quantity_variance !== null &&
                    l.quantity_variance !== l.indicative_variance && (
                      <div>
                        <small className="sm-help">
                          {t('inventories.appliedVariance', {
                            quantity: formatQuantity(l.quantity_variance, locale),
                          })}
                        </small>
                      </div>
                    )}
                </div>
              )}
            />
          )}
          {final && (
            <Column
              header={t('stock.averageCost')}
              {...num}
              body={(l: InventoryLine) => formatCost(l.unit_cost, currency, locale)}
            />
          )}
          {final && (
            <Column
              header={t('inventories.adjustmentValue')}
              {...num}
              body={(l: InventoryLine) => formatMoney(l.adjustment_value, currency, locale)}
            />
          )}
          {editArticles && (
            <Column
              header={t('common.actions')}
              body={(l: InventoryLine) => (
                <RowActions
                  actions={[
                    {
                      key: 'remove',
                      label: t('inventories.removeArticle'),
                      icon: 'pi pi-times',
                      danger: true,
                      onClick: () => changeArticles({ remove_article_ids: [l.article_id] }),
                    },
                  ]}
                />
              )}
            />
          )}
        </ServerTable>
      </div>
    </section>
  );
}
