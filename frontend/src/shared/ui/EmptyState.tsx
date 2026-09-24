import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

/** État vide : message clair, piste éventuelle, action principale éventuelle. */
export function EmptyState({
  title,
  description,
  icon = 'pi pi-inbox',
  action,
}: {
  title?: string;
  description?: string;
  icon?: string;
  action?: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <div className="sm-empty">
      <i className={`${icon} sm-empty-icon`} aria-hidden />
      <p className="sm-empty-title">{title ?? t('common.noData')}</p>
      {description && <p className="sm-empty-text">{description}</p>}
      {action}
    </div>
  );
}

/**
 * État vide d'une liste : « aucun résultat » si des filtres sont appliqués (avec la piste
 * « réinitialiser »), sinon message propre à la liste et action de création éventuelle.
 */
export function ListEmpty({
  filtered,
  title,
  action,
  icon,
}: {
  filtered: boolean;
  title: string;
  action?: ReactNode;
  icon?: string;
}) {
  const { t } = useTranslation();
  return filtered ? (
    <EmptyState
      icon="pi pi-filter-slash"
      title={t('common.noResults')}
      description={t('common.noResultsHint')}
    />
  ) : (
    <EmptyState icon={icon} title={title} action={action} />
  );
}
