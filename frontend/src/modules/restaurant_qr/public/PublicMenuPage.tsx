import { Button } from 'primereact/button';
import { InputText } from 'primereact/inputtext';
import { Message } from 'primereact/message';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ApiError } from '@/core/api/client';
import { formatMoney } from '@/shared/lib/decimal';
import { translateError } from '@/shared/lib/errors';
import { newUuid } from '@/shared/lib/uuid';

import { publicQrApi, type PublicMenu, type PublicMenuItem } from './publicApi';

/**
 * Menu public d'un point QR (R7, ADR-0052) : consulter, choisir, quantités, instructions,
 * informations demandées par le site, envoyer. Aucun compte, aucun paiement. Le serveur
 * revérifie TOUT (prix, disponibilité, plafonds, informations) ; la page ne fait qu'aider.
 */

const MENU_REFRESH_MS = 30_000;

interface CartLine {
  quantity: number;
  note: string;
}

function itemTitle(item: PublicMenuItem): string {
  return item.packaging_name ? `${item.name} — ${item.packaging_name}` : item.name;
}

export function PublicMenuPage({ token }: { token: string }) {
  const { t } = useTranslation();
  const [menu, setMenu] = useState<PublicMenu | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [cart, setCart] = useState<Record<string, CartLine>>({});
  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [sending, setSending] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState<string[]>([]);
  // Clé d'idempotence : la même tant que le contenu envoyé ne change pas (renvoi après une
  // coupure réseau = même commande) ; nouvelle dès que le contenu change.
  const submission = useRef<{ key: string; content: string } | null>(null);

  const load = useCallback(
    (signal?: AbortSignal) =>
      publicQrApi.menu(token, signal).then(
        (data) => {
          setMenu(data);
          setError(null);
        },
        (failure: unknown) => {
          if (signal?.aborted) return;
          setError(failure instanceof ApiError ? failure : new ApiError(0, 'unknown', ''));
        },
      ),
    [token],
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    const timer = window.setInterval(() => void load(), MENU_REFRESH_MS);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [load]);

  if (error && !menu) {
    return (
      <main className="sm-qr-public">
        <Message severity="error" text={translateError(t, error)} />
      </main>
    );
  }
  if (!menu) {
    return (
      <main className="sm-qr-public">
        <p className="sm-help">{t('qrPublic.loading')}</p>
      </main>
    );
  }

  const items = new Map(menu.sections.flatMap((s) => s.items.map((i) => [i.id, i] as const)));
  const lines = Object.entries(cart).filter(([, line]) => line.quantity > 0);
  // Total INDICATIF (en centimes, sans erreur d'arrondi) : le serveur fixe le total réel.
  const totalCents = lines.reduce((sum, [id, line]) => {
    const item = items.get(id);
    return item ? sum + Math.round(Number(item.price) * 100) * line.quantity : sum;
  }, 0);
  const { ordering } = menu;
  const setQuantity = (id: string, quantity: number) =>
    setCart((current) => {
      const next = { ...current };
      const bounded = Math.max(0, Math.min(ordering.max_quantity_per_line, quantity));
      if (bounded === 0) delete next[id];
      else next[id] = { quantity: bounded, note: current[id]?.note ?? '' };
      return next;
    });
  const setNote = (id: string, note: string) =>
    setCart((current) => (current[id] ? { ...current, [id]: { ...current[id], note } } : current));
  const tooManyLines = lines.length > ordering.max_lines;
  const nameMissing = ordering.customer_name_mode === 'REQUIRED' && name.trim() === '';
  const phoneMissing = ordering.customer_phone_mode === 'REQUIRED' && phone.trim() === '';
  const canSend = lines.length > 0 && !tooManyLines && !nameMissing && !phoneMissing && !sending;

  const send = () => {
    const body = {
      lines: lines.map(([id, line]) => ({
        menu_item_id: id,
        quantity: line.quantity,
        note: line.note.trim() || null,
      })),
      customer_name: ordering.customer_name_mode === 'HIDDEN' ? null : name.trim() || null,
      customer_phone: ordering.customer_phone_mode === 'HIDDEN' ? null : phone.trim() || null,
    };
    const content = JSON.stringify(body);
    if (submission.current?.content !== content) {
      try {
        submission.current = { key: newUuid(), content };
      } catch {
        // Jamais de toucher sans effet : l'échec (aucun aléa disponible) est signalé.
        setSubmitError(t('errors:unknown'));
        return;
      }
    }
    setSending(true);
    setSubmitError(null);
    setUnavailable([]);
    publicQrApi.order(token, { client_key: submission.current.key, ...body }).then(
      (placed) => {
        submission.current = null;
        setCart({});
        // Adresse construite par le serveur (configuration) ; le secret est dans le fragment.
        window.location.assign(placed.tracking_url);
      },
      (failure: unknown) => {
        setSending(false);
        if (failure instanceof ApiError && failure.code === 'qr_order_item_unavailable') {
          setUnavailable((failure.extra.items as string[] | undefined) ?? []);
          void load();
        }
        setSubmitError(translateError(t, failure));
      },
    );
  };

  return (
    <main className="sm-qr-public">
      <header className="sm-qr-public-header">
        <h1>{menu.establishment}</h1>
        <p className="sm-help">
          {menu.site_name} ·{' '}
          {menu.point.kind === 'TABLE'
            ? t('qrPublic.table', { label: menu.point.label })
            : menu.point.label}
        </p>
      </header>
      {error && <Message severity="warn" text={t('qrPublic.menuStale')} />}
      {menu.sections.length === 0 && <p className="sm-help">{t('qrPublic.emptyMenu')}</p>}
      {menu.sections.map((section) => (
        <section key={section.id} className="sm-qr-section" aria-label={section.name}>
          <h2>{section.name}</h2>
          <ul>
            {section.items.map((item) => {
              const line = cart[item.id];
              const quantity = line?.quantity ?? 0;
              return (
                <li
                  key={item.id}
                  className={`sm-qr-item${unavailable.includes(item.id) ? ' sm-qr-item-error' : ''}`}
                >
                  <div className="sm-qr-item-head">
                    <span className="sm-qr-item-name">{itemTitle(item)}</span>
                    <span className="sm-qr-item-price">
                      {formatMoney(item.price, menu.currency, 'fr')}
                    </span>
                  </div>
                  {item.description && <p className="sm-help">{item.description}</p>}
                  {!item.available ? (
                    <span className="sm-qr-item-soldout">{t('qrPublic.soldOut')}</span>
                  ) : (
                    <div className="sm-qr-qty">
                      <Button
                        type="button"
                        icon="pi pi-minus"
                        rounded
                        outlined
                        disabled={quantity === 0}
                        aria-label={t('qrPublic.less', { name: itemTitle(item) })}
                        onClick={() => setQuantity(item.id, quantity - 1)}
                      />
                      <span aria-live="polite" className="sm-qr-qty-value">
                        {quantity}
                      </span>
                      <Button
                        type="button"
                        icon="pi pi-plus"
                        rounded
                        disabled={quantity >= ordering.max_quantity_per_line}
                        aria-label={t('qrPublic.more', { name: itemTitle(item) })}
                        onClick={() => setQuantity(item.id, quantity + 1)}
                      />
                    </div>
                  )}
                  {quantity > 0 && (
                    <InputText
                      className="sm-qr-note"
                      maxLength={200}
                      value={line?.note ?? ''}
                      placeholder={t('qrPublic.notePlaceholder')}
                      aria-label={t('qrPublic.note', { name: itemTitle(item) })}
                      onChange={(e) => setNote(item.id, e.target.value)}
                    />
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      ))}
      {lines.length > 0 && (
        <section className="sm-qr-cart" aria-label={t('qrPublic.cart')}>
          <h2>{t('qrPublic.cart')}</h2>
          <ul>
            {lines.map(([id, line]) => {
              const item = items.get(id);
              return (
                <li key={id}>
                  {line.quantity} × {item ? itemTitle(item) : '—'}
                </li>
              );
            })}
          </ul>
          {ordering.customer_name_mode !== 'HIDDEN' && (
            <label className="sm-qr-field">
              <span>
                {t('qrPublic.name')}
                {ordering.customer_name_mode === 'REQUIRED' ? ' *' : ''}
              </span>
              <InputText
                maxLength={40}
                value={name}
                autoComplete="given-name"
                onChange={(e) => setName(e.target.value)}
              />
            </label>
          )}
          {ordering.customer_phone_mode !== 'HIDDEN' && (
            <label className="sm-qr-field">
              <span>
                {t('qrPublic.phone')}
                {ordering.customer_phone_mode === 'REQUIRED' ? ' *' : ''}
              </span>
              <InputText
                maxLength={30}
                value={phone}
                inputMode="tel"
                autoComplete="tel"
                onChange={(e) => setPhone(e.target.value)}
              />
              <small className="sm-help">{t('qrPublic.phoneHelp')}</small>
            </label>
          )}
          {tooManyLines && (
            <Message
              severity="warn"
              text={t('qrPublic.tooManyLines', { max: ordering.max_lines })}
            />
          )}
          {submitError && <Message severity="error" text={submitError} />}
          {/* Bouton désactivé : la raison est toujours écrite (le serveur revérifie tout). */}
          {(nameMissing || phoneMissing) && (
            <p className="sm-help" id="qr-send-hint" role="status">
              {t(nameMissing ? 'qrPublic.nameRequired' : 'qrPublic.phoneRequired')}
            </p>
          )}
          <div className="sm-qr-cart-footer">
            <strong>
              {t('qrPublic.total')} :{' '}
              {formatMoney((totalCents / 100).toFixed(2), menu.currency, 'fr')}
            </strong>
            <Button
              type="button"
              icon="pi pi-send"
              label={t('qrPublic.send')}
              disabled={!canSend}
              loading={sending}
              aria-describedby={nameMissing || phoneMissing ? 'qr-send-hint' : undefined}
              onClick={send}
            />
          </div>
          <p className="sm-help">{t('qrPublic.noPayment')}</p>
        </section>
      )}
    </main>
  );
}
