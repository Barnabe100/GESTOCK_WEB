import type { ConfiguredPaymentMethod, Payment, Sale } from './api';

/** Vente validée partiellement payée (données renvoyées par le serveur). */
export function saleFixture(over: Partial<Sale> = {}): Sale {
  return {
    id: 'v1',
    number: 'VENT-BOU-2026-000123',
    site_id: 's1',
    site_name: 'Boutique',
    customer_id: 'c1',
    customer_code: 'CLI-000001',
    customer_name: 'Awa Ouédraogo',
    status: 'VALIDATED',
    channel: 'BACKOFFICE',
    sale_date: '2026-09-24',
    notes: null,
    subtotal: '100000.00',
    total: '100000.00',
    line_count: 1,
    created_at: '2026-09-24T08:00:00Z',
    updated_at: '2026-09-24T08:00:00Z',
    created_by_name: 'Moussa',
    validated_at: '2026-09-24T08:05:00Z',
    validated_by_name: 'Moussa',
    cancelled_at: null,
    cancelled_by_name: null,
    cancellation_reason: null,
    paid_amount: '30000.00',
    remaining_amount: '70000.00',
    payment_status: 'PARTIALLY_PAID',
    is_credit: true,
    credit_status: 'PARTIAL',
    credit_override_at: null,
    credit_override_by_name: null,
    credit_override_reason: null,
    credit_override_amount: null,
    lines: [
      {
        id: 'l1',
        line_no: 1,
        article_id: 'a1',
        article_reference: 'CIM-50',
        article_designation: 'Ciment 50 kg',
        unit: 'sac',
        quantity: '10.000',
        unit_price: '10000.00',
        line_total: '100000.00',
      },
    ],
    ...over,
  };
}

export function paymentFixture(over: Partial<Payment> = {}): Payment {
  return {
    id: 'p1',
    number: 'PAY-000001',
    sale_id: 'v1',
    sale_number: 'VENT-BOU-2026-000123',
    site_id: 's1',
    amount: '10000.00',
    method: 'CASH',
    payment_method_id: 'pm1',
    method_label: 'Espèces',
    provider: null,
    amount_received: null,
    change_given: null,
    status: 'COMPLETED',
    reference: null,
    paid_at: '2026-09-24T09:00:00Z',
    created_at: '2026-09-24T09:00:00Z',
    created_by_name: 'Moussa',
    cancelled_at: null,
    cancelled_by_name: null,
    cancellation_reason: null,
    ...over,
  };
}

export function paymentMethodFixture(
  over: Partial<ConfiguredPaymentMethod> = {},
): ConfiguredPaymentMethod {
  return {
    id: 'pm1',
    label: 'Espèces',
    kind: 'CASH',
    integration_mode: 'MANUAL',
    reference_required: false,
    is_active: true,
    sort_order: 10,
    disabled_site_ids: [],
    available: true,
    ...over,
  };
}

/** Moyens configurés d'une entreprise : le libellé est libre, le type gouverne le comportement. */
export const PAYMENT_METHODS_FIXTURE: ConfiguredPaymentMethod[] = [
  paymentMethodFixture(),
  paymentMethodFixture({
    id: 'pm2',
    label: 'Orange Money',
    kind: 'MOBILE_MONEY',
    reference_required: true,
    sort_order: 20,
  }),
  paymentMethodFixture({ id: 'pm3', label: 'Carte bancaire', kind: 'CARD', sort_order: 30 }),
];
