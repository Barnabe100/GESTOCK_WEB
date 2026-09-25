import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { LoadingState } from '@/shared/ui/LoadingState';

import { useDocumentIdentity, type IdentityLine } from './api';

/**
 * Aperçu de l'en-tête des futurs reçus et documents. L'en-tête est construit par le serveur à
 * partir du tenant (source unique) : rien n'est recalculé ni recopié ici, et une information
 * absente n'apparaît pas (jamais « N/A »).
 */
export function DocumentIdentityPreview({ totalRecommended }: { totalRecommended: number }) {
  const { t } = useTranslation();
  const identity = useDocumentIdentity();
  const [logoFailed, setLogoFailed] = useState<string | null>(null);

  if (identity.isError)
    return <ErrorMessage error={identity.error} onRetry={() => void identity.refetch()} />;
  if (!identity.data) return <LoadingState />;

  const data = identity.data;
  const line = (l: IdentityLine) => (
    <span key={l.kind} data-kind={l.kind}>
      {t(`company.preview.lines.${l.kind}`, { value: l.value })}
    </span>
  );
  const filled = totalRecommended - data.missing_recommended.length;
  return (
    <>
      <div className="sm-identity-preview" data-testid="identity-preview">
        {data.logo_url && logoFailed !== data.logo_url && (
          <img
            src={data.logo_url}
            alt={t('company.preview.logoAlt', { name: data.name })}
            referrerPolicy="no-referrer"
            onError={() => setLogoFailed(data.logo_url)}
          />
        )}
        <strong className="sm-identity-name">{data.name}</strong>
        {data.trade_name && <span>{data.trade_name}</span>}
        {data.contact.length > 0 && (
          <div className="sm-identity-block">{data.contact.map(line)}</div>
        )}
        {data.identifiers.length > 0 && (
          <div className="sm-identity-block">{data.identifiers.map(line)}</div>
        )}
      </div>
      <p className="sm-help" data-testid="identity-completeness">
        {t('company.preview.completeness', { count: filled, total: totalRecommended })}
      </p>
    </>
  );
}
