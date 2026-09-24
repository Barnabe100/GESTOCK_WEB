import { useTranslation } from 'react-i18next';

import { formatQuantity } from '@/shared/lib/decimal';
import { StatusBadge, type Tone } from '@/shared/ui/StatusBadge';

import type { InventoryStatus } from './api';

/** Tonalités du Design System : brouillon neutre, en cours info, à valider avertissement. */
const STATUS_TONES: Record<InventoryStatus, Tone> = {
  DRAFT: 'neutral',
  COUNTING: 'info',
  READY_TO_VALIDATE: 'warning',
  VALIDATED: 'success',
  CANCELLED: 'danger',
};

export function InventoryStatusBadge({ status }: { status: InventoryStatus }) {
  const { t } = useTranslation();
  return <StatusBadge tone={STATUS_TONES[status]} label={t(`inventories.statuses.${status}`)} />;
}

export type VarianceKind = 'surplus' | 'shortage' | 'none';

/** Sens d'un écart à partir de sa chaîne décimale (aucun calcul : lecture du signe). */
export function varianceKind(value: string): VarianceKind {
  if (value.startsWith('-')) return 'shortage';
  return /[1-9]/.test(value) ? 'surplus' : 'none';
}

const VARIANCE_TONES: Record<VarianceKind, Tone> = {
  surplus: 'info',
  shortage: 'danger',
  none: 'success',
};

/** Écart : valeur signée + libellé (« +5 Excédent »), jamais la seule couleur. */
export function VarianceBadge({ value, locale }: { value: string | null; locale: string }) {
  const { t } = useTranslation();
  if (value === null) return <span className="sm-muted">—</span>;
  const kind = varianceKind(value);
  const number = formatQuantity(value, locale);
  return (
    <span className="sm-variance">
      <span className="sm-num">{kind === 'surplus' ? `+${number}` : number}</span>
      <StatusBadge tone={VARIANCE_TONES[kind]} label={t(`inventories.variance.${kind}`)} />
    </span>
  );
}
