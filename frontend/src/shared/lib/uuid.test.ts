import { afterEach, describe, expect, it, vi } from 'vitest';

import { newUuid } from './uuid';

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('newUuid', () => {
  it('utilise crypto.randomUUID quand le contexte est sécurisé', () => {
    const randomUUID = vi.fn(() => '11111111-1111-4111-8111-111111111111');
    vi.stubGlobal('crypto', { randomUUID, getRandomValues: vi.fn() });
    expect(newUuid()).toBe('11111111-1111-4111-8111-111111111111');
    expect(randomUUID).toHaveBeenCalledOnce();
  });

  it('HTTP sur le réseau local (sans randomUUID) : UUID v4 tiré de getRandomValues', () => {
    const real = globalThis.crypto;
    const getRandomValues = vi.fn(<T extends ArrayBufferView>(array: T) =>
      real.getRandomValues(array as T & ArrayBufferView<ArrayBuffer>),
    );
    vi.stubGlobal('crypto', { getRandomValues });
    const keys = new Set(Array.from({ length: 200 }, () => newUuid()));
    expect(getRandomValues).toHaveBeenCalledTimes(200);
    expect(keys.size).toBe(200);
    for (const key of keys) expect(key).toMatch(UUID_V4);
  });

  it('pose les bits de version et de variante quel que soit l’aléa', () => {
    vi.stubGlobal('crypto', {
      getRandomValues: (array: Uint8Array) => array.fill(0xff),
    });
    expect(newUuid()).toBe('ffffffff-ffff-4fff-bfff-ffffffffffff');
    vi.stubGlobal('crypto', {
      getRandomValues: (array: Uint8Array) => array.fill(0),
    });
    expect(newUuid()).toBe('00000000-0000-4000-8000-000000000000');
  });

  it('refuse de produire une clé sans aléa cryptographique (jamais Math.random)', () => {
    vi.stubGlobal('crypto', undefined);
    expect(() => newUuid()).toThrow();
  });
});

describe('aucun appel direct à crypto.randomUUID dans l’interface', () => {
  it('toutes les clés passent par newUuid (fonctionne aussi en HTTP sur le réseau local)', () => {
    const sources = import.meta.glob<string>(
      ['/src/**/*.{ts,tsx}', '!/src/**/*.test.{ts,tsx}', '!/src/shared/lib/uuid.ts'],
      { query: '?raw', import: 'default', eager: true },
    );
    expect(Object.keys(sources).length).toBeGreaterThan(100);
    const offenders = Object.entries(sources)
      .filter(([, source]) => /crypto\.randomUUID/.test(source))
      .map(([path]) => path);
    expect(offenders).toEqual([]);
  });
});
