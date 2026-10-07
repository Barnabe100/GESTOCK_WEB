import { describe, expect, it } from 'vitest';

import { cashSummary } from './change';

describe('monnaie rendue et reste dû (indicatifs)', () => {
  it('paiement exact : ni reste dû ni monnaie rendue', () => {
    expect(cashSummary('1500.00', ['1500'])).toEqual({
      total: '1500.00',
      received: '1500.00',
      remaining: '0.00',
      change: '0.00',
    });
  });

  it('paiement supérieur : monnaie rendue = montant reçu − total, reste dû 0', () => {
    expect(cashSummary('1500.00', ['5000'])).toEqual({
      total: '1500.00',
      received: '5000.00',
      remaining: '0.00',
      change: '3500.00',
    });
  });

  it('paiement inférieur : reste dû = total − montant reçu, monnaie rendue 0', () => {
    expect(cashSummary('1500.00', ['1000'])).toEqual({
      total: '1500.00',
      received: '1000.00',
      remaining: '500.00',
      change: '0.00',
    });
  });

  it('jamais un reste dû ET une monnaie rendue positifs à la fois', () => {
    for (const amounts of [[], ['0'], ['700', '800'], ['1499.99'], ['1500.01'], ['abc']]) {
      const { remaining, change } = cashSummary('1500.00', amounts);
      expect(/[1-9]/.test(remaining) && /[1-9]/.test(change)).toBe(false);
    }
  });

  it('plusieurs moyens : somme des montants remis (centimes exacts)', () => {
    expect(cashSummary('10000.00', ['6000', '4000.50'])).toMatchObject({
      received: '10000.50',
      change: '0.50',
    });
  });
});
