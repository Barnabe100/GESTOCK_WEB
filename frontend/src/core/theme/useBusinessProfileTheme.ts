import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';

import { FRONTEND_MODULES } from '@/app/modules';
import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';

import { resolveBusinessProfileTheme, type BusinessProfileTheme } from './businessProfileTheme';

/** Thème métier du site actif (recalculé à chaque changement de site ou de profil). */
export function useBusinessProfileTheme(): BusinessProfileTheme {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  return useMemo(
    () => resolveBusinessProfileTheme(capabilities, t, FRONTEND_MODULES),
    [capabilities, t],
  );
}
