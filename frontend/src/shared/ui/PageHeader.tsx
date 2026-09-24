import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';

export interface Crumb {
  label: string;
  to?: string;
}

/**
 * En-tête standard d'une page : fil d'Ariane éventuel, titre, description courte, actions
 * principales. Puis, dans la page : filtres, puis contenu.
 */
export function PageHeader({
  title,
  description,
  breadcrumbs,
  actions,
}: {
  title: string;
  description?: string;
  breadcrumbs?: Crumb[];
  actions?: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <header className="sm-page-header">
      <div className="sm-page-heading">
        {breadcrumbs && breadcrumbs.length > 0 && (
          <nav aria-label={t('layout.breadcrumb')} className="sm-breadcrumbs">
            <ol>
              {breadcrumbs.map((c) => (
                <li key={c.label}>{c.to ? <Link to={c.to}>{c.label}</Link> : c.label}</li>
              ))}
            </ol>
          </nav>
        )}
        <h1>{title}</h1>
        {description && <p className="sm-page-description">{description}</p>}
      </div>
      {actions && <div className="sm-page-actions">{actions}</div>}
    </header>
  );
}
