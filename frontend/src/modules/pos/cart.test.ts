import { describe, expect, it } from 'vitest';

import type { PosArticle } from './api';
import { cartReducer, cartTotal, lineTotal, validQuantity, type CartLine } from './cart';

const article = (over: Partial<PosArticle> = {}): PosArticle => ({
  article_id: 'a1',
  reference: 'CIM-50',
  designation: 'Ciment 50 kg',
  unit: 'sac',
  category_name: null,
  sale_price: '5500.00',
  quantity: '10.000',
  is_active: true,
  ...over,
});

describe('panier du point de vente', () => {
  it('un article une seule fois : un nouvel ajout augmente la quantité', () => {
    let lines: CartLine[] = [];
    lines = cartReducer(lines, { type: 'add', article: article() });
    lines = cartReducer(lines, { type: 'add', article: article() });
    lines = cartReducer(lines, { type: 'add', article: article({ article_id: 'a2' }) });
    expect(lines.map((l) => [l.article.article_id, l.quantity])).toEqual([
      ['a1', '2'],
      ['a2', '1'],
    ]);
  });

  it('article inactif : jamais ajouté', () => {
    expect(cartReducer([], { type: 'add', article: article({ is_active: false }) })).toEqual([]);
  });

  it('quantité : +/− par unité sans descendre sous 1, saisie libre, suppression', () => {
    let lines = cartReducer([], { type: 'add', article: article() });
    lines = cartReducer(lines, { type: 'step', articleId: 'a1', delta: -1 });
    expect(lines[0]?.quantity).toBe('1');
    lines = cartReducer(lines, { type: 'step', articleId: 'a1', delta: 1 });
    expect(lines[0]?.quantity).toBe('2');
    lines = cartReducer(lines, { type: 'set', articleId: 'a1', quantity: '2,5' });
    expect(validQuantity(lines[0]?.quantity ?? '')).toBe('2.5');
    expect(lines[0] && lineTotal(lines[0])).toBe('13750.00');
    lines = cartReducer(lines, { type: 'remove', articleId: 'a1' });
    expect(lines).toEqual([]);
  });

  it('totaux décimaux exacts (sans float) ; quantité invalide ignorée', () => {
    const lines: CartLine[] = [
      { article: article({ sale_price: '0.35' }), quantity: '0.333' },
      { article: article({ article_id: 'a2', sale_price: '1000.10' }), quantity: '3' },
      { article: article({ article_id: 'a3' }), quantity: '0' },
    ];
    expect(cartTotal(lines)).toBe('3000.42');
    expect(validQuantity('0')).toBeNull();
    expect(validQuantity('abc')).toBeNull();
    expect(validQuantity('1.2345')).toBeNull();
  });
});
