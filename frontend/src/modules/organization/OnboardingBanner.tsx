import { Button } from 'primereact/button';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';

import { useOnboarding } from './onboardingApi';
import { OnboardingProgress } from './OnboardingProgress';

/** Bandeau du tableau de bord tant que l'onboarding n'est pas terminé (étapes obligatoires).
 * N'empêche jamais l'utilisation de l'application. */
export function OnboardingBanner() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { can } = useCapabilities();
  const allowed = can('organization.onboarding.view');
  const onboarding = useOnboarding(allowed);
  const data = onboarding.data;
  if (!allowed || !data || data.completed) return null;
  const current = data.steps.find((s) => s.code === data.current_step);
  return (
    <section
      className="sm-onboarding-banner sm-block"
      aria-labelledby="onboarding-banner-title"
      data-testid="onboarding-banner"
    >
      <div className="sm-onboarding-banner-text">
        <h2 id="onboarding-banner-title">{t('onboarding.bannerTitle')}</h2>
        <OnboardingProgress onboarding={data} />
        {current && (
          <p>
            <span className="sm-muted">{t('onboarding.nextStep')}</span>{' '}
            <strong>{t(current.title)}</strong>
          </p>
        )}
      </div>
      <Button
        label={t('onboarding.continue')}
        icon="pi pi-arrow-right"
        iconPos="right"
        onClick={() => void navigate('/onboarding')}
      />
    </section>
  );
}
