import { Button } from 'primereact/button';
import { useTranslation } from 'react-i18next';

import { translateError } from '@/shared/lib/errors';

/**
 * Erreur de chargement d'un écran ou d'un bloc : message traduit (jamais d'erreur technique
 * brute) et action « Réessayer » éventuelle.
 */
export function ErrorMessage({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="sm-empty sm-error-state" role="alert">
      <i className="pi pi-exclamation-circle sm-empty-icon" aria-hidden />
      <p className="sm-empty-title">{t('errors.loadFailed')}</p>
      <p className="sm-empty-text">{translateError(t, error)}</p>
      {onRetry && (
        <Button icon="pi pi-refresh" label={t('actions.retry')} outlined onClick={onRetry} />
      )}
    </div>
  );
}
