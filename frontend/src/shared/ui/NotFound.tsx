import { useTranslation } from 'react-i18next';

export function NotFound() {
  const { t } = useTranslation();
  return (
    <div className="sm-center">
      <p>{t('errors.pageNotFound')}</p>
    </div>
  );
}
