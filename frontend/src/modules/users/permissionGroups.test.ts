import { describe, expect, it } from 'vitest';

import type { Permission } from './api';
import { groupPermissions, normalizeSearch } from './permissionGroups';

const perm = (code: string, module: string, access = 'read'): Permission => {
  const [resource = '', action = ''] = code.slice(module.length + 1).split('.');
  return { code, module, access, resource, action };
};

const PERMISSIONS = [
  perm('stock.entry.view', 'stock'),
  perm('stock.entry.create', 'stock', 'write'),
  perm('stock.exit.view', 'stock'),
  perm('catalog.article.view', 'catalog'),
];

describe('regroupement des permissions', () => {
  it('regroupe par module puis ressource, dans l’ordre reçu', () => {
    const groups = groupPermissions(PERMISSIONS);
    expect(groups.map((g) => g.module)).toEqual(['stock', 'catalog']);
    expect(groups[0]?.resources.map((r) => [r.resource, r.permissions.length])).toEqual([
      ['entry', 2],
      ['exit', 1],
    ]);
  });

  it('filtre et supprime les groupes vides', () => {
    const groups = groupPermissions(PERMISSIONS, (p) => p.action === 'create');
    expect(groups).toHaveLength(1);
    expect(groups[0]?.resources[0]?.permissions.map((p) => p.code)).toEqual(['stock.entry.create']);
  });

  it('normalise la recherche (casse, accents)', () => {
    expect(normalizeSearch('  Entrées ')).toBe('entrees');
  });
});
