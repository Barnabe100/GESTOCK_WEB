import { ProgressBar } from 'primereact/progressbar';
import { useId } from 'react';
import { useTranslation } from 'react-i18next';

import type { Onboarding } from './onboardingApi';

/** Barre et libellés de progression, tels que calculés par le serveur. */
export function OnboardingProgress({ onboarding }: { onboarding: Onboarding }) {
  const { t } = useTranslation();
  const id = useId();
  const { progress } = onboarding;
  return (
    <div className="sm-onboarding-progress">
      <p className="sm-strong" id={id}>
        {t('onboarding.percentage', { percentage: progress.percentage })}
      </p>
      <ProgressBar
        value={progress.percentage}
        showValue={false}
        aria-labelledby={id}
        aria-valuetext={t('onboarding.percentage', { percentage: progress.percentage })}
      />
      <p className="sm-help">
        {t('onboarding.counts', { completed: progress.completed, total: progress.total })}
        {' · '}
        {t('onboarding.requiredCounts', {
          completed: progress.required_completed,
          total: progress.required_total,
        })}
      </p>
    </div>
  );
}
