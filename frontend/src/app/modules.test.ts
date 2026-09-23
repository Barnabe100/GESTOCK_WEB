import { describe, expect, it } from 'vitest';

import frCommon from '@/core/i18n/locales/fr/common.json';

import { FRONTEND_MODULES } from './modules';

function lookup(path: string): unknown {
  return path
    .split('.')
    .reduce<unknown>(
      (node, key) =>
        node && typeof node === 'object' ? (node as Record<string, unknown>)[key] : undefined,
      frCommon,
    );
}

describe('registre des modules frontend', () => {
  it('a des codes uniques', () => {
    const codes = FRONTEND_MODULES.map((m) => m.code);
    expect(new Set(codes).size).toBe(codes.length);
  });

  it('traduit chaque entrée de menu et chaque module', () => {
    for (const module of FRONTEND_MODULES) {
      expect((frCommon.modules as Record<string, string>)[module.code]).toBeTruthy();
      for (const item of module.navigation) {
        expect(typeof lookup(item.labelKey), item.labelKey).toBe('string');
        if (item.permission) {
          expect((frCommon.permissions as Record<string, string>)[item.permission]).toBeTruthy();
        }
      }
    }
  });

  it('protège chaque route de menu par la même permission que son entrée', () => {
    for (const module of FRONTEND_MODULES) {
      for (const item of module.navigation) {
        const route = module.routes.find(
          (r) => `/${r.path}` === item.path || (r.path === '' && item.path === '/'),
        );
        expect(route, item.path).toBeDefined();
        expect(route?.permission).toBe(item.permission);
      }
    }
  });
});
