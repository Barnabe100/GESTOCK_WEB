import { Button } from 'primereact/button';
import { useTranslation } from 'react-i18next';
import { matchPath, useLocation, useNavigate } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import type { FrontendModule } from '@/core/modules/types';
import { EmptyState } from '@/shared/ui/EmptyState';
import { NotFound } from '@/shared/ui/NotFound';

/**
 * Route sans page pour le contexte courant. Si elle appartient à un module de l'application,
 * c'est que ce module (ou la permission de la page) n'est pas disponible sur le site actif —
 * typiquement après un changement de site : message explicite et retour au tableau de bord,
 * jamais un écran blanc. Le backend reste l'autorité (la navigation n'est pas une sécurité).
 */
export function RouteFallback({ modules }: { modules: readonly FrontendModule[] }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const { capabilities } = useCapabilities();
  const known = modules.some((module) =>
    module.routes.some((route) => matchPath({ path: `/${route.path}`, end: true }, pathname)),
  );
  if (!known) return <NotFound />;
  return (
    <EmptyState
      icon="pi pi-lock"
      title={t('layout.unavailableHere')}
      description={t('layout.unavailableHereHint', {
        site: capabilities.site?.name ?? t('layout.allSites'),
      })}
      action={
        <Button
          icon="pi pi-home"
          label={t('errors.backHome')}
          outlined
          onClick={() => void navigate('/')}
        />
      }
    />
  );
}
