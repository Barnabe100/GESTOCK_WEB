import { useTranslation } from 'react-i18next';

import { profileLabel } from '@/core/capabilities/profile';
import { FormSection } from '@/shared/ui/FormSection';

/**
 * Profil d'origine de l'entreprise (choisi à l'inscription) : lecture seule. Le profil
 * d'activité effectif est propre à chaque site ; le serveur refuse tout changement du profil de
 * l'entreprise dès qu'un site existe (`profile_is_per_site`). Aucune donnée n'est modifiée.
 */
export function BusinessProfileSection({ code }: { code: string }) {
  const { t } = useTranslation();
  return (
    <FormSection title={t('company.profileSection')} description={t('company.profileHelp')}>
      <dl className="sm-details">
        <div>
          <dt>{t('company.originProfile')}</dt>
          <dd data-testid="company-profile">{profileLabel(t, { code, name: code })}</dd>
        </div>
      </dl>
    </FormSection>
  );
}
