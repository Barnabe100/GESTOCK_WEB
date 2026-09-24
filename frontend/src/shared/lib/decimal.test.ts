import { describe, expect, it } from 'vitest';

import {
  formatCost,
  formatMoney,
  formatQuantity,
  multiplyMoney,
  normalizeDecimal,
  sumMoney,
} from './decimal';

describe('saisie décimale', () => {
  it('normalise la virgule et les espaces', () => {
    expect(normalizeDecimal(' 1 500,5 ', 2)).toBe('1500.5');
    expect(normalizeDecimal('12', 2)).toBe('12');
  });

  it('refuse les valeurs invalides', () => {
    expect(normalizeDecimal('-1', 2)).toBeNull();
    expect(normalizeDecimal('1.234', 2)).toBeNull();
    expect(normalizeDecimal('abc', 3)).toBeNull();
    expect(normalizeDecimal('', 2)).toBeNull();
  });
});

describe('affichage', () => {
  it('affiche le franc CFA sans décimales', () => {
    expect(formatMoney('1500.00', 'XOF').replace(/\s/g, ' ')).toMatch(/^1 500 F\s?CFA$/);
  });

  it("n'arrondit jamais un montant avec centimes", () => {
    expect(formatMoney('2000.50', 'XOF').replace(/\s/g, ' ')).toMatch(/^2 000,50 F\s?CFA$/);
  });

  it('affiche une quantité sans zéros superflus', () => {
    expect(formatQuantity('10.000')).toBe('10');
    expect(formatQuantity('10.500')).toBe('10,5');
  });

  it('affiche un CMUP avec ses 4 décimales, sans arrondi à 2', () => {
    expect(formatCost('1500.1234', 'XOF').replace(/\s/g, ' ')).toMatch(/^1 500,1234 F\s?CFA$/);
    expect(formatCost('100.5000', 'XOF').replace(/\s/g, ' ')).toMatch(/^100,50 F\s?CFA$/);
    expect(formatCost('100.0000', 'XOF').replace(/\s/g, ' ')).toMatch(/^100 F\s?CFA$/);
    expect(formatCost(null, 'XOF')).toBe('');
  });
});

describe('calculs d’affichage (sans float)', () => {
  it('multiplie quantité × prix au centime, demi supérieur', () => {
    expect(multiplyMoney('2.5', '150')).toBe('375.00');
    expect(multiplyMoney('0.333', '0.35')).toBe('0.12');
    expect(multiplyMoney('3', '0.1')).toBe('0.30'); // 0.1 × 3 exact (pas 0.30000000000000004)
    expect(multiplyMoney('1.005', '1')).toBe('1.01');
  });

  it('additionne des montants', () => {
    expect(sumMoney(['375.00', '150.00', '0.12'])).toBe('525.12');
    expect(sumMoney([])).toBe('0.00');
  });
});
