import { describe, expect, it } from 'vitest';

import { healthSchema } from '@/core/health';

describe('healthSchema', () => {
  it('accepte une réponse valide du backend', () => {
    expect(healthSchema.parse({ status: 'ok', version: '0.1.0' })).toEqual({
      status: 'ok',
      version: '0.1.0',
    });
  });

  it('rejette une réponse incomplète', () => {
    expect(() => healthSchema.parse({ status: 'ok' })).toThrow();
  });
});
