import { Button } from 'primereact/button';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatQuantity, normalizeDecimal } from '@/shared/lib/decimal';
import { translateError } from '@/shared/lib/errors';
import { FormField } from '@/shared/ui/FormField';
import { StatusBadge } from '@/shared/ui/StatusBadge';
import { useToast } from '@/shared/ui/toast';
import { LotLabel } from '@/modules/stock/ui';

import {
  useLotMutations,
  type CountValue,
  type DiscoveredLotInput,
  type Inventory,
  type InventoryLine,
  type InventoryLot,
} from './api';
import { VarianceBadge } from './ui';

const BASE_UNIT = 'base';

interface LotEntry {
  packaging: string;
  quantity: string;
  loose: string;
}

/** « 95.000 » → « 95 » pour la saisie (le serveur fait foi, aucune arithmétique). */
function toInput(value: string | null | undefined): string {
  if (value == null) return '';
  return value.includes('.') ? value.replace(/\.?0+$/, '') : value;
}

function entryOf(lot: InventoryLot): LotEntry {
  return {
    packaging: lot.count_packaging_id ?? BASE_UNIT,
    quantity: lot.count_packaging_id
      ? toInput(lot.count_packaging_quantity)
      : toInput(lot.quantity_physical),
    loose: lot.count_packaging_id ? toInput(lot.count_unit_quantity) : '',
  };
}

/** Saisie → comptage envoyé au serveur (null : non saisi = 0 à la validation) ; `undefined`
 *  si la saisie est invalide. */
function countOf(entry: LotEntry): CountValue | null | undefined {
  const raw = entry.quantity.trim();
  if (raw === '') return null;
  const quantity = normalizeDecimal(raw, 3);
  if (quantity === null) return undefined;
  if (entry.packaging === BASE_UNIT) return { quantity_physical: quantity };
  const loose = entry.loose.trim() === '' ? '0' : normalizeDecimal(entry.loose, 3);
  if (loose === null) return undefined;
  return { packaging_id: entry.packaging, packaging_quantity: quantity, unit_quantity: loose };
}

/** Quantité comptée d'un lot : « 8 Carton 24 + 5 u = 197 u » ou « 197 u ». */
function countedLot(lot: InventoryLot, unit: string, locale: string): string {
  if (lot.quantity_physical === null) return '—';
  const base = `${formatQuantity(lot.quantity_physical, locale)} ${unit}`;
  if (!lot.count_packaging_name || lot.count_packaging_quantity == null) return base;
  const loose =
    lot.count_unit_quantity && /[1-9]/.test(lot.count_unit_quantity)
      ? ` + ${formatQuantity(lot.count_unit_quantity, locale)} ${unit}`
      : '';
  return `${formatQuantity(lot.count_packaging_quantity, locale)} ${lot.count_packaging_name}${loose} = ${base}`;
}

/**
 * Lots d'une ligne suivie par lot (Lot 3-H) : un bloc par lot — numéro, péremption et état,
 * théorique au démarrage, stock actuel, quantité physique (unité de base, ou conditionnement +
 * vrac), écart. Un lot attendu non saisi compte 0 à la validation ; « Ajouter un lot
 * découvert » pour un lot trouvé physiquement. Tout est recalculé et revérifié par le serveur ;
 * le comptage de la ligne est envoyé complet à chaque modification (remplacement).
 */
export function InventoryLotsPanel({
  inventory,
  line,
  editable,
}: {
  inventory: Inventory;
  line: InventoryLine;
  editable: boolean;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const { capabilities } = useCapabilities();
  const { locale } = capabilities.tenant;
  const { save, remove } = useLotMutations(inventory.id);
  const lots = line.lots ?? [];
  const packagings = line.packagings ?? [];
  const serverEntries = Object.fromEntries(lots.map((lot) => [lot.id, entryOf(lot)]));
  const serverKey = JSON.stringify(serverEntries);
  const [entries, setEntries] = useState<Record<string, LotEntry>>(serverEntries);
  const [invalid, setInvalid] = useState<Record<string, boolean>>({});
  const [discovering, setDiscovering] = useState(false);
  // Réponse du serveur : seuls les lots dont la valeur enregistrée a changé (ou nouveaux) sont
  // repris ; une saisie en cours sur un autre lot est conservée.
  const [previous, setPrevious] = useState(serverKey);
  if (serverKey !== previous) {
    const before = JSON.parse(previous) as Record<string, LotEntry>;
    setPrevious(serverKey);
    setEntries((local) =>
      Object.fromEntries(
        lots.map((lot) => {
          const server = serverEntries[lot.id] as LotEntry;
          const old = before[lot.id];
          const keep = old && JSON.stringify(old) === JSON.stringify(server) && local[lot.id];
          return [lot.id, keep ? (local[lot.id] as LotEntry) : server];
        }),
      ),
    );
  }
  const qty = (value: string | null) =>
    value === null ? '—' : `${formatQuantity(value, locale)} ${line.unit}`;

  // Le comptage de la ligne est remplacé en entier : tous les lots sont envoyés.
  const commit = () => {
    const counts = [];
    const errors: Record<string, boolean> = {};
    for (const lot of lots) {
      const count = countOf(entries[lot.id] ?? entryOf(lot));
      if (count === undefined) errors[lot.id] = true;
      else if (count !== null) counts.push({ lot_row_id: lot.id, ...count });
    }
    setInvalid(errors);
    if (Object.keys(errors).length > 0) return;
    if (JSON.stringify(entries) === serverKey) return;
    save.mutate(
      { lineId: line.id, counts },
      { onError: (error) => toast.error(translateError(t, error)) },
    );
  };

  return (
    <section
      className="sm-lot-count"
      aria-label={t('inventoryLots.title', { reference: line.reference })}
    >
      {lots.length === 0 ? (
        <p className="sm-muted">{t('inventoryLots.none')}</p>
      ) : (
        <ul className="sm-lot-count-list">
          {lots.map((lot) => {
            const entry = entries[lot.id] ?? entryOf(lot);
            const packaging = packagings.find((p) => p.id === entry.packaging) ?? null;
            const inputId = `lot-count-${lot.id}`;
            return (
              <li
                key={lot.id}
                className="sm-lot-count-row"
                data-testid={`lot-row-${lot.lot_number}`}
              >
                <div className="sm-cell-stack">
                  <LotLabel
                    number={lot.lot_number}
                    expiry={lot.expiry_date}
                    state={lot.state}
                    locale={locale}
                  />
                  {lot.discovered && (
                    <StatusBadge tone="info" label={t('inventoryLots.discovered')} />
                  )}
                  <span className="sm-muted">
                    {t('inventoryLots.theoretical', {
                      quantity: qty(lot.stock_theoretical_initial),
                    })}
                  </span>
                  {lot.stock_current !== null &&
                    lot.stock_current !== lot.stock_theoretical_initial && (
                      <span className="sm-help">
                        {t('inventoryLots.current', { quantity: qty(lot.stock_current) })}
                      </span>
                    )}
                  {lot.stock_theoretical_at_validation !== null && (
                    <span className="sm-muted">
                      {t('inventoryLots.atValidation', {
                        quantity: qty(lot.stock_theoretical_at_validation),
                      })}
                    </span>
                  )}
                </div>
                <div className="sm-lot-count-input">
                  {editable ? (
                    <>
                      {packagings.length > 0 && (
                        <Dropdown
                          value={entry.packaging}
                          options={[
                            {
                              value: BASE_UNIT,
                              label: t('presentation.baseUnit', { unit: line.unit }),
                            },
                            ...packagings.map((p) => ({
                              value: p.id,
                              label: `${p.name} (${formatQuantity(p.conversion, locale)} ${line.unit})`,
                            })),
                          ]}
                          aria-label={t('inventoryLots.presentationFor', {
                            number: lot.lot_number,
                          })}
                          onChange={(e) =>
                            setEntries((s) => ({
                              ...s,
                              [lot.id]: { ...entry, packaging: e.value as string, loose: '' },
                            }))
                          }
                        />
                      )}
                      <label htmlFor={inputId} className="sm-lot-allocation-input">
                        <span>
                          {packaging
                            ? t('inventoryLots.packagingsFor', {
                                number: lot.lot_number,
                                name: packaging.name,
                              })
                            : t('inventoryLots.physicalFor', { number: lot.lot_number })}
                        </span>
                        <InputText
                          id={inputId}
                          inputMode="decimal"
                          value={entry.quantity}
                          invalid={invalid[lot.id]}
                          aria-invalid={invalid[lot.id] ?? false}
                          onChange={(e) =>
                            setEntries((s) => ({
                              ...s,
                              [lot.id]: { ...entry, quantity: e.target.value },
                            }))
                          }
                          onBlur={commit}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') {
                              e.preventDefault();
                              commit();
                            }
                          }}
                        />
                      </label>
                      {packaging && (
                        <InputText
                          inputMode="decimal"
                          value={entry.loose}
                          placeholder={`+ ${line.unit}`}
                          aria-label={t('inventoryLots.looseFor', {
                            number: lot.lot_number,
                            unit: line.unit,
                          })}
                          onChange={(e) =>
                            setEntries((s) => ({
                              ...s,
                              [lot.id]: { ...entry, loose: e.target.value },
                            }))
                          }
                          onBlur={commit}
                        />
                      )}
                      {invalid[lot.id] && (
                        <small className="p-error" role="alert">
                          {t('articles.invalidQuantity')}
                        </small>
                      )}
                    </>
                  ) : (
                    <span>{countedLot(lot, line.unit, locale)}</span>
                  )}
                </div>
                <div className="sm-lot-count-variance">
                  <VarianceBadge value={lot.quantity_variance} locale={locale} />
                  {editable && lot.discovered && (
                    <Button
                      type="button"
                      icon="pi pi-trash"
                      text
                      severity="danger"
                      aria-label={t('inventoryLots.removeDiscovered', { number: lot.lot_number })}
                      loading={remove.isPending}
                      onClick={() =>
                        remove.mutate(
                          { lineId: line.id, rowId: lot.id },
                          { onError: (error) => toast.error(translateError(t, error)) },
                        )
                      }
                    />
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {editable && (
        <div className="sm-lot-count-actions">
          <Button
            type="button"
            icon="pi pi-plus"
            outlined
            label={t('inventoryLots.discover')}
            onClick={() => setDiscovering(true)}
          />
          {lots.length > 0 && line.quantity_physical === null && (
            <small className="sm-help">{t('inventoryLots.zeroHint')}</small>
          )}
        </div>
      )}
      {discovering && (
        <DiscoverLotDialog
          inventoryId={inventory.id}
          line={line}
          onClose={() => setDiscovering(false)}
        />
      )}
    </section>
  );
}

/** Lot trouvé physiquement : numéro, péremption, fabrication, quantité. Plein écran sur mobile.
 *  Règles 3-G contrôlées par le serveur (numéro, péremption si l'article la suit, fabrication
 *  ≤ péremption, lot connu avec SA péremption, aucun doublon). */
function DiscoverLotDialog({
  inventoryId,
  line,
  onClose,
}: {
  inventoryId: string;
  line: InventoryLine;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const { discover } = useLotMutations(inventoryId);
  const [form, setForm] = useState({ number: '', expiry: '', made: '', quantity: '' });
  const [error, setError] = useState<string | null>(null);
  const quantity = form.quantity.trim() === '' ? null : normalizeDecimal(form.quantity, 3);
  const ready = form.number.trim() !== '' && (form.quantity.trim() === '' || quantity !== null);
  const submit = () => {
    const input: DiscoveredLotInput = {
      lot_number: form.number.trim(),
      expiry_date: form.expiry || null,
      manufacturing_date: form.made || null,
      quantity_physical: quantity,
    };
    discover.mutate(
      { lineId: line.id, input },
      {
        onSuccess: () => {
          toast.success(t('inventoryLots.discoveredAdded', { number: input.lot_number }));
          onClose();
        },
        onError: (e) => setError(translateError(t, e)),
      },
    );
  };
  return (
    <Dialog
      header={t('inventoryLots.discoverTitle', { reference: line.reference })}
      visible
      onHide={onClose}
      className="sm-dialog sm-dialog-full-sm"
      breakpoints={{ '640px': '100vw' }}
    >
      <div className="sm-form">
        <FormField id="discover-number" label={t('lots.lotNumber')} required>
          <InputText
            id="discover-number"
            maxLength={50}
            value={form.number}
            onChange={(e) => setForm({ ...form, number: e.target.value })}
            autoFocus
          />
        </FormField>
        <FormField
          id="discover-expiry"
          label={t('lots.expiryDate')}
          help={t('inventoryLots.expiryHelp')}
        >
          <InputText
            id="discover-expiry"
            type="date"
            value={form.expiry}
            onChange={(e) => setForm({ ...form, expiry: e.target.value })}
          />
        </FormField>
        <FormField id="discover-made" label={t('lots.manufacturingDate')}>
          <InputText
            id="discover-made"
            type="date"
            value={form.made}
            onChange={(e) => setForm({ ...form, made: e.target.value })}
          />
        </FormField>
        <FormField
          id="discover-quantity"
          label={t('inventoryLots.discoverQuantity', { unit: line.unit })}
          error={
            form.quantity.trim() !== '' && quantity === null
              ? t('articles.invalidQuantity')
              : undefined
          }
        >
          <InputText
            id="discover-quantity"
            inputMode="decimal"
            value={form.quantity}
            onChange={(e) => setForm({ ...form, quantity: e.target.value })}
          />
        </FormField>
        {error && (
          <small className="p-error" role="alert">
            {error}
          </small>
        )}
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.back')} text onClick={onClose} />
          <Button
            type="button"
            icon="pi pi-check"
            label={t('inventoryLots.discoverSubmit')}
            disabled={!ready}
            loading={discover.isPending}
            onClick={submit}
          />
        </div>
      </div>
    </Dialog>
  );
}
