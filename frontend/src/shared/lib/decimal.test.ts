import { describe, expect, it } from 'vitest';

import { formatCost, formatMoney, formatQuantity, normalizeDecimal } from './decimal';

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
