import { Message } from 'primereact/message';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ApiError } from '@/core/api/client';
import { formatMoney } from '@/shared/lib/decimal';
import { translateError } from '@/shared/lib/errors';

import { publicQrApi, type PublicTracking } from './publicApi';

/**
 * Suivi temporaire d'UNE commande passée par QR Code (R7-E, ADR-0052) : état, lignes, total et
 * reste à régler auprès du personnel ; motif d'un refus. Actualisé toutes les 15 s. Aucun
 * paiement en ligne.
 */

const TRACKING_REFRESH_MS = 15_000;

export function PublicTrackingPage({ secret }: { secret: string }) {
  const { t } = useTranslation();
  const [tracking, setTracking] = useState<PublicTracking | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    const load = () =>
      publicQrApi.track(secret, controller.signal).then(
        (data) => {
          setTracking(data);
          setError(null);
        },
        (failure: unknown) => {
          if (controller.signal.aborted) return;
          setError(failure instanceof ApiError ? failure : new ApiError(0, 'unknown', ''));
        },
      );
    void load();
    const timer = window.setInterval(() => void load(), TRACKING_REFRESH_MS);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [secret]);

  if (error && (error.status === 404 || !tracking)) {
    return (
      <main className="sm-qr-public">
        <Message severity="error" text={translateError(t, error)} />
      </main>
    );
  }
  if (!tracking) {
    return (
      <main className="sm-qr-public">
        <p className="sm-help">{t('qrPublic.loading')}</p>
      </main>
    );
  }
  const unpaid = tracking.remaining_due !== '0.00';
  return (
    <main className="sm-qr-public">
      <header className="sm-qr-public-header">
        <h1>{tracking.establishment}</h1>
        <p className="sm-help">
          {tracking.site_name}
          {tracking.point
            ? ` · ${
                tracking.point.kind === 'TABLE'
                  ? t('qrPublic.table', { label: tracking.point.label })
                  : tracking.point.label
              }`
            : ''}
        </p>
      </header>
      <section className="sm-qr-tracking" aria-label={t('qrPublic.tracking')}>
        <p className="sm-help">{t('qrPublic.orderNumber')}</p>
        <p className="sm-qr-tracking-number" data-testid="qr-order-number">
          {tracking.daily_number}
        </p>
        <p className="sm-qr-tracking-status" data-testid="qr-order-status">
          {t(`qrPublic.statuses.${tracking.status}`)}
        </p>
        {tracking.status === 'REJECTED' && tracking.rejection_reason && (
          <Message
            severity="warn"
            text={t('qrPublic.rejectedBecause', { reason: tracking.rejection_reason })}
          />
        )}
        <ul className="sm-qr-tracking-lines">
          {tracking.lines.map((line, index) => (
            <li key={index} className={line.status === 'CANCELLED' ? 'sm-qr-line-cancelled' : ''}>
              <span>
                {Number(line.quantity)} ×{' '}
                {line.packaging_name ? `${line.name} — ${line.packaging_name}` : line.name}
              </span>
              <span className="sm-help">{t(`qrPublic.lineStatuses.${line.status}`)}</span>
            </li>
          ))}
        </ul>
        <dl className="sm-details">
          <div>
            <dt>{t('qrPublic.total')}</dt>
            <dd>{formatMoney(tracking.total, tracking.currency, 'fr')}</dd>
          </div>
          <div>
            <dt>{t('qrPublic.remainingDue')}</dt>
            <dd data-testid="qr-remaining-due">
              {formatMoney(tracking.remaining_due, tracking.currency, 'fr')}
            </dd>
          </div>
        </dl>
        {unpaid && <p className="sm-help">{t('qrPublic.payOnSite')}</p>}
        {tracking.available_until && <p className="sm-help">{t('qrPublic.trackingEnds')}</p>}
      </section>
    </main>
  );
}
