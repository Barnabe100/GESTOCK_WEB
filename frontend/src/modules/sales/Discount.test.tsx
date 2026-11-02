// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import '@/core/i18n';
import { formatMoney, percentOfMoney } from '@/shared/lib/decimal';

import { discountAmount, discountedTotal, savedDiscount } from './discount';
import { DiscountDialog } from './DiscountDialog';

const money = (v: string) => formatMoney(v, 'XOF', 'fr').replace(/\s/g, ' ');
const text = (el: Element) => (el.textContent ?? '').replace(/\s/g, ' ');

afterEach(cleanup);

describe('remise : calcul indicatif (le serveur fait foi)', () => {
  it('pourcentage arrondi au centime demi supérieur, comme le serveur', () => {
    expect(percentOfMoney('150.00', '33.33')).toBe('50.00'); // 49,995
    expect(percentOfMoney('1500', '10')).toBe('150.00');
    expect(percentOfMoney('0.10', '50')).toBe('0.05');
  });

  it('montant ou pourcentage, borné au sous-total ; sans remise : total inchangé', () => {
    expect(discountAmount('1500.00', null)).toBe('0.00');
    expect(discountedTotal('1500.00', null)).toBe('1500.00');
    const amount = { mode: 'AMOUNT' as const, value: '200', reason: 'Fidèle' };
    expect(discountAmount('1500.00', amount)).toBe('200.00');
    expect(discountedTotal('1500.00', amount)).toBe('1300.00');
    const percent = { mode: 'PERCENT' as const, value: '10', reason: 'Fidèle' };
    expect(discountedTotal('300.00', percent)).toBe('270.00');
    expect(discountedTotal('100.00', { ...amount, value: '500' })).toBe('0.00');
  });

  it('remise enregistrée rechargée comme un montant', () => {
    expect(savedDiscount({ discount_amount: '0.00', discount_reason: null })).toBeNull();
    expect(savedDiscount({})).toBeNull();
    expect(savedDiscount({ discount_amount: '30.00', discount_reason: 'Geste' })).toEqual({
      mode: 'AMOUNT',
      value: '30.00',
      reason: 'Geste',
    });
  });
});

describe('dialogue de remise', () => {
  const open = (initial: Parameters<typeof DiscountDialog>[0]['initial'] = null) => {
    const onApply = vi.fn();
    render(
      <DiscountDialog
        subtotal="1500.00"
        currency="XOF"
        locale="fr"
        initial={initial}
        idPrefix="t"
        onApply={onApply}
        onClose={vi.fn()}
      />,
    );
    return onApply;
  };

  it('valeur et motif obligatoires ; remise supérieure au montant refusée', () => {
    const onApply = open();
    fireEvent.click(screen.getByRole('button', { name: 'Appliquer la remise' }));
    expect(onApply).not.toHaveBeenCalled();
    expect(screen.getByText('Saisissez une valeur supérieure à zéro.')).toBeTruthy();
    expect(screen.getByText('Le motif est obligatoire (3 caractères au moins).')).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/Montant de la remise/), {
      target: { value: '1600' },
    });
    fireEvent.change(screen.getByLabelText(/Motif de la remise/), {
      target: { value: 'Client fidèle' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Appliquer la remise' }));
    expect(onApply).not.toHaveBeenCalled();
    expect(
      screen.getByText('La remise ne peut pas dépasser le montant de la vente (ni 100 %).'),
    ).toBeTruthy();
  });

  it('pourcentage : aperçu calculé, remise transmise avec son motif', () => {
    const onApply = open();
    fireEvent.click(screen.getByText('Pourcentage'));
    fireEvent.change(screen.getByLabelText(/Pourcentage de remise/), {
      target: { value: '10,5' },
    });
    fireEvent.change(screen.getByLabelText(/Motif de la remise/), {
      target: { value: '  Geste commercial ' },
    });
    expect(text(screen.getByTestId('t-discount-preview'))).toContain(money('157.5'));
    fireEvent.click(screen.getByRole('button', { name: 'Appliquer la remise' }));
    expect(onApply).toHaveBeenCalledWith({
      mode: 'PERCENT',
      value: '10.5',
      reason: 'Geste commercial',
    });
  });

  it('remise existante : « Retirer la remise » transmet null', () => {
    const onApply = open({ mode: 'AMOUNT', value: '200.00', reason: 'Fidèle' });
    fireEvent.click(screen.getByRole('button', { name: 'Retirer la remise' }));
    expect(onApply).toHaveBeenCalledWith(null);
  });
});
