import { Button } from 'primereact/button';
import { Message } from 'primereact/message';
import { useTranslation } from 'react-i18next';

import { translateError } from '@/shared/lib/errors';

export function ErrorMessage({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="sm-stack">
      <Message severity="error" text={translateError(t, error)} />
      {onRetry && <Button label={t('actions.retry')} text onClick={onRetry} />}
    </div>
  );
}
