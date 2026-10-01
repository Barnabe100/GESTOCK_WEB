import { describe, expect, it } from 'vitest';

import type { PosArticle, PosPackaging } from './api';
import {
  baseQuantity,
  cartReducer,
  cartTotal,
  exceedsStock,
  lineKey,
  lineQuantity,
  lineTotal,
  unitPrice,
  validQuantity,
  type CartLine,
} from './cart';

const CARTON: PosPackaging = {
  id: 'p24',
  name: 'Carton 24',
  conversion: '24.000',
  sale_price: '10500.00',
};
const PACK: PosPackaging = { id: 'p6', name: 'Pack 6', conversion: '6.000', sale_price: '2800.00' };

const article = (over: Partial<PosArticle> = {}): PosArticle => ({
  article_id: 'a1',
  reference: 'CIM-50',
  designation: 'Ciment 50 kg',
  unit: 'sac',
  category_name: null,
  sale_price: '5500.00',
  stock_managed: true,
  quantity: '10.000',
  is_active: true,
  decimal_quantity_allowed: false,
  packagings: [],
  ...over,
});

const line = (over: Partial<CartLine> = {}): CartLine => ({
  article: article(),
  packaging: null,
  quantity: '1',
  ...over,
});

describe('panier du point de vente', () => {
  it('une ligne par présentation : un nouvel ajout augmente la quantité', () => {
    let lines: CartLine[] = [];
    lines = cartReducer(lines, { type: 'add', article: article() });
    lines = cartReducer(lines, { type: 'add', article: article() });
    lines = cartReducer(lines, { type: 'add', article: article({ article_id: 'a2' }) });
    expect(lines.map((l) => [lineKey(l), l.quantity])).toEqual([
      ['a1:base', '2'],
      ['a2:base', '1'],
    ]);
  });

  it('article inactif : jamais ajouté', () => {
    expect(cartReducer([], { type: 'add', article: article({ is_active: false }) })).toEqual([]);
  });

  it('quantité : +/− par unité sans descendre sous 1, saisie libre, suppression', () => {
    const decimal = article({ decimal_quantity_allowed: true });
    let lines = cartReducer([], { type: 'add', article: decimal });
    lines = cartReducer(lines, { type: 'step', key: 'a1:base', delta: -1 });
    expect(lines[0]?.quantity).toBe('1');
    lines = cartReducer(lines, { type: 'step', key: 'a1:base', delta: 1 });
    expect(lines[0]?.quantity).toBe('2');
    lines = cartReducer(lines, { type: 'set', key: 'a1:base', quantity: '2,5' });
    expect(validQuantity(lines[0]?.quantity ?? '')).toBe('2.5');
    expect(lines[0] && lineTotal(lines[0])).toBe('13750.00');
    lines = cartReducer(lines, { type: 'remove', key: 'a1:base' });
    expect(lines).toEqual([]);
  });

  it('totaux décimaux exacts (sans float) ; quantité invalide ignorée', () => {
    const decimal = { decimal_quantity_allowed: true };
    const lines: CartLine[] = [
      line({ article: article({ ...decimal, sale_price: '0.35' }), quantity: '0.333' }),
      line({ article: article({ article_id: 'a2', sale_price: '1000.10' }), quantity: '3' }),
      line({ article: article({ article_id: 'a3' }), quantity: '0' }),
    ];
    expect(cartTotal(lines)).toBe('3000.42');
    expect(validQuantity('0')).toBeNull();
    expect(validQuantity('abc')).toBeNull();
    expect(validQuantity('1.2345')).toBeNull();
  });

  it('quantités entières seulement pour un article sans décimales (guidage)', () => {
    expect(lineQuantity(line({ quantity: '2.5' }))).toBeNull();
    expect(lineQuantity(line({ quantity: '0.5' }))).toBeNull();
    expect(lineTotal(line({ quantity: '2.5' }))).toBeNull();
    expect(lineQuantity(line({ quantity: '10' }))).toBe('10');
    expect(lineQuantity(line({ quantity: '3.000' }))).toBe('3.000');
    const decimal = article({ unit: 'kg', decimal_quantity_allowed: true });
    expect(lineQuantity(line({ article: decimal, quantity: '2,5' }))).toBe('2.5');
  });
});

describe('conditionnements dans le panier (Lot 3-B)', () => {
  const coca = article({
    designation: 'Coca-Cola',
    unit: 'pièce',
    sale_price: '500.00',
    quantity: '50.000',
    packagings: [PACK, CARTON],
  });

  it('prix, quantité de base et total du conditionnement', () => {
    const carton = line({ article: coca, packaging: CARTON, quantity: '2' });
    expect(unitPrice(carton)).toBe('10500.00');
    expect(baseQuantity(carton)).toBe('48.000');
    expect(lineTotal(carton)).toBe('21000.00');
    // Unité de base toujours disponible, à son propre prix.
    const pieces = line({ article: coca, quantity: '3' });
    expect([unitPrice(pieces), baseQuantity(pieces), lineTotal(pieces)]).toEqual([
      '500.00',
      '3',
      '1500.00',
    ]);
    expect(cartTotal([carton, pieces])).toBe('22500.00');
  });

  it('changer de conditionnement recalcule ; même présentation déjà présente : fusion', () => {
    let lines = cartReducer([], { type: 'add', article: coca });
    lines = cartReducer(lines, { type: 'packaging', key: 'a1:base', packagingId: 'p24' });
    expect(lines.map((l) => [lineKey(l), lineTotal(l), baseQuantity(l)])).toEqual([
      ['a1:p24', '10500.00', '24.000'],
    ]);
    lines = cartReducer(lines, { type: 'add', article: coca });
    lines = cartReducer(lines, { type: 'set', key: 'a1:base', quantity: '2' });
    lines = cartReducer(lines, { type: 'packaging', key: 'a1:base', packagingId: 'p24' });
    expect(lines.map((l) => [lineKey(l), l.quantity])).toEqual([['a1:p24', '3']]);
    lines = cartReducer(lines, { type: 'packaging', key: 'a1:p24', packagingId: null });
    expect(lines.map((l) => [lineKey(l), lineTotal(l)])).toEqual([['a1:base', '1500.00']]);
  });

  it('conditionnement décimal ; fraction de conditionnement refusée pour un article entier', () => {
    const rice = article({
      unit: 'kg',
      decimal_quantity_allowed: true,
      packagings: [{ id: 's', name: 'Sac', conversion: '25.500', sale_price: '17000.00' }],
    });
    const bag = line({ article: rice, packaging: rice.packagings[0] ?? null, quantity: '1.5' });
    expect([baseQuantity(bag), lineTotal(bag)]).toEqual(['38.250', '25500.00']);
    expect(lineQuantity(line({ article: coca, packaging: CARTON, quantity: '1.5' }))).toBeNull();
    // Quantité de base au-delà de 3 décimales : invalide (le serveur la refuse, sans arrondi).
    const dose = { id: 'd', name: 'Dose', conversion: '0.125', sale_price: '100.00' };
    expect(lineQuantity(line({ article: rice, packaging: dose, quantity: '0.001' }))).toBeNull();
    expect(lineQuantity(line({ article: rice, packaging: dose, quantity: '0.008' }))).toBe('0.008');
  });

  it('disponibilité indicative en unité de base, toutes présentations confondues', () => {
    const cartons = line({ article: coca, packaging: CARTON, quantity: '2' }); // 48
    const pieces = line({ article: coca, quantity: '2' }); // 2 → 50 : disponible
    expect(exceedsStock([cartons, pieces], cartons)).toBe(false);
    const more = { ...pieces, quantity: '3' }; // 51 > 50
    expect(exceedsStock([cartons, more], more)).toBe(true);
    const unmanaged = line({ article: article({ stock_managed: false, quantity: '0' }) });
    expect(exceedsStock([unmanaged], unmanaged)).toBe(false);
  });
});
