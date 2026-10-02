import { InputText } from 'primereact/inputtext';
import { useTranslation } from 'react-i18next';

import {
  addQuantity,
  compareQuantity,
  formatQuantity,
  normalizeDecimal,
  subtractQuantity,
} from '@/shared/lib/decimal';
import { formatDate } from '@/shared/lib/format';
import { LoadingState } from '@/shared/ui/LoadingState';
import { StatusBadge } from '@/shared/ui/StatusBadge';

import { exitLotsPath, useAvailableLots, type LineLot } from './api';
import { LotStateTag } from './ui';

/** Choix saisi (quantité en unité de base, chaîne telle que tapée). */
export interface LotChoice {
  lot_id: string;
  quantity: string;
}

/** Somme des choix valides (indicative : le serveur recalcule tout). */
export function allocatedTotal(choices: LotChoice[]): string {
  return choices.reduce((sum, c) => {
    const n = normalizeDecimal(c.quantity, 3);
    return n === null ? sum : addQuantity(sum, n);
  }, '0');
}

/**
 * Répartition MANUELLE d'une ligne sur les lots — sorties (Lot 3-H-A, H-D1, O-4) et transferts
 * (Lot 3-H-B1) : lots ayant un solde sur le site (numéro, quantité disponible, péremption, état),
 * plusieurs lots possibles, quantités en unité de base. « Demandé / Réparti / Reste » est
 * indicatif : le brouillon peut rester incomplet, le serveur exige la somme exacte à la
 * validation et revérifie lots, soldes, site et quantités. Rien n'est affiché pour un article
 * non suivi par lot. `lotsPath` : point d'accès propre à l'usage (permissions distinctes) ;
 * `blockExpired` (transferts, D-1) : un lot périmé est affiché avec son état mais n'est pas
 * sélectionnable (le serveur le refuse de toute façon).
 */
export function LotAllocationEditor({
  id,
  articleId,
  siteId,
  unit,
  requested,
  value,
  known = [],
  locale,
  onChange,
  lotsPath = exitLotsPath,
  blockExpired = false,
  help,
}: {
  id: string;
  articleId: string;
  siteId: string | null;
  unit: string;
  /** Quantité de la ligne en unité de base (indicative), nulle si non calculable. */
  requested: string | null;
  value: LotChoice[];
  /** Lots déjà enregistrés sur la ligne (numéro d'un lot sans solde restant). */
  known?: LineLot[];
  locale: string;
  onChange: (value: LotChoice[]) => void;
  lotsPath?: (articleId: string, siteId: string) => string;
  blockExpired?: boolean;
  /** Aide affichée sous l'éditeur (défaut : celle des sorties). */
  help?: string;
}) {
  const { t } = useTranslation();
  const query = useAvailableLots(siteId ? lotsPath(articleId, siteId) : null);
  if (query.isPending && siteId) return <LoadingState />;
  const data = query.data;
  if (!data?.lot_tracked && value.length === 0) return null;
  const available = data?.lots ?? [];
  // Lots choisis sans solde restant sur le site : toujours affichés (à retirer ou corriger).
  const missing = value
    .filter((c) => !available.some((l) => l.lot_id === c.lot_id))
    .map((c) => {
      const lot = known.find((k) => k.lot_id === c.lot_id);
      return {
        lot_id: c.lot_id,
        number: lot?.lot_number ?? '?',
        quantity: '0',
        expiry_date: lot?.expiry_date ?? null,
        state: lot?.state ?? null,
        expired: false,
      };
    });
  const rows = [...available, ...missing];
  const allocated = allocatedTotal(value);
  const rest = requested === null ? null : subtractQuantity(requested, allocated);
  const complete = rest !== null && compareQuantity(rest, '0') === 0;
  const quantityOf = (lotId: string) => value.find((c) => c.lot_id === lotId)?.quantity ?? '';
  const setQuantity = (lotId: string, quantity: string) => {
    const others = value.filter((c) => c.lot_id !== lotId);
    const next = quantity.trim() === '' ? others : [...others, { lot_id: lotId, quantity }];
    // Ordre d'affichage conservé (ordre du serveur) pour un envoi stable.
    const order = rows.map((r) => r.lot_id);
    onChange(next.sort((a, b) => order.indexOf(a.lot_id) - order.indexOf(b.lot_id)));
  };
  const q = (v: string) => `${formatQuantity(v, locale)} ${unit}`;

  return (
    <fieldset className="sm-lot-allocation" aria-labelledby={`${id}-title`}>
      <legend id={`${id}-title`}>{t('lotAllocation.title')}</legend>
      <p className="sm-lot-allocation-summary" aria-live="polite" data-testid={`${id}-summary`}>
        <span>{t('lotAllocation.requested', { quantity: requested ? q(requested) : '—' })}</span>
        <span>{t('lotAllocation.allocated', { quantity: q(allocated) })}</span>
        {rest !== null && <span>{t('lotAllocation.rest', { quantity: q(rest) })}</span>}
        <StatusBadge
          tone={complete ? 'success' : 'warning'}
          label={t(complete ? 'lotAllocation.complete' : 'lotAllocation.incomplete')}
        />
      </p>
      {rows.length === 0 ? (
        <p className="sm-muted">{t('lotAllocation.noLot')}</p>
      ) : (
        <ul className="sm-lot-allocation-list">
          {rows.map((lot) => {
            const inputId = `${id}-${lot.lot_id}`;
            const typed = quantityOf(lot.lot_id);
            const invalid = typed !== '' && normalizeDecimal(typed, 3) === null;
            // Lot périmé non transférable : saisie bloquée (une valeur déjà saisie reste
            // modifiable pour pouvoir la retirer).
            const blocked = blockExpired && lot.expired && typed === '';
            return (
              <li key={lot.lot_id} className="sm-lot-allocation-row">
                <div className="sm-cell-stack">
                  <strong>{t('lots.lotNumberShort', { number: lot.number })}</strong>
                  <span className="sm-muted">
                    {lot.expiry_date
                      ? t('lots.expiresOn', { date: formatDate(lot.expiry_date, locale, 'UTC') })
                      : t('lotAllocation.noExpiry')}
                  </span>
                  <span className="sm-muted">
                    {t('lotAllocation.available', { quantity: q(lot.quantity) })}
                  </span>
                  {lot.state && lot.state !== 'no_expiry' && <LotStateTag state={lot.state} />}
                  {blockExpired && lot.expired && (
                    <span className="p-error">{t('lotAllocation.expiredNotTransferable')}</span>
                  )}
                </div>
                <label htmlFor={inputId} className="sm-lot-allocation-input">
                  <span>{t('lotAllocation.quantity', { number: lot.number })}</span>
                  <InputText
                    id={inputId}
                    inputMode="decimal"
                    value={typed}
                    invalid={invalid}
                    aria-invalid={invalid}
                    disabled={blocked}
                    onChange={(e) => setQuantity(lot.lot_id, e.target.value)}
                  />
                </label>
              </li>
            );
          })}
        </ul>
      )}
      <small className="sm-help">{help ?? t('lotAllocation.help')}</small>
    </fieldset>
  );
}
