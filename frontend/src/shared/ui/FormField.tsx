import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

/** Champ de formulaire : libellé associé, marque « obligatoire », aide ou erreur. */
export function FormField({
  id,
  label,
  error,
  help,
  required = false,
  children,
}: {
  id: string;
  label: string;
  error?: string;
  help?: string;
  required?: boolean;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <div className="sm-field">
      <label htmlFor={id}>
        {label}
        {required && (
          <span className="sm-required" title={t('validation.requiredField')}>
            <span aria-hidden> *</span>
            <span className="sm-sr-only"> ({t('validation.requiredField')})</span>
          </span>
        )}
      </label>
      {children}
      {help && !error && <small className="sm-help">{help}</small>}
      {error && (
        <small className="p-error" role="alert">
          <i className="pi pi-exclamation-circle" aria-hidden /> {error}
        </small>
      )}
    </div>
  );
}
