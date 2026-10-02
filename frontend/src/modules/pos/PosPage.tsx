import { Button } from 'primereact/button';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { IconField } from 'primereact/iconfield';
import { InputIcon } from 'primereact/inputicon';
import { InputText } from 'primereact/inputtext';
import { Message } from 'primereact/message';
import { InputTextarea } from 'primereact/inputtextarea';
import { useCallback, useContext, useEffect, useReducer, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import { ApiError } from '@/core/api/client';
import { AuthContext } from '@/core/auth/AuthContext';
import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { useCashSessions, useCashSites } from '@/modules/cash_register/api';
import type { Customer } from '@/modules/customers/api';
import { paymentError } from '@/modules/sales/ui';
import { translateError } from '@/shared/lib/errors';
import { formatMoney, formatQuantity, subtractMoney, sumMoney } from '@/shared/lib/decimal';
import { useDebouncedValue } from '@/shared/lib/serverTable';
import { EmptyState } from '@/shared/ui/EmptyState';
import { FormField } from '@/shared/ui/FormField';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { LoadingState } from '@/shared/ui/LoadingState';
import { StatusBadge } from '@/shared/ui/StatusBadge';

import {
  findByBarcode,
  useCheckout,
  usePosArticles,
  type CheckoutResult,
  type PosArticle,
  type PosPackaging,
} from './api';
import {
  baseQuantity,
  cartReducer,
  cartTotal,
  exceedsStock,
  lineKey,
  lineQuantity,
  lineTotal,
  unitPrice,
} from './cart';
import { PosCustomerDialog } from './PosCustomerDialog';
import { PosPaymentDialog, toCheckoutPayment, type PosPayment } from './PosPaymentDialog';
import { PosReceipt } from './PosReceipt';
import { ExpiredLotOverridePanel } from '@/modules/sales/ExpiredLotOverridePanel';
import type { ExpiredLotOverride } from '@/modules/sales/api';
import { expiredShortages, type ExpiredShortage } from '@/modules/stock/ui';
import { RecentSalesDialog } from './RecentSalesDialog';

type PosDialog = 'customer' | 'payment' | 'confirm' | 'recent' | null;

/** Présentation « unité de base » dans le choix du conditionnement (Lot 3-B). */
const BASE_UNIT = 'base';

/**
 * Point de vente : interface de saisie rapide au-dessus des services existants. Recherche
 * serveur des articles du site, panier, client facultatif, paiements (aucun, partiel,
 * multiples), encaissement en une étape (`POST /pos/checkout` : le serveur recalcule prix,
 * totaux, stock, crédit et caisse). Raccourcis : F2 recherche, F4 client, F8 paiement,
 * F10 validation, Échap fermeture, Entrée : scan d'un code-barres (correspondance EXACTE
 * cherchée par le serveur ; code inconnu signalé, rien n'est ajouté — Lot 3-A).
 */
export default function PosPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { can, capabilities, siteId: selectedSite, hasModule } = useCapabilities();
  const { currency, locale } = capabilities.tenant;
  const sites = capabilities.sites;
  const [chosenSite, setChosenSite] = useState<string | null>(
    sites.length === 1 ? (sites[0]?.id ?? null) : null,
  );
  const siteId = selectedSite ?? chosenSite;
  const siteName = sites.find((s) => s.id === siteId)?.name;
  const [search, setSearch] = useState('');
  const debounced = useDebouncedValue(search, 250);
  const articles = usePosArticles(siteId, debounced);
  const [lines, dispatch] = useReducer(cartReducer, []);
  const [customer, setCustomer] = useState<Customer | null>(null);
  const [payments, setPayments] = useState<PosPayment[]>([]);
  const [cashRegisterId, setCashRegisterId] = useState<string | null>(null);
  const [cartKey, setCartKey] = useState(() => crypto.randomUUID());
  const [dialog, setDialog] = useState<PosDialog>(null);
  const [result, setResult] = useState<CheckoutResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [overrideAllowed, setOverrideAllowed] = useState(false);
  const [overrideReason, setOverrideReason] = useState('');
  // Lot 3-H-A (O-1) : refus « stock non périmé insuffisant » — dérogation explicite proposée.
  const [expired, setExpired] = useState<ExpiredShortage[]>([]);
  const [tab, setTab] = useState<'catalog' | 'cart'>('catalog');
  const [scanError, setScanError] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const checkout = useCheckout();
  const userId = useContext(AuthContext)?.user?.id ?? null;
  const cashVisible =
    hasModule('cash_register') &&
    can('cash_register.session.view') &&
    can('cash_register.register.view');
  // Caisse optionnelle par site : état affiché seulement si elle est activée pour ce site.
  const cashSites = useCashSites(cashVisible);
  const cashEnabled = cashSites.data?.some((s) => s.site_id === siteId && s.enabled) === true;
  const openSessions = useCashSessions(
    new URLSearchParams({
      status: 'OPEN',
      site_id: siteId ?? '',
      limit: '5',
      ...(userId ? { opened_by: userId } : {}),
    }).toString(),
    cashVisible && cashEnabled && siteId !== null,
  );

  const money = (v: string) => formatMoney(v, currency, locale);
  const total = cartTotal(lines);
  const paid = sumMoney(payments.map((p) => p.amount));
  const remaining = subtractMoney(total, paid);
  const canSell = can('sales.sale.create') && can('sales.sale.validate');
  const canPay = can('sales.payment.create');
  const invalidLines = lines.some((l) => lineQuantity(l) === null);

  const reset = useCallback(() => {
    dispatch({ type: 'clear' });
    setCustomer(null);
    setPayments([]);
    setCashRegisterId(null);
    setCartKey(crypto.randomUUID());
    setResult(null);
    setError(null);
    setOverrideAllowed(false);
    setOverrideReason('');
    setExpired([]);
    setDialog(null);
    setTab('catalog');
    document.getElementById('pos-search')?.focus();
  }, []);

  const add = (article: PosArticle, packaging: PosPackaging | null = null) => {
    if (!article.is_active) return;
    dispatch({ type: 'add', article, packaging });
    // Le panier a changé : les paiements saisis sont à revoir.
    setPayments([]);
  };

  /** Entrée dans la recherche = scan : seule une correspondance exacte du serveur est ajoutée. */
  const scan = async (code: string) => {
    const barcode = code.trim();
    if (!barcode || siteId === null || scanning) return;
    setScanning(true);
    setScanError(null);
    try {
      // Lot 3-D : le code d'un conditionnement ajoute 1 conditionnement (présentation gardée).
      const found = await findByBarcode(siteId, barcode);
      add(found, found.packagings.find((p) => p.id === found.scanned_packaging_id) ?? null);
      setSearch('');
    } catch (failure) {
      setScanError(
        failure instanceof ApiError && failure.code !== 'barcode_unknown'
          ? translateError(t, failure)
          : t('pos.barcodeUnknown'),
      );
    } finally {
      setScanning(false);
    }
  };

  // Raccourcis clavier (sans effet quand un dialogue est ouvert ; Échap ferme les dialogues).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (dialog !== null || result !== null) return;
      const actions: Record<string, () => void> = {
        F2: () => document.getElementById('pos-search')?.focus(),
        F4: () => setDialog('customer'),
        F8: () => lines.length > 0 && canPay && setDialog('payment'),
        F10: () => lines.length > 0 && canSell && setDialog('confirm'),
      };
      const action = actions[e.key];
      if (action) {
        e.preventDefault();
        action();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [dialog, result, lines.length, canPay, canSell]);

  const confirm = (withOverride = false, expiredLotOverride: ExpiredLotOverride | null = null) => {
    if (!siteId) return;
    setError(null);
    checkout.mutate(
      {
        site_id: siteId,
        customer_id: customer?.id ?? null,
        lines: lines.map((l) => ({
          article_id: l.article.article_id,
          packaging_id: l.packaging?.id ?? null,
          quantity: lineQuantity(l) ?? '0',
        })),
        payments: payments.map((p) => toCheckoutPayment(p, cashRegisterId)),
        credit_override: withOverride ? { reason: overrideReason.trim() } : null,
        ...(expiredLotOverride ? { expired_lot_override: expiredLotOverride } : {}),
        idempotency_key: cartKey,
      },
      {
        onSuccess: (data) => {
          setDialog(null);
          setResult(data);
        },
        onError: (e) => {
          setError(paymentError(t, e, currency, locale));
          // Dépassement de limite : proposé seulement si le serveur le permet à l'utilisateur.
          setOverrideAllowed(
            e instanceof ApiError &&
              e.code === 'credit_limit_exceeded' &&
              e.extra.override_allowed === true,
          );
          setExpired(expiredShortages(e));
        },
      },
    );
  };

  const cashStatus = () => {
    if (!cashVisible || !cashEnabled || !siteId || !openSessions.data) return null;
    const open = openSessions.data.items;
    return open.length === 0 ? (
      <StatusBadge tone="warning" icon="pi pi-box" label={t('pos.noCashOpen')} />
    ) : (
      <StatusBadge
        tone="success"
        icon="pi pi-box"
        label={t('pos.cashOpen', { names: open.map((s) => s.cash_register_name).join(', ') })}
      />
    );
  };

  return (
    <div className="sm-pos">
      <header className="sm-pos-header">
        <h1>{t('pos.title')}</h1>
        <div className="sm-tags">
          <StatusBadge tone="neutral" icon="pi pi-user" label={capabilities.user.full_name} />
          {selectedSite || sites.length === 1 ? (
            <StatusBadge tone="info" icon="pi pi-map-marker" label={siteName ?? ''} />
          ) : (
            <Dropdown
              value={chosenSite}
              onChange={(e) => {
                setChosenSite(e.value as string);
                reset();
              }}
              options={sites.map((s) => ({ value: s.id, label: s.name }))}
              placeholder={t('pos.chooseSite')}
              aria-label={t('layout.site')}
            />
          )}
          {cashStatus()}
          {can('sales.sale.view') && siteId && (
            <Button
              icon="pi pi-history"
              label={t('pos.recentSales')}
              text
              onClick={() => setDialog('recent')}
            />
          )}
        </div>
      </header>

      {!siteId ? (
        <EmptyState icon="pi pi-map-marker" title={t('pos.siteRequired')} />
      ) : (
        <>
          <div className="sm-pos-tabs" role="tablist" aria-label={t('pos.title')}>
            <Button
              role="tab"
              aria-selected={tab === 'catalog'}
              label={t('pos.articles')}
              outlined={tab !== 'catalog'}
              onClick={() => setTab('catalog')}
            />
            <Button
              role="tab"
              aria-selected={tab === 'cart'}
              label={t('pos.cartCount', { count: lines.length })}
              outlined={tab !== 'cart'}
              onClick={() => setTab('cart')}
            />
          </div>
          <div className="sm-pos-body" data-tab={tab}>
            <section className="sm-pos-catalog" aria-labelledby="pos-catalog-title">
              <h2 id="pos-catalog-title" className="sm-sr-only">
                {t('pos.articles')}
              </h2>
              <IconField iconPosition="left" className="sm-pos-search">
                <InputIcon className="pi pi-search" />
                <InputText
                  id="pos-search"
                  type="search"
                  value={search}
                  placeholder={t('pos.searchPlaceholder')}
                  aria-label={t('pos.search')}
                  aria-keyshortcuts="F2"
                  autoFocus
                  onChange={(e) => {
                    setSearch(e.target.value);
                    setScanError(null);
                  }}
                  onKeyDown={(e) => {
                    if (e.key !== 'Enter') return;
                    e.preventDefault();
                    void scan(e.currentTarget.value);
                  }}
                />
              </IconField>
              {scanError && (
                <Message
                  severity="warn"
                  text={scanError}
                  role="alert"
                  className="sm-pos-scan-error"
                />
              )}
              {articles.isPending ? (
                <LoadingState />
              ) : articles.isError ? (
                <ErrorMessage error={articles.error} onRetry={() => void articles.refetch()} />
              ) : articles.data.length === 0 ? (
                <EmptyState icon="pi pi-search" title={t('common.noResults')} />
              ) : (
                <ul className="sm-pos-tiles" aria-label={t('pos.results')}>
                  {articles.data.map((a) => {
                    const out = !/[1-9]/.test(a.quantity) || a.quantity.startsWith('-');
                    return (
                      <li key={a.article_id}>
                        <button
                          type="button"
                          className="sm-pos-tile"
                          disabled={!a.is_active}
                          aria-label={t('pos.addArticle', { name: a.designation })}
                          onClick={() => add(a)}
                        >
                          <span className="sm-pos-tile-name">{a.designation}</span>
                          <span className="sm-muted">{a.reference}</span>
                          <span className="sm-pos-tile-price">{`${money(a.sale_price)} / ${a.unit}`}</span>
                          {a.packagings.length > 0 && (
                            <span className="sm-muted">
                              {t('pos.packagingsAvailable', { count: a.packagings.length })}
                            </span>
                          )}
                          {!a.is_active ? (
                            <StatusBadge tone="neutral" label={t('common.inactive')} />
                          ) : !a.stock_managed ? (
                            <StatusBadge tone="info" label={t('pos.notStockManaged')} />
                          ) : (
                            <StatusBadge
                              tone={out ? 'danger' : 'success'}
                              label={
                                out
                                  ? t('pos.outOfStock')
                                  : t('pos.inStock', {
                                      quantity: formatQuantity(a.quantity, locale),
                                      unit: a.unit,
                                    })
                              }
                            />
                          )}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>

            <section className="sm-pos-cart" aria-labelledby="pos-cart-title">
              <div className="sm-section-header">
                <h2 id="pos-cart-title">{t('pos.cart')}</h2>
                {lines.length > 0 && (
                  <Button
                    icon="pi pi-trash"
                    label={t('pos.clear')}
                    text
                    severity="secondary"
                    onClick={reset}
                  />
                )}
              </div>
              <button
                type="button"
                className="sm-pos-customer"
                aria-keyshortcuts="F4"
                onClick={() => setDialog('customer')}
              >
                <i className="pi pi-user" aria-hidden />
                <span>
                  {customer ? `${customer.name} (${customer.code})` : t('sales.anonymous')}
                </span>
                <span className="sm-muted">F4</span>
              </button>
              {lines.length === 0 ? (
                <EmptyState icon="pi pi-shopping-cart" title={t('pos.emptyCart')} />
              ) : (
                <ul className="sm-pos-lines" aria-label={t('pos.cart')}>
                  {lines.map((l) => {
                    const lt = lineTotal(l);
                    const key = lineKey(l);
                    const name = l.article.designation;
                    const presentation = l.packaging?.name ?? l.article.unit;
                    const base = baseQuantity(l);
                    const quantityOk = lineQuantity(l) !== null;
                    return (
                      <li key={key} className="sm-pos-line">
                        <div className="sm-pos-line-info">
                          <strong>{name}</strong>
                          {l.article.packagings.length > 0 ? (
                            <Dropdown
                              value={l.packaging?.id ?? BASE_UNIT}
                              options={[
                                {
                                  value: BASE_UNIT,
                                  label: `${l.article.unit} · ${money(l.article.sale_price)}`,
                                },
                                ...l.article.packagings.map((p) => ({
                                  value: p.id,
                                  label: `${p.name} (${formatQuantity(p.conversion, locale)} ${l.article.unit}) · ${money(p.sale_price)}`,
                                })),
                              ]}
                              aria-label={t('pos.presentationOf', { name })}
                              onChange={(e) => {
                                const value = e.value as string;
                                dispatch({
                                  type: 'packaging',
                                  key,
                                  packagingId: value === BASE_UNIT ? null : value,
                                });
                                setPayments([]);
                              }}
                            />
                          ) : null}
                          <span className="sm-muted">
                            {`${money(unitPrice(l))} / ${presentation}`}
                          </span>
                          {l.packaging && base && (
                            <span className="sm-muted" data-testid="pos-base-quantity">
                              {t('pos.baseQuantity', {
                                quantity: formatQuantity(base, locale),
                                unit: l.article.unit,
                              })}
                            </span>
                          )}
                          {!quantityOk && !l.article.decimal_quantity_allowed && (
                            <small className="p-error">{t('pos.wholeQuantityOnly')}</small>
                          )}
                          {quantityOk && exceedsStock(lines, l) && (
                            <small className="sm-text-warning">{t('pos.stockShort')}</small>
                          )}
                        </div>
                        <div className="sm-pos-qty">
                          <Button
                            icon="pi pi-minus"
                            rounded
                            outlined
                            aria-label={t('pos.decrease', { name })}
                            onClick={() => {
                              dispatch({ type: 'step', key, delta: -1 });
                              setPayments([]);
                            }}
                          />
                          <InputText
                            value={l.quantity}
                            inputMode={l.article.decimal_quantity_allowed ? 'decimal' : 'numeric'}
                            aria-label={t('pos.quantityOf', {
                              name: l.packaging ? `${name} (${presentation})` : name,
                            })}
                            invalid={!quantityOk}
                            onChange={(e) => {
                              dispatch({ type: 'set', key, quantity: e.target.value });
                              setPayments([]);
                            }}
                          />
                          <Button
                            icon="pi pi-plus"
                            rounded
                            outlined
                            aria-label={t('pos.increase', { name })}
                            onClick={() => {
                              dispatch({ type: 'step', key, delta: 1 });
                              setPayments([]);
                            }}
                          />
                        </div>
                        <span className="sm-num sm-strong">{lt ? money(lt) : '—'}</span>
                        <Button
                          icon="pi pi-times"
                          text
                          severity="danger"
                          aria-label={t('pos.removeLine', { name })}
                          onClick={() => {
                            dispatch({ type: 'remove', key });
                            setPayments([]);
                          }}
                        />
                      </li>
                    );
                  })}
                </ul>
              )}
              <dl className="sm-pos-summary" aria-label={t('pos.totals')}>
                <div>
                  <dt>{t('pos.subtotal')}</dt>
                  <dd>{money(total)}</dd>
                </div>
                <div className="sm-pos-grand-total">
                  <dt>{t('pos.total')}</dt>
                  <dd data-testid="pos-total">{money(total)}</dd>
                </div>
                {payments.map((p) => (
                  <div key={p.key}>
                    <dt>{p.label}</dt>
                    <dd>{money(p.amount)}</dd>
                  </div>
                ))}
                {payments.length > 0 && (
                  <div>
                    <dt>{t('pos.remaining')}</dt>
                    <dd>{money(remaining.startsWith('-') ? '0' : remaining)}</dd>
                  </div>
                )}
              </dl>
              <p className="sm-help">{t('pos.indicative')}</p>
              {!canSell && <Message severity="warn" text={t('pos.cannotSell')} />}
              <div className="sm-pos-actions">
                {canPay && (
                  <Button
                    icon="pi pi-wallet"
                    label={t('pos.pay')}
                    outlined
                    aria-keyshortcuts="F8"
                    disabled={lines.length === 0}
                    onClick={() => setDialog('payment')}
                  />
                )}
                <Button
                  icon="pi pi-check"
                  label={t('pos.validate')}
                  aria-keyshortcuts="F10"
                  disabled={lines.length === 0 || !canSell || invalidLines}
                  onClick={() => setDialog('confirm')}
                />
              </div>
            </section>
          </div>
        </>
      )}

      {dialog === 'customer' && (
        <PosCustomerDialog
          onSelect={(c) => {
            setCustomer(c);
            setDialog(null);
          }}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === 'payment' && siteId && (
        <PosPaymentDialog
          total={total}
          siteId={siteId}
          initial={payments}
          cashRegisterId={cashRegisterId}
          onSave={(saved, register) => {
            setPayments(saved);
            setCashRegisterId(register);
            setDialog(null);
          }}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === 'recent' && siteId && (
        <RecentSalesDialog siteId={siteId} onClose={() => setDialog(null)} />
      )}
      {dialog === 'confirm' && (
        <Dialog
          header={t('pos.confirmTitle')}
          visible
          onHide={() => setDialog(null)}
          className="sm-dialog"
        >
          <div className="sm-form">
            <dl className="sm-pos-summary" aria-label={t('pos.confirmSummary')}>
              <div>
                <dt>{t('pos.customer')}</dt>
                <dd>{customer ? customer.name : t('sales.anonymousShort')}</dd>
              </div>
              <div>
                <dt>{t('pos.articleCount')}</dt>
                <dd>{lines.length}</dd>
              </div>
              <div className="sm-pos-grand-total">
                <dt>{t('pos.total')}</dt>
                <dd>{money(total)}</dd>
              </div>
              <div>
                <dt>{t('pos.paid')}</dt>
                <dd>{money(paid)}</dd>
              </div>
              <div>
                <dt>{t('pos.remaining')}</dt>
                <dd data-testid="confirm-remaining">
                  {money(remaining.startsWith('-') ? '0' : remaining)}
                </dd>
              </div>
            </dl>
            {/[1-9]/.test(remaining) && !remaining.startsWith('-') && (
              <Message
                severity="warn"
                text={t(customer ? 'pos.creditNotice' : 'pos.unpaidWithoutCustomer')}
              />
            )}
            {error && <Message severity="error" text={error} />}
            {expired.length > 0 && (
              <ExpiredLotOverridePanel
                shortages={expired}
                idPrefix="pos"
                pending={checkout.isPending}
                onConfirm={(override) => confirm(false, override)}
              />
            )}
            {overrideAllowed && (
              <FormField
                id="pos-override-reason"
                label={t('sales.overrideReason')}
                help={t('sales.overrideReasonHelp')}
                required
              >
                <InputTextarea
                  id="pos-override-reason"
                  rows={3}
                  maxLength={500}
                  value={overrideReason}
                  onChange={(e) => setOverrideReason(e.target.value)}
                />
              </FormField>
            )}
            <div className="sm-dialog-actions">
              <Button
                type="button"
                label={t('actions.cancel')}
                text
                onClick={() => setDialog(null)}
              />
              {overrideAllowed && (
                <Button
                  type="button"
                  icon="pi pi-shield"
                  label={t('sales.overrideAndValidate')}
                  severity="warning"
                  disabled={overrideReason.trim().length < 5}
                  loading={checkout.isPending}
                  onClick={() => confirm(true)}
                />
              )}
              <Button
                type="button"
                icon="pi pi-check"
                label={t('pos.validate')}
                loading={checkout.isPending}
                autoFocus
                onClick={() => confirm()}
              />
            </div>
          </div>
        </Dialog>
      )}
      {result && (
        <PosReceipt
          result={result}
          onNewSale={reset}
          onOpenSale={
            can('sales.sale.view') ? () => void navigate(`/sales/${result.sale.id}`) : undefined
          }
        />
      )}
    </div>
  );
}
