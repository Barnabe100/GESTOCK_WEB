import { describe, expect, it } from 'vitest';

import { equivalences, formatEquivalence, formatPresented, toBase } from './presentation';

const PACK = { id: 'p6', name: 'Pack 6', conversion: '6.000' };
const CARTON = { id: 'p24', name: 'Carton 24', conversion: '24.000' };

describe('présentations et équivalences (Lot 3-C)', () => {
  it('quantité de base = quantité × conversion, exacte, sans arrondi', () => {
    expect(toBase('2', '24.000')).toBe('48.000');
    expect(toBase('1.5', '25.500')).toBe('38.250');
    expect(toBase('0.001', '0.125')).toBeNull(); // > 3 décimales : refus, jamais d'arrondi
  });

  it('48 bouteilles = 8 packs = 2 cartons (du plus petit au plus grand)', () => {
    const result = equivalences('48', [CARTON, PACK]);
    expect(result.map((e) => formatEquivalence(e, 'bouteille'))).toEqual([
      '= 8 Pack 6',
      '= 2 Carton 24',
    ]);
  });

  it('reste en unité de base ; conditionnements plus grands que la quantité ignorés', () => {
    const result = equivalences('50', [PACK, CARTON]);
    expect(result.map((e) => formatEquivalence(e, 'bouteille'))).toEqual([
      '= 8 Pack 6 + 2 bouteille',
      '= 2 Carton 24 + 2 bouteille',
    ]);
    expect(equivalences('5', [PACK, CARTON])).toEqual([]);
    expect(equivalences('0', [PACK])).toEqual([]);
  });

  it('article décimal : quotient décimal exact (38,25 kg = 1,5 sac)', () => {
    const bag = { id: 's', name: 'Sac', conversion: '25.500' };
    expect(equivalences('38.25', [bag], true)).toEqual([
      { name: 'Sac', quantity: '1.500', remainder: null },
    ]);
    // Article entier : quotient entier + reste.
    expect(equivalences('38.25', [bag], false)[0]?.remainder).toBe('12.750');
  });

  it('pas plus de 3 équivalences affichées', () => {
    const many = ['2', '3', '4', '6'].map((c) => ({ id: c, name: `Lot ${c}`, conversion: c }));
    expect(equivalences('24', many)).toHaveLength(3);
  });

  it('présentation saisie → unité de base', () => {
    expect(formatPresented('2.000', 'Carton 24', '48.000', 'bouteille')).toBe(
      '2 Carton 24 = 48 bouteille',
    );
    expect(formatPresented('3.000', null, '3.000', 'bouteille')).toBe('3 bouteille');
  });
});
