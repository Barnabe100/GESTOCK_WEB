import { ProgressSpinner } from 'primereact/progressspinner';
import { useTranslation } from 'react-i18next';

/** Chargement d'un écran ou d'un bloc (annoncé aux technologies d'assistance). */
export function LoadingState({ label }: { label?: string }) {
  const { t } = useTranslation();
  return (
    <div className="sm-center" role="status" aria-live="polite">
      <ProgressSpinner style={{ width: '2.5rem', height: '2.5rem' }} strokeWidth="4" />
      <span className="sm-muted">{label ?? t('common.loading')}</span>
    </div>
  );
}
