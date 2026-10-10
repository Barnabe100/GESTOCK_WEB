// @vitest-environment jsdom
import {
  act,
  cleanup,
  configure,
  fireEvent,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { formatMoney } from '@/shared/lib/decimal';
import { jsonResponse, renderWithCapabilities } from '@/shared/testing';

import type { Receipt } from './api';
import { DEFAULT_RECEIPT_FORMAT, RECEIPT_FORMATS } from './formats';
import { ReceiptDialog, SaleReceiptActions } from './ReceiptDialog';
import { ReceiptPrinter } from './ReceiptPrinter';
import { SaleReceipt } from './SaleReceipt';

const money = (v: string) => formatMoney(v, 'XOF', 'fr').replace(/\s+/g, ' ');
const text = (el: Element | null) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim();

const receipt = (over: Partial<Receipt> = {}): Receipt => ({
  sale_id: 'v1',
  number: 'VENT-BOU-2026-000042',
  site_name: 'Boutique',
  issued_at: '2026-10-07T14:30:00Z',
  cashier_name: 'Awa Traoré',
  customer_name: null,
  issuer: {
    name: 'Quincaillerie du Centre SARL',
    trade_name: 'Quincaillerie du Centre',
    logo_url: null,
    contact: [{ kind: 'phone', value: '+226 70 00 00 00' }],
    identifiers: [{ kind: 'tax_id', value: '00012345A' }],
  },
  lines: [
    {
      designation: 'Coca-Cola 33 cl',
      unit: 'bouteille',
      quantity: '2.000',
      packaging_name: 'Carton 24',
      packaging_conversion: '24.000',
      unit_price: '10500.00',
      line_total: '21000.00',
    },
    {
      designation: 'Pain',
      unit: 'u',
      quantity: '1.000',
      packaging_name: null,
      packaging_conversion: null,
      unit_price: '1500.00',
      line_total: '1500.00',
    },
  ],
  total: '22500.00',
  paid_amount: '22500.00',
  remaining_amount: '0.00',
  payment_status: 'PAID',
  is_credit: false,
  payments: [
    {
      method: 'CASH',
      method_label: 'Espèces',
      amount: '22500.00',
      amount_received: '25000.00',
      change_given: '2500.00',
      paid_at: '2026-10-07T14:30:00Z',
    },
  ],
  amount_received: '25000.00',
  change_given: '2500.00',
  print_count: 0,
  ...over,
});

const fetchMock = vi.fn<typeof fetch>();
const printMock = vi.fn();
const VIEW = ['sales.sale.view'];

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
  vi.stubGlobal('print', printMock);
});

afterEach(() => {
  cleanup();
  fetchMock.mockReset();
  printMock.mockReset();
  vi.unstubAllGlobals();
});

describe('reçu de vente (80 mm)', () => {
  it('contenu : en-tête, vente, lignes telles que vendues, total, paiements, monnaie rendue', () => {
    renderWithCapabilities(<SaleReceipt receipt={receipt()} />, { permissions: VIEW });
    const article = screen.getByTestId('sale-receipt');
    const content = text(article);
    expect(content).toContain('Quincaillerie du Centre');
    expect(content).toContain('Boutique');
    expect(text(screen.getByTestId('receipt-number'))).toBe('VENT-BOU-2026-000042');
    expect(content).toContain('Awa Traoré');
    // Présentation commerciale réellement vendue, prix figé du conditionnement.
    const [carton, pain] = screen.getAllByTestId('receipt-line');
    expect(text(carton ?? null)).toContain(`2 Carton 24 × ${money('10500')}`);
    expect(text(carton ?? null)).toContain(money('21000'));
    expect(text(pain ?? null)).toContain(`1 u × ${money('1500')}`);
    expect(text(screen.getByTestId('receipt-total'))).toBe(money('22500'));
    expect(text(screen.getByTestId('receipt-payment'))).toContain('Espèces');
    expect(text(screen.getByTestId('receipt-received'))).toBe(money('25000'));
    // Libellé exact « Monnaie rendue » (jamais « Monnaie » seul).
    const change = screen.getByTestId('receipt-change');
    expect(text(change.previousElementSibling)).toBe('Monnaie rendue');
    expect(text(change)).toBe(money('2500'));
    expect(text(screen.getByTestId('receipt-remaining'))).toBe(money('0'));
    expect(screen.queryByTestId('receipt-credit')).toBeNull();
    expect(content).toContain('Merci pour votre confiance. À très bientôt !');
    // Aucune donnée interne.
    expect(content).not.toMatch(/CMUP|coût|cost|lot/i);
  });

  it('lignes regroupées à des prix différents : prix moyen signalé, total de la ligne exact', () => {
    renderWithCapabilities(
      <SaleReceipt
        receipt={receipt({
          lines: [
            {
              designation: 'Poulet braisé',
              unit: 'portion',
              quantity: '3.000',
              packaging_name: null,
              packaging_conversion: null,
              unit_price: '733.33',
              line_total: '2200.00',
              weighted_unit_price: true,
            },
          ],
          total: '2200.00',
        })}
      />,
      { permissions: VIEW },
    );
    const [poulet] = screen.getAllByTestId('receipt-line');
    expect(text(poulet ?? null)).toContain(`3 portion × ${money('733.33')} (prix moyen)`);
    expect(text(poulet ?? null)).toContain(money('2200'));
  });

  it('vente à crédit : reste dû et mention du crédit, monnaie rendue nulle', () => {
    renderWithCapabilities(
      <SaleReceipt
        receipt={receipt({
          customer_name: 'Client comptoir',
          paid_amount: '10000.00',
          remaining_amount: '12500.00',
          payment_status: 'PARTIALLY_PAID',
          is_credit: true,
          amount_received: '10000.00',
          change_given: '0.00',
          payments: [
            {
              method: 'CASH',
              method_label: 'Espèces',
              amount: '10000.00',
              amount_received: '10000.00',
              change_given: '0.00',
              paid_at: '2026-10-07T14:30:00Z',
            },
          ],
        })}
      />,
      { permissions: VIEW },
    );
    expect(text(screen.getByTestId('sale-receipt'))).toContain('Client comptoir');
    expect(text(screen.getByTestId('receipt-remaining'))).toBe(money('12500'));
    expect(text(screen.getByTestId('receipt-change'))).toBe(money('0'));
    expect(text(screen.getByTestId('receipt-credit'))).toBe(
      `Vente à crédit — reste dû : ${money('12500')}`,
    );
  });

  it('sans détail des paiements (permission) : ni montant reçu ni monnaie rendue', () => {
    renderWithCapabilities(
      <SaleReceipt
        receipt={receipt({ payments: null, amount_received: null, change_given: null })}
      />,
      { permissions: VIEW },
    );
    expect(screen.queryByTestId('receipt-payment')).toBeNull();
    expect(screen.queryByTestId('receipt-received')).toBeNull();
    expect(screen.queryByTestId('receipt-change')).toBeNull();
    expect(text(screen.getByTestId('receipt-remaining'))).toBe(money('0'));
  });

  it('logo du tenant : en haut du reçu, avant les informations de vente, nom conservé', () => {
    const withLogo = receipt();
    withLogo.issuer = { ...withLogo.issuer, logo_url: 'https://cdn.example.com/logo.png' };
    renderWithCapabilities(<SaleReceipt receipt={withLogo} />, { permissions: VIEW });
    const article = screen.getByTestId('sale-receipt');
    const logo = article.querySelector('img.sm-receipt-logo') as HTMLImageElement;
    expect(logo.getAttribute('src')).toBe('https://cdn.example.com/logo.png');
    // Premier élément de l'en-tête, avant le nom et avant le numéro de vente.
    expect(article.querySelector('header')?.firstElementChild).toBe(logo);
    const number = screen.getByTestId('receipt-number');
    expect(logo.compareDocumentPosition(number) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(text(article.querySelector('.sm-receipt-issuer'))).toBe('Quincaillerie du Centre');
  });

  it('sans logo : aucune image, reçu complet', () => {
    renderWithCapabilities(<SaleReceipt receipt={receipt()} />, { permissions: VIEW });
    const article = screen.getByTestId('sale-receipt');
    expect(article.querySelector('img')).toBeNull();
    expect(text(article.querySelector('.sm-receipt-issuer'))).toBe('Quincaillerie du Centre');
    expect(text(screen.getByTestId('receipt-total'))).toBe(money('22500'));
  });

  it.each([
    ['chargé', 'load'],
    ['introuvable', 'error'],
  ])(
    'impression avec logo %s : attendue jusqu’à la fin du chargement, jamais bloquée',
    async (_label, event) => {
      const withLogo = receipt();
      withLogo.issuer = { ...withLogo.issuer, logo_url: 'https://cdn.example.com/logo.png' };
      fetchMock.mockImplementation(async (_url, init) =>
        jsonResponse(init?.method === 'POST' ? { ...withLogo, print_count: 1 } : withLogo),
      );
      let printedWithLogo: boolean | null = null;
      printMock.mockImplementation(() => {
        printedWithLogo = Boolean(document.querySelector('#sm-print-root img.sm-receipt-logo'));
      });
      renderWithCapabilities(<ReceiptDialog saleId="v1" onClose={() => undefined} />, {
        permissions: [...VIEW, 'sales.sale.receipt_print'],
      });
      const dialog = await screen.findByRole('dialog', { name: 'Reçu de vente' });
      await act(async () => {
        fireEvent.click(await within(dialog).findByRole('button', { name: 'Imprimer' }));
      });
      const logo = await waitFor(() => {
        const img = document.querySelector('#sm-print-root img.sm-receipt-logo');
        expect(img).not.toBeNull();
        return img as HTMLImageElement;
      });
      // Logo pas encore chargé : rien n'est envoyé à l'imprimante.
      expect(printMock).not.toHaveBeenCalled();
      await act(async () => {
        logo.dispatchEvent(new Event(event));
      });
      await waitFor(() => expect(printMock).toHaveBeenCalledTimes(1));
      expect(printedWithLogo).toBe(true);
    },
  );

  it('mode strict (double exécution des effets) : une seule impression, logo chargé', async () => {
    const withLogo = receipt();
    withLogo.issuer = { ...withLogo.issuer, logo_url: 'https://cdn.example.com/logo.png' };
    const onDone = vi.fn();
    // Comme `main.tsx` : racine en mode strict (effets exécutés, annulés puis ré-exécutés).
    configure({ reactStrictMode: true });
    try {
      renderWithCapabilities(<ReceiptPrinter receipt={withLogo} onDone={onDone} />, {
        permissions: VIEW,
      });
    } finally {
      configure({ reactStrictMode: false });
    }
    const logo = document.querySelector('#sm-print-root img.sm-receipt-logo') as HTMLImageElement;
    expect(logo).not.toBeNull();
    expect(printMock).not.toHaveBeenCalled();
    await act(async () => {
      logo.dispatchEvent(new Event('load'));
    });
    await waitFor(() => expect(printMock).toHaveBeenCalledTimes(1));
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it('format V1 : ticket thermique 80 mm seulement (58 mm, A4 non implémentés)', () => {
    expect(Object.keys(RECEIPT_FORMATS)).toEqual(['THERMAL_80']);
    expect(DEFAULT_RECEIPT_FORMAT.widthMm).toBe(80);
    expect(DEFAULT_RECEIPT_FORMAT.pageRule).toContain('size: 80mm auto');
    renderWithCapabilities(<SaleReceipt receipt={receipt()} />, { permissions: VIEW });
    const article = screen.getByTestId('sale-receipt');
    expect(article.dataset.format).toBe('THERMAL_80');
    expect(article.className).toContain('sm-receipt--thermal-80');
  });

  it('impression : autorisée et journalisée par le serveur, page 80 mm le temps de l’impression', async () => {
    let printed = 0;
    fetchMock.mockImplementation(async (url, init) => {
      if (init?.method === 'POST' && String(url).endsWith('/sales/v1/receipt/print')) {
        printed += 1;
        return jsonResponse(receipt({ print_count: printed }));
      }
      return jsonResponse(receipt({ print_count: printed }));
    });
    let duringPrint: { root: string; page: string | null; printing: boolean } | null = null;
    printMock.mockImplementation(() => {
      duringPrint = {
        root: text(document.getElementById('sm-print-root')),
        page: document.head.querySelector('style[data-receipt-format]')?.textContent ?? null,
        printing: document.body.classList.contains('sm-printing'),
      };
    });
    renderWithCapabilities(<ReceiptDialog saleId="v1" onClose={() => undefined} />, {
      permissions: [...VIEW, 'sales.sale.receipt_print', 'sales.sale.reprint'],
    });
    const dialog = await screen.findByRole('dialog', { name: 'Reçu de vente' });
    expect(await within(dialog).findByText('Reçu jamais imprimé.')).toBeTruthy();
    await act(async () => {
      fireEvent.click(within(dialog).getByRole('button', { name: 'Imprimer' }));
    });
    await waitFor(() => expect(printMock).toHaveBeenCalledTimes(1));
    expect(duringPrint).toMatchObject({
      page: '@page { size: 80mm auto; margin: 0; }',
      printing: true,
    });
    expect(duringPrint!.root).toContain('VENT-BOU-2026-000042');
    expect(duringPrint!.root).toContain('Monnaie rendue');
    // Après impression : plus de racine d'impression, de règle de page ni de classe.
    await waitFor(() => expect(document.getElementById('sm-print-root')).toBeNull());
    expect(document.head.querySelector('style[data-receipt-format]')).toBeNull();
    expect(document.body.classList.contains('sm-printing')).toBe(false);
    // Impression suivante : « Réimprimer ».
    expect(await within(dialog).findByRole('button', { name: 'Réimprimer' })).toBeTruthy();
    expect(within(dialog).getByText('Reçu imprimé 1 fois.')).toBeTruthy();
  });

  it('refus du serveur : message traduit, aucune impression', async () => {
    fetchMock.mockImplementation(async (_url, init) =>
      init?.method === 'POST'
        ? jsonResponse({ code: 'receipt_reprint_denied', status: 403 }, 403)
        : jsonResponse(receipt({ print_count: 1 })),
    );
    renderWithCapabilities(<ReceiptDialog saleId="v1" onClose={() => undefined} />, {
      permissions: [...VIEW, 'sales.sale.reprint'],
    });
    const dialog = await screen.findByRole('dialog', { name: 'Reçu de vente' });
    fireEvent.click(await within(dialog).findByRole('button', { name: 'Réimprimer' }));
    expect(
      await within(dialog).findByText(/vous n'avez pas le droit de le réimprimer/),
    ).toBeTruthy();
    expect(printMock).not.toHaveBeenCalled();
  });

  it('permissions : sans impression, consultation seule ; réimpression selon `reprint`', async () => {
    fetchMock.mockImplementation(async () => jsonResponse(receipt({ print_count: 1 })));
    renderWithCapabilities(<SaleReceiptActions saleId="v1" />, {
      permissions: [...VIEW, 'sales.sale.receipt_print'],
    });
    expect(await screen.findByRole('button', { name: 'Voir le reçu' })).toBeTruthy();
    // Déjà imprimé : la réimpression exige `sales.sale.reprint`.
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: 'Réimprimer' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Imprimer' })).toBeNull();
    cleanup();
    renderWithCapabilities(<SaleReceiptActions saleId="v1" />, {
      permissions: [...VIEW, 'sales.sale.reprint'],
    });
    expect(await screen.findByRole('button', { name: 'Réimprimer' })).toBeTruthy();
  });
});
