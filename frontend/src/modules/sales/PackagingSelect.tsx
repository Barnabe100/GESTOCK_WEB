import { Dropdown } from 'primereact/dropdown';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { usePackagings, type Packaging } from '@/modules/catalog/api';
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
 * désactivé depuis (le serveur refusera la validation). Un conditionnement au prix non configuré
 * est signalé « Prix non configuré » et ne peut pas être choisi.
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
  const active: (SalePackaging | Packaging)[] = packagings.data?.items ?? [];
  const options = value && !active.some((p) => p.id === value.id) ? [...active, value] : active;
  if (options.length === 0) return null;
  // Prix non configuré : affiché mais NON sélectionnable (le serveur refuse aussi la vente).
  const priced = (p: SalePackaging | Packaging): p is SalePackaging => p.sale_price !== null;
  const money = (v: string) => formatMoney(v, currency, locale);
  return (
    <Dropdown
      inputId={id}
      value={value?.id ?? BASE_UNIT}
      options={[
        { value: BASE_UNIT, label: basePrice ? `${unit} · ${money(basePrice)}` : unit },
        ...options.map((p) => ({
          value: p.id,
          label: `${p.name} (${formatQuantity(p.conversion, locale)} ${unit}) · ${
            priced(p) ? money(p.sale_price) : t('packagings.priceNotSet')
          }`,
          disabled: !priced(p),
        })),
      ]}
      optionDisabled="disabled"
      aria-label={t('sales.presentation')}
      onChange={(e) => {
        const chosen = e.value as string;
        const found = options.find((p) => p.id === chosen);
        onChange(chosen === BASE_UNIT || !found || !priced(found) ? null : found);
      }}
    />
  );
}
