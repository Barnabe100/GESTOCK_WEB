import { Button } from 'primereact/button';
import { Card } from 'primereact/card';
import { Message } from 'primereact/message';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { LoadingState } from '@/shared/ui/LoadingState';
import { PageHeader } from '@/shared/ui/PageHeader';
import { StatusBadge, type Tone } from '@/shared/ui/StatusBadge';

import {
  useOnboarding,
  useStartOnboardingStep,
  type OnboardingStatus,
  type OnboardingStep,
} from './onboardingApi';
import { OnboardingProgress } from './OnboardingProgress';

const STATUS_TONES: Record<OnboardingStatus, Tone> = {
  NOT_STARTED: 'neutral',
  IN_PROGRESS: 'info',
  COMPLETED: 'success',
};

function stepIcon(step: OnboardingStep, current: boolean): string {
  if (step.status === 'COMPLETED') return 'pi pi-check-circle';
  if (current) return 'pi pi-arrow-circle-right';
  return step.status === 'IN_PROGRESS' ? 'pi pi-clock' : 'pi pi-circle';
}

/** Action d'une étape : l'étape est marquée « en cours » (jamais « terminée ») puis l'écran
 * concerné est ouvert ; action indisponible : raison affichée (le backend refuse de toute
 * façon). */
function StepAction({ step, primary }: { step: OnboardingStep; primary?: boolean }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { can } = useCapabilities();
  const start = useStartOnboardingStep();
  const action = step.action;
  if (!action || step.status === 'COMPLETED') return null;
  if (!action.available) {
    return (
      <p className="sm-help" data-testid={`blocked-${step.code}`}>
        <i className="pi pi-lock" aria-hidden /> {t(`onboarding.blocked.${action.blocked_reason}`)}
      </p>
    );
  }
  return (
    <Button
      label={t(action.label)}
      icon="pi pi-arrow-right"
      iconPos="right"
      outlined={!primary}
      size={primary ? undefined : 'small'}
      onClick={() => {
        if (step.status === 'NOT_STARTED' && can('organization.onboarding.manage')) {
          start.mutate(step.code);
        }
        void navigate(action.route);
      }}
    />
  );
}

export default function OnboardingPage() {
  const { t } = useTranslation();
  const onboarding = useOnboarding();

  if (onboarding.isError)
    return <ErrorMessage error={onboarding.error} onRetry={() => void onboarding.refetch()} />;
  if (!onboarding.data) return <LoadingState />;

  const data = onboarding.data;
  const current = data.steps.find((s) => s.code === data.current_step);
  return (
    <>
      <PageHeader title={t('onboarding.title')} description={t('onboarding.welcome')} />
      {data.completed && (
        <Message
          severity={data.subscription_status === 'pending_activation' ? 'info' : 'success'}
          className="sm-block"
          data-testid="onboarding-completed"
          text={t(
            data.subscription_status === 'pending_activation'
              ? 'onboarding.completedPending'
              : 'onboarding.completed',
          )}
        />
      )}
      <Card className="sm-block" title={t('onboarding.progressTitle')}>
        <OnboardingProgress onboarding={data} />
        {current && (
          <div className="sm-onboarding-next" data-testid="onboarding-next">
            <p>
              <span className="sm-muted">
                {t(current.required ? 'onboarding.nextStep' : 'onboarding.nextRecommendation')}
              </span>{' '}
              <strong>{t(current.title)}</strong>
            </p>
            <StepAction step={current} primary />
          </div>
        )}
      </Card>
      <ol className="sm-onboarding-steps" aria-label={t('onboarding.steps.label')}>
        {data.steps.map((step) => {
          const isCurrent = step.code === data.current_step;
          return (
            <li
              key={step.code}
              className={`sm-onboarding-step sm-onboarding-step--${step.status.toLowerCase()}${
                isCurrent ? ' sm-onboarding-step--current' : ''
              }`}
              aria-current={isCurrent ? 'step' : undefined}
              data-testid={`step-${step.code}`}
            >
              <i className={`sm-onboarding-step-icon ${stepIcon(step, isCurrent)}`} aria-hidden />
              <div className="sm-onboarding-step-body">
                <div className="sm-onboarding-step-head">
                  <h3>{t(step.title)}</h3>
                  <StatusBadge
                    tone={step.required ? 'warning' : 'neutral'}
                    label={t(step.required ? 'onboarding.required' : 'onboarding.recommended')}
                  />
                  <StatusBadge
                    tone={STATUS_TONES[step.status]}
                    label={t(`onboarding.status.${step.status}`)}
                  />
                </div>
                <p className="sm-muted">{t(step.description)}</p>
                <StepAction step={step} />
              </div>
            </li>
          );
        })}
      </ol>
    </>
  );
}
