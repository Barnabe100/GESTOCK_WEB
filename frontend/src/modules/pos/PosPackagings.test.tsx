// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { Toast } from 'primereact/toast';
import type { ReactNode, RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { saleFixture } from '@/modules/sales/testData';
import { formatMoney } from '@/shared/lib/decimal';
import { jsonResponse, pageOf, renderWithCapabilities, SITES } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import type { PosArticle } from './api';
import PosPage from './PosPage';
import { PosReceipt } from './PosReceipt';

/** Lot 3-B : unité de base, quantités entières / décimales, conditionnements au POS. */
const money = (v: string) => formatMoney(v, 'XOF', 'fr').replace(/\s/g, ' ');
const text = (el: Element) => (el.textContent ?? '').replace(/\s/g, ' ');

const COCA: PosArticle = {
  article_id: 'a1',
  reference: 'COCA-33',
  designation: 'Coca-Cola',
  unit: 'pièce',
  category_name: 'Boissons',
  sale_price: '500.00',
  quantity: '50.000',
  is_active: true,
  stock_managed: true,
  decimal_quantity_allowed: false,
  // Seuls les conditionnements ACTIFS sont renvoyés par le serveur.
  packagings: [
    { id: 'p6', name: 'Pack 6', conversion: '6.000', sale_price: '2800.00' },
    { id: 'p24', name: 'Carton 24', conversion: '24.000', sale_price: '10500.00' },
  ],
};
const RICE: PosArticle = {
  article_id: 'a2',
  reference: 'RIZ',
  designation: 'Riz parfumé',
  unit: 'kg',
  category_name: 'Épicerie',
  sale_price: '700.00',
  quantity: '100.000',
  is_active: true,
  stock_managed: true,
  decimal_quantity_allowed: true,
  packagings: [{ id: 's', name: 'Sac 25,5 kg', conversion: '25.500', sale_price: '17000.00' }],
};

const SELLER = [
  'pos.terminal.use',
  'sales.sale.view',
  'sales.sale.create',
  'sales.sale.validate',
  'sales.payment.create',
];

function withToast(element: ReactNode) {
  const ref = { current: { show: vi.fn() } as unknown as Toast } as RefObject<Toast | null>;
  return <ToastContext.Provider value={ref}>{element}</ToastContext.Provider>;
}

const fetchMock = vi.fn<typeof fetch>();
const checkoutBodies = () =>
  fetchMock.mock.calls
    .filter(([url, init]) => init?.method === 'POST' && String(url).includes('/pos/checkout'))
    .map(([, init]) => JSON.parse(String(init?.body)) as { lines: unknown[] });

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockImplementation(async (url, init) => {
    const u = String(url);
    if (init?.method === 'POST') {
      return jsonResponse({ replayed: false, sale: saleFixture(), payments: [] }, 201);
    }
    if (u.includes('/pos/articles')) return jsonResponse([COCA, RICE]);
    return pageOf([]);
  });
});
afterEach(() => {
  cleanup();
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

const render = () =>
  renderWithCapabilities(withToast(<PosPage />), {
    permissions: SELLER,
    sites: [SITES[0] as (typeof SITES)[number]],
    path: '/pos',
    route: '/pos',
  });

const chooseOption = async (label: string, option: string) => {
  const field = screen.getByLabelText(label, { selector: 'input, select, span, div' });
  fireEvent.click(field.closest('.p-dropdown') ?? field);
  const options = await screen.findAllByRole('option', { name: new RegExp(option), hidden: true });
  fireEvent.click(options.at(-1) as Element);
};

describe('point de vente : unité de base et conditionnements (Lot 3-B)', () => {
  it('unité de base proposée par défaut ; conditionnements actifs au choix ; prix et total', async () => {
    render();
    const tile = await screen.findByRole('button', { name: 'Ajouter Coca-Cola au panier' });
    expect(text(tile)).toContain(`${money('500')} / pièce`);
    expect(text(tile)).toContain('+ 2 conditionnements');
    fireEvent.click(tile);
    expect(text(screen.getByTestId('pos-total'))).toBe(money('500'));
    // Choix du carton : prix du conditionnement, quantité de base, total recalculés.
    await chooseOption('Présentation de Coca-Cola', 'Carton 24');
    fireEvent.change(screen.getByLabelText('Quantité de Coca-Cola (Carton 24)'), {
      target: { value: '2' },
    });
    expect(text(screen.getByTestId('pos-total'))).toBe(money('21000'));
    expect(text(screen.getByTestId('pos-base-quantity'))).toBe('Soit 48 pièce');
    // L'unité de base reste disponible : ajout d'une pièce sur une seconde ligne.
    fireEvent.click(tile);
    const cart = screen.getByRole('list', { name: 'Panier' });
    expect(within(cart).getAllByRole('listitem')).toHaveLength(2);
    expect(text(screen.getByTestId('pos-total'))).toBe(money('21500'));
    // Le serveur reçoit la présentation choisie (jamais de prix).
    fireEvent.click(screen.getByRole('button', { name: 'Valider la vente (F10)' }));
    const confirm = await screen.findByRole('dialog', { name: 'Valider la vente ?' });
    fireEvent.click(within(confirm).getByRole('button', { name: 'Valider la vente (F10)' }));
    await waitFor(() => expect(checkoutBodies()).toHaveLength(1));
    expect(checkoutBodies()[0]?.lines).toEqual([
      { article_id: 'a1', packaging_id: 'p24', quantity: '2' },
      { article_id: 'a1', packaging_id: null, quantity: '1' },
    ]);
    expect(JSON.stringify(checkoutBodies()[0])).not.toContain('price');
  });

  it('quantités entières : 2,5 refusé pour un article sans décimales (guidage)', async () => {
    render();
    fireEvent.click(await screen.findByRole('button', { name: 'Ajouter Coca-Cola au panier' }));
    fireEvent.change(screen.getByLabelText('Quantité de Coca-Cola'), {
      target: { value: '2,5' },
    });
    expect(screen.getByText('Quantité entière uniquement pour cet article')).toBeTruthy();
    expect(
      (screen.getByRole('button', { name: 'Valider la vente (F10)' }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    fireEvent.change(screen.getByLabelText('Quantité de Coca-Cola'), { target: { value: '3' } });
    expect(text(screen.getByTestId('pos-total'))).toBe(money('1500'));
  });

  it('quantités et conditionnement décimaux ; stock insuffisant signalé (indicatif)', async () => {
    render();
    fireEvent.click(await screen.findByRole('button', { name: 'Ajouter Riz parfumé au panier' }));
    fireEvent.change(screen.getByLabelText('Quantité de Riz parfumé'), {
      target: { value: '2,5' },
    });
    expect(text(screen.getByTestId('pos-total'))).toBe(money('1750'));
    await chooseOption('Présentation de Riz parfumé', 'Sac 25,5 kg');
    fireEvent.change(screen.getByLabelText('Quantité de Riz parfumé (Sac 25,5 kg)'), {
      target: { value: '1,5' },
    });
    expect(text(screen.getByTestId('pos-base-quantity'))).toBe('Soit 38,25 kg');
    expect(text(screen.getByTestId('pos-total'))).toBe(money('25500'));
    fireEvent.change(screen.getByLabelText('Quantité de Riz parfumé (Sac 25,5 kg)'), {
      target: { value: '4' },
    });
    expect(screen.getByText('Stock du site insuffisant (indicatif)')).toBeTruthy();
  });

  it('reçu : présentation vendue, prix unitaire figé et total', () => {
    renderWithCapabilities(
      <PosReceipt
        result={{
          replayed: false,
          payments: [],
          sale: saleFixture({
            number: 'VENT-BOU-2026-000043',
            total: '22500.00',
            lines: [
              {
                id: 'l1',
                line_no: 1,
                article_id: 'a1',
                article_reference: 'COCA-33',
                article_designation: 'Coca-Cola',
                unit: 'pièce',
                quantity: '2.000',
                unit_price: '10500.00',
                line_total: '21000.00',
                packaging_id: 'p24',
                packaging_name: 'Carton 24',
                packaging_conversion: '24.000',
                base_quantity: '48.000',
              },
              {
                id: 'l2',
                line_no: 2,
                article_id: 'a1',
                article_reference: 'COCA-33',
                article_designation: 'Coca-Cola',
                unit: 'pièce',
                quantity: '3.000',
                unit_price: '500.00',
                line_total: '1500.00',
                packaging_id: null,
                packaging_name: null,
                packaging_conversion: null,
                base_quantity: '3.000',
              },
            ],
          }),
        }}
        onNewSale={() => undefined}
      />,
      { permissions: SELLER },
    );
    const lines = screen.getAllByTestId('receipt-line').map(text);
    expect(lines[0]).toBe(`Coca-Cola2 Carton 24 × ${money('10500')}${money('21000')}`);
    expect(lines[1]).toBe(`Coca-Cola3 pièce × ${money('500')}${money('1500')}`);
  });
});
