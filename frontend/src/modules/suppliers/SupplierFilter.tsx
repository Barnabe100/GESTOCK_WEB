import { Dropdown } from 'primereact/dropdown';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';

import { useSuppliers } from './api';

/** Fournisseurs proposés au filtre : actifs ET inactifs (l'historique reste consultable). */
const FILTER_QUERY = 'limit=200&sort=name';

/**
 * Filtre de liste par fournisseur (Lot 3-E : entrées, articles). Affiché seulement si le module
 * Fournisseurs est actif et consultable ; le serveur applique le filtre (`supplier_id`).
 */
export function SupplierFilter({
  value,
  onChange,
  label,
}: {
  value: string | null;
  onChange: (value: string | null) => void;
  label?: string;
}) {
  const { t } = useTranslation();
  const { can, hasModule } = useCapabilities();
  const visible = hasModule('suppliers') && can('suppliers.supplier.view');
  const suppliers = useSuppliers(FILTER_QUERY, visible);
  if (!visible) return null;
  return (
    <Dropdown
      value={value}
      onChange={(e) => onChange((e.value as string | undefined) ?? null)}
      options={(suppliers.data?.items ?? []).map((s) => ({ value: s.id, label: s.name }))}
      placeholder={t('suppliers.all')}
      showClear
      filter
      aria-label={label ?? t('entries.supplier')}
    />
  );
}
