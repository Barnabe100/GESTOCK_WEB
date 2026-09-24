import { Button } from 'primereact/button';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import { EmptyState } from './EmptyState';

export function NotFound() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  return (
    <EmptyState
      icon="pi pi-compass"
      title={t('errors.pageNotFound')}
      description={t('errors.pageNotFoundHint')}
      action={
        <Button
          icon="pi pi-home"
          label={t('errors.backHome')}
          outlined
          onClick={() => void navigate('/')}
        />
      }
    />
  );
}
