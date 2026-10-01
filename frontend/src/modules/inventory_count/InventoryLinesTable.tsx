import { Column } from 'primereact/column';
import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
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
import { EmptyState, ListEmpty } from '@/shared/ui/EmptyState';
import { FilterBar } from '@/shared/ui/FilterBar';
import { FormField } from '@/shared/ui/FormField';
import { RowActions } from '@/shared/ui/RowActions';
import { SearchInput } from '@/shared/ui/SearchInput';
import { ServerTable } from '@/shared/ui/ServerTable';
import { StatusBadge } from '@/shared/ui/StatusBadge';
import { useToast } from '@/shared/ui/toast';
import { COST_VIEW, type ScanResult } from '@/modules/catalog/api';
import { BarcodeScanField } from '@/modules/catalog/BarcodeScanField';

import { QuantityEquivalence } from '@/modules/catalog/PresentationField';
import { toBase } from '@/shared/lib/presentation';

import {
  useInventoryLines,
  useInventoryMutations,
  useSaveCount,
  type CountValue,
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

const BASE_UNIT = 'base';

/** Comptage affiché : « 8 Carton 24 + 5 u = 197 u » ou « 197 u » (Lot 3-C). */
export function countedQuantity(line: InventoryLine, locale = 'fr'): string {
  if (line.quantity_physical === null) return '—';
  const base = `${formatQuantity(line.quantity_physical, locale)} ${line.unit}`;
  if (!line.count_packaging_name || line.count_packaging_quantity == null) return base;
  const loose =
    line.count_unit_quantity && /[1-9]/.test(line.count_unit_quantity)
      ? ` + ${formatQuantity(line.count_unit_quantity, locale)} ${line.unit}`
      : '';
  return `${formatQuantity(line.count_packaging_quantity, locale)} ${line.count_packaging_name}${loose} = ${base}`;
}

/**
 * Saisie d'une quantité physique : enregistrée à la sortie du champ ou avec Entrée (qui passe
 * à la ligne suivante). Aucun dialogue : la saisie reste rapide. Lot 3-C : si l'article a des
 * conditionnements, le comptage peut se faire dans un conditionnement + unités en vrac
 * (8 cartons + 5 bouteilles) ; le serveur calcule la quantité en unité de base.
 */
/** Lot 3-D : présentation identifiée par un scan, à présélectionner (jamais la quantité). */
interface ScanPreset {
  packagingId: string | null;
  nonce: number;
}

function CountCell({
  line,
  inventoryId,
  index,
  preset = null,
}: {
  line: InventoryLine;
  inventoryId: string;
  index: number;
  preset?: ScanPreset | null;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const { capabilities } = useCapabilities();
  const { locale } = capabilities.tenant;
  const save = useSaveCount(inventoryId);
  const packagings = line.packagings ?? [];
  const saved = {
    packaging: line.count_packaging_id ?? BASE_UNIT,
    quantity: line.count_packaging_id
      ? toInput(line.count_packaging_quantity ?? null)
      : toInput(line.quantity_physical),
    loose: line.count_packaging_id ? toInput(line.count_unit_quantity ?? null) : '',
  };
  const savedKey = JSON.stringify(saved);
  const [value, setValue] = useState(saved);
  const [invalid, setInvalid] = useState(false);
  // Valeur enregistrée modifiée côté serveur : la saisie affichée la reprend.
  const [previous, setPrevious] = useState(savedKey);
  if (savedKey !== previous) {
    setPrevious(savedKey);
    setValue(saved);
  }
  // Lot 3-D : scan → présentation présélectionnée ; si elle change, la saisie repart à vide
  // (la quantité comptée n'est jamais devinée), puis le champ de quantité prend le focus.
  const [appliedScan, setAppliedScan] = useState<number | null>(null);
  if (preset && preset.nonce !== appliedScan) {
    setAppliedScan(preset.nonce);
    const target = preset.packagingId ?? BASE_UNIT;
    if (target !== value.packaging) setValue({ packaging: target, quantity: '', loose: '' });
  }
  const quantityRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (preset) quantityRef.current?.focus();
  }, [preset]);
  const errorId = `count-error-${line.id}`;
  const packaging =
    value.packaging === BASE_UNIT
      ? null
      : (packagings.find((p) => p.id === value.packaging) ??
        (line.count_packaging_id === value.packaging && line.count_packaging_name
          ? {
              id: line.count_packaging_id,
              name: line.count_packaging_name,
              conversion: line.count_packaging_conversion ?? '1',
            }
          : null));

  const commit = (next = value) => {
    if (JSON.stringify(next) === savedKey) return setInvalid(false);
    const raw = next.quantity.trim();
    const quantity = raw === '' ? null : normalizeDecimal(raw, 3);
    if (raw !== '' && quantity === null) return setInvalid(true);
    let count: CountValue = { quantity_physical: quantity };
    if (next.packaging !== BASE_UNIT && quantity !== null) {
      const loose = next.loose.trim() === '' ? '0' : normalizeDecimal(next.loose, 3);
      if (loose === null) return setInvalid(true);
      count = { packaging_id: next.packaging, packaging_quantity: quantity, unit_quantity: loose };
    }
    setInvalid(false);
    save.mutate(
      { lineId: line.id, count },
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
  const base = (() => {
    const q = normalizeDecimal(value.quantity, 3);
    if (q === null) return null;
    if (!packaging) return q;
    const packed = toBase(q, packaging.conversion);
    const loose = value.loose.trim() === '' ? '0' : normalizeDecimal(value.loose, 3);
    return packed === null || loose === null ? null : addQuantities(packed, loose);
  })();

  return (
    <div className="sm-count-cell">
      {packagings.length > 0 && (
        <Dropdown
          value={value.packaging}
          options={[
            { value: BASE_UNIT, label: t('presentation.baseUnit', { unit: line.unit }) },
            ...packagings.map((p) => ({
              value: p.id,
              label: `${p.name} (${formatQuantity(p.conversion, locale)} ${line.unit})`,
            })),
          ]}
          aria-label={t('inventories.presentationFor', { reference: line.reference })}
          onChange={(e) => setValue({ ...value, packaging: e.value as string, loose: '' })}
        />
      )}
      <InputText
        ref={quantityRef}
        id={`count-${line.id}`}
        data-count-index={index}
        inputMode="decimal"
        value={value.quantity}
        onChange={(e) => setValue({ ...value, quantity: e.target.value })}
        onBlur={() => commit()}
        onKeyDown={onKeyDown}
        invalid={invalid}
        aria-invalid={invalid}
        aria-describedby={invalid ? errorId : undefined}
        aria-label={
          packaging
            ? t('inventories.packagingsFor', { reference: line.reference, name: packaging.name })
            : t('inventories.physicalFor', { reference: line.reference })
        }
        className="sm-count-input"
      />
      {packaging && (
        <InputText
          inputMode="decimal"
          value={value.loose}
          placeholder={`+ ${line.unit}`}
          onChange={(e) => setValue({ ...value, loose: e.target.value })}
          onBlur={() => commit()}
          aria-label={t('inventories.looseFor', { reference: line.reference, unit: line.unit })}
          className="sm-count-input"
        />
      )}
      {save.isPending && (
        <i className="pi pi-spin pi-spinner" role="status" aria-label={t('inventories.saving')} />
      )}
      {packaging && base !== null && /[1-9]/.test(base) ? (
        <small className="sm-equivalence" data-testid={`count-equivalence-${line.id}`}>
          {`= ${formatQuantity(base, locale)} ${line.unit}`}
        </small>
      ) : (
        base !== null && (
          <QuantityEquivalence
            quantity={base}
            unit={line.unit}
            packaging={null}
            packagings={packagings}
            testId={`count-equivalence-${line.id}`}
          />
        )
      )}
      {invalid && (
        <small className="p-error" id={errorId} role="alert">
          {t('articles.invalidQuantity')}
        </small>
      )}
    </div>
  );
}

/** Somme exacte de deux quantités à 3 décimales (affichage indicatif). */
function addQuantities(a: string, b: string): string {
  const scale = (v: string) => {
    const [whole = '0', fraction = ''] = v.split('.');
    return BigInt(`${whole}${fraction.padEnd(3, '0').slice(0, 3)}`);
  };
  const total = (scale(a) + scale(b)).toString().padStart(4, '0');
  return `${total.slice(0, -3)}.${total.slice(-3)}`;
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
  // Lot 3-D : article identifié par un scan (ligne exacte) et présentation à présélectionner.
  const [scanned, setScanned] = useState<{
    articleId: string;
    label: string;
    preset: ScanPreset;
  } | null>(null);
  const debounced = useDebouncedValue(search);
  const lines = useInventoryLines(
    inventory.id,
    toQueryString(table, {
      search: debounced,
      state: state === 'all' ? null : state,
      article_id: scanned?.articleId ?? null,
    }),
  );
  const { currency, locale } = capabilities.tenant;
  // Coûts internes : affichés seulement avec cost_view (absents des réponses sinon).
  const costs = can(COST_VIEW);
  const { status } = inventory;
  const draft = status === 'DRAFT';
  const counting = status === 'COUNTING' && can(`${P}.count`);
  const review = status === 'COUNTING' || status === 'READY_TO_VALIDATE';
  const final = status === 'VALIDATED';
  const editArticles = draft && inventory.inventory_type === 'TARGETED' && can(`${P}.update`);

  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));
  const filtered = search !== '' || state !== 'all' || scanned !== null;
  const onScan = (scan: ScanResult) => {
    setScanned({
      articleId: scan.article.id,
      label: `${scan.article.reference} — ${scan.packaging?.name ?? scan.article.unit}`,
      preset: { packagingId: scan.packaging?.id ?? null, nonce: Date.now() },
    });
    resetPage();
  };
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
      {counting && <BarcodeScanField id="inventory-scan" onScan={onScan} />}
      <FilterBar
        onReset={() => {
          setSearch('');
          setState('all');
          setScanned(null);
          resetPage();
        }}
        active={filtered}
      >
        {scanned && (
          <StatusBadge
            tone="info"
            label={t('inventories.scannedFilter', { label: scanned.label })}
          />
        )}
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
            scanned ? (
              <EmptyState
                icon="pi pi-barcode"
                title={t('inventories.scannedAbsent', { label: scanned.label })}
              />
            ) : (
              <ListEmpty filtered={filtered} icon="pi pi-box" title={t('inventories.noLines')} />
            )
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
                if (!counting) return countedQuantity(l, locale);
                const index = (lines.data?.items ?? []).findIndex((x) => x.id === l.id);
                return (
                  <CountCell
                    line={l}
                    inventoryId={inventory.id}
                    index={index}
                    preset={scanned?.articleId === l.article_id ? scanned.preset : null}
                  />
                );
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
          {final && costs && (
            <Column
              header={t('stock.averageCost')}
              {...num}
              body={(l: InventoryLine) => formatCost(l.unit_cost, currency, locale)}
            />
          )}
          {final && costs && (
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
