import { describe, expect, it } from 'vitest';

import frCommon from '@/core/i18n/locales/fr/common.json';

import { buildNavigation, groupNavigation } from '@/core/modules/registry';

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

  it('Créances : rubrique « Ventes et clients », après Clients, selon la permission', () => {
    const caps = {
      modules: ['sales', 'customers', 'receivables'].map((code) => ({ code, status: 'available' })),
      navigation: ['sales', 'customers', 'receivables'],
      permissions: ['sales.sale.view', 'customers.customer.view', 'receivables.receivable.view'],
    };
    const sales = groupNavigation(buildNavigation(FRONTEND_MODULES, caps)).find(
      (section) => section.group === 'sales',
    );
    expect(sales?.items.map((item) => item.key)).toEqual(['sales', 'customers', 'receivables']);
    const withoutPermission = buildNavigation(FRONTEND_MODULES, {
      ...caps,
      permissions: caps.permissions.filter((p) => !p.startsWith('receivables.')),
    });
    expect(withoutPermission.map((item) => item.key)).not.toContain('receivables');
    const withoutModule = buildNavigation(FRONTEND_MODULES, {
      ...caps,
      modules: caps.modules.filter((m) => m.code !== 'receivables'),
    });
    expect(withoutModule.map((item) => item.key)).not.toContain('receivables');
  });

  it('Caisse : rubrique dédiée, entrées selon les permissions', () => {
    const caps = {
      modules: [{ code: 'cash_register', status: 'available' }],
      navigation: ['cash_register'],
      permissions: ['cash_register.register.view', 'cash_register.session.view'],
    };
    const cash = groupNavigation(buildNavigation(FRONTEND_MODULES, caps)).find(
      (section) => section.group === 'cash',
    );
    expect(cash?.items.map((item) => item.key)).toEqual([
      'cash-registers',
      'cash-sessions',
      'cash-journal',
    ]);
    const sessionsOnly = buildNavigation(FRONTEND_MODULES, {
      ...caps,
      permissions: ['cash_register.session.view'],
    });
    expect(sessionsOnly.map((item) => item.key)).toEqual(['cash-sessions', 'cash-journal']);
    expect(buildNavigation(FRONTEND_MODULES, { ...caps, modules: [] })).toEqual([]);
  });
});

describe('aucune condition par secteur ou par profil (Phase 3.1)', () => {
  const sources = import.meta.glob<string>(
    ['/src/**/*.{ts,tsx}', '!/src/**/*.test.{ts,tsx}', '!/src/shared/testing.tsx'],
    {
      query: '?raw',
      import: 'default',
      eager: true,
    },
  );
  const profileCodes = Object.entries(frCommon.businessProfiles).flatMap(([sector, profiles]) =>
    Object.keys(profiles).map((name) => `${sector}.${name}`),
  );

  it('le code applicatif ne compare jamais un code de profil ou de secteur', () => {
    const offenders = Object.entries(sources)
      .filter(
        ([, source]) =>
          /(profile|sector|business_?[Pp]rofile_?[Cc]ode|businessType)(\.code)?\s*[!=]==?\s*['"]/.test(
            source,
          ) || profileCodes.some((code) => source.includes(`'${code}'`)),
      )
      .map(([path]) => path);
    expect(offenders).toEqual([]);
  });

  it('chaque profil du catalogue a un libellé traduit', () => {
    expect(profileCodes.length).toBeGreaterThanOrEqual(28);
    expect(Object.keys(frCommon.sectors)).toEqual([
      'retail',
      'restaurant',
      'automobile',
      'distribution',
    ]);
  });
});
