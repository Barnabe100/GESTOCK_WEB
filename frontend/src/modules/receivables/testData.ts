import type { CreditExposure, Receivable } from './api';

export const VIEW = ['receivables.receivable.view', 'sales.sale.view'];

export function receivable(overrides: Partial<Receivable> = {}): Receivable {
  return {
    sale_id: 'v1',
    sale_number: 'VTE-000001',
    sale_date: '2026-09-20',
    validated_at: '2026-09-20T08:00:00Z',
    site_id: 's1',
    site_name: 'Boutique',
    customer_id: 'c1',
    customer_code: 'CLI-000001',
    customer_name: 'Awa Traoré',
    customer_is_active: true,
    total: '100000.00',
    paid_amount: '40000.00',
    remaining_amount: '60000.00',
    payment_status: 'PARTIALLY_PAID',
    ...overrides,
  };
}

export function exposure(overrides: Partial<CreditExposure> = {}): CreditExposure {
  return {
    customer_id: 'c1',
    customer_code: 'CLI-000001',
    customer_name: 'Awa Traoré',
    customer_is_active: true,
    credit_limit: '500000.00',
    limit_configured: true,
    current_exposure: '320000.00',
    available_credit: '180000.00',
    over_limit: false,
    open_receivables_count: 2,
    consolidated: true,
    ...overrides,
  };
}
