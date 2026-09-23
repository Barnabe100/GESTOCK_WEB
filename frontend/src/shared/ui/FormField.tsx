import type { ReactNode } from 'react';

export function FormField({
  id,
  label,
  error,
  help,
  children,
}: {
  id: string;
  label: string;
  error?: string;
  help?: string;
  children: ReactNode;
}) {
  return (
    <div className="sm-field">
      <label htmlFor={id}>{label}</label>
      {children}
      {help && !error && <small className="sm-help">{help}</small>}
      {error && (
        <small className="p-error" role="alert">
          {error}
        </small>
      )}
    </div>
  );
}
