import type { ReactNode } from 'react';

/** Groupe logique d'un formulaire (titre, description courte, champs). */
export function FormSection({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <fieldset className="sm-fieldset">
      <legend>{title}</legend>
      {description && <p className="sm-help sm-fieldset-help">{description}</p>}
      {children}
    </fieldset>
  );
}
