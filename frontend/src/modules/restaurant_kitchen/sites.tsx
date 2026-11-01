import { Dropdown } from 'primereact/dropdown';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';

/** Site d'une nouvelle saisie : le site sélectionné, sinon le seul site, sinon à choisir. */
export function useDefaultSite(): { siteId: string | null; chooseSite: boolean } {
  const { capabilities, siteId } = useCapabilities();
  const only = capabilities.sites.length === 1 ? (capabilities.sites[0]?.id ?? null) : null;
  return { siteId: siteId ?? only, chooseSite: siteId === null && capabilities.sites.length > 1 };
}

export function SiteDropdown({
  id,
  value,
  onChange,
}: {
  id: string;
  value: string | null;
  onChange: (value: string | null) => void;
}) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  return (
    <Dropdown
      inputId={id}
      value={value}
      onChange={(e) => onChange(e.value as string | null)}
      options={capabilities.sites.map((s) => ({ value: s.id, label: s.name }))}
      placeholder={t('stock.chooseSite')}
    />
  );
}
