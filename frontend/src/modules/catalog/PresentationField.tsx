import { Dropdown } from 'primereact/dropdown';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatQuantity, normalizeDecimal } from '@/shared/lib/decimal';
import {
  equivalences,
  formatEquivalence,
  toBase,
  type PresentationPackaging,
} from '@/shared/lib/presentation';

import { usePackagings } from './api';

const BASE_UNIT = 'base';

/**
 * Équivalence affichée d'une quantité saisie (Lot 3-C) : « 2 Carton 24 = 48 bouteilles » pour
 * un conditionnement ; « 48 bouteilles = 8 Pack 6 = 2 Carton 24 » en unité de base. Indicatif :
 * le serveur calcule et enregistre la quantité en unité de base.
 */
export function QuantityEquivalence({
  quantity,
  unit,
  packaging,
  packagings,
  decimalAllowed = false,
  testId = 'presentation-equivalence',
}: {
  quantity: string;
  unit: string;
  packaging: PresentationPackaging | null;
  packagings: PresentationPackaging[];
  decimalAllowed?: boolean;
  testId?: string;
}) {
  const { capabilities } = useCapabilities();
  const { locale } = capabilities.tenant;
  const normalized = normalizeDecimal(quantity, 3);
  if (normalized === null || !/[1-9]/.test(normalized)) return null;
  const base = packaging ? toBase(normalized, packaging.conversion) : normalized;
  if (base === null) return null;
  const parts = packaging
    ? [
        `${formatQuantity(normalized, locale)} ${packaging.name}`,
        `= ${formatQuantity(base, locale)} ${unit}`,
      ]
    : [
        `${formatQuantity(base, locale)} ${unit}`,
        ...equivalences(base, packagings, decimalAllowed).map((e) =>
          formatEquivalence(e, unit, locale),
        ),
      ];
  if (parts.length < 2) return null;
  return (
    <small className="sm-equivalence" data-testid={testId}>
      {parts.join(' ')}
    </small>
  );
}

/**
 * Présentation d'une ligne d'opération de stock (Lot 3-C) : choix entre l'unité de base
 * (toujours proposée) et un conditionnement ACTIF de l'article — le prix n'intervient pas —,
 * puis équivalence de la quantité saisie. Un conditionnement déjà enregistré sur le brouillon
 * reste affiché même s'il a été désactivé depuis (le serveur refusera la validation). Guidage
 * seulement : le serveur relit tout et calcule la quantité en unité de base.
 */
export function PresentationField({
  id,
  articleId,
  unit,
  decimalAllowed = false,
  value,
  quantity,
  onChange,
}: {
  id: string;
  articleId: string | undefined;
  unit: string;
  decimalAllowed?: boolean;
  value: PresentationPackaging | null;
  quantity: string;
  onChange: (value: PresentationPackaging | null) => void;
}) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const { locale } = capabilities.tenant;
  const packagings = usePackagings(articleId, 'status=active&limit=100');
  const active: PresentationPackaging[] = (packagings.data?.items ?? []).map((p) => ({
    id: p.id,
    name: p.name,
    conversion: p.conversion,
  }));
  const options = value && !active.some((p) => p.id === value.id) ? [...active, value] : active;
  if (options.length === 0) return null;
  return (
    <div className="sm-presentation">
      <Dropdown
        inputId={id}
        value={value?.id ?? BASE_UNIT}
        options={[
          { value: BASE_UNIT, label: t('presentation.baseUnit', { unit }) },
          ...options.map((p) => ({
            value: p.id,
            label: `${p.name} (${formatQuantity(p.conversion, locale)} ${unit})`,
          })),
        ]}
        aria-label={t('presentation.label')}
        onChange={(e) => {
          const chosen = e.value as string;
          onChange(chosen === BASE_UNIT ? null : (options.find((p) => p.id === chosen) ?? null));
        }}
      />
      <QuantityEquivalence
        quantity={quantity}
        unit={unit}
        packaging={value}
        packagings={active}
        decimalAllowed={decimalAllowed}
      />
    </div>
  );
}
