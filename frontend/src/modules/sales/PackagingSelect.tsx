import { Dropdown } from 'primereact/dropdown';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { usePackagings } from '@/modules/catalog/api';
import { formatMoney, formatQuantity } from '@/shared/lib/decimal';

/** Présentation vendue sur une ligne (Lot 3-B) : `null` = unité de base. */
export interface SalePackaging {
  id: string;
  name: string;
  conversion: string;
  sale_price: string;
}

const BASE_UNIT = 'base';

/**
 * Choix de la présentation d'une ligne : unité de base (toujours proposée) ou conditionnement
 * ACTIF de l'article. Guidage seulement : le serveur relit le conditionnement, son prix et sa
 * conversion. Un conditionnement déjà enregistré sur le brouillon reste affiché même s'il a été
 * désactivé depuis (le serveur refusera la validation).
 */
export function PackagingSelect({
  id,
  articleId,
  unit,
  basePrice,
  value,
  onChange,
}: {
  id: string;
  articleId: string | undefined;
  unit: string;
  basePrice: string | undefined;
  value: SalePackaging | null;
  onChange: (value: SalePackaging | null) => void;
}) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const { currency, locale } = capabilities.tenant;
  const packagings = usePackagings(articleId, 'status=active&limit=100');
  const active: SalePackaging[] = packagings.data?.items ?? [];
  const options = value && !active.some((p) => p.id === value.id) ? [...active, value] : active;
  if (options.length === 0) return null;
  const money = (v: string) => formatMoney(v, currency, locale);
  return (
    <Dropdown
      inputId={id}
      value={value?.id ?? BASE_UNIT}
      options={[
        { value: BASE_UNIT, label: basePrice ? `${unit} · ${money(basePrice)}` : unit },
        ...options.map((p) => ({
          value: p.id,
          label: `${p.name} (${formatQuantity(p.conversion, locale)} ${unit}) · ${money(p.sale_price)}`,
        })),
      ]}
      aria-label={t('sales.presentation')}
      onChange={(e) => {
        const chosen = e.value as string;
        onChange(chosen === BASE_UNIT ? null : (options.find((p) => p.id === chosen) ?? null));
      }}
    />
  );
}
