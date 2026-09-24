import { Button } from 'primereact/button';
import { Checkbox } from 'primereact/checkbox';
import { InputSwitch } from 'primereact/inputswitch';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { SearchInput } from '@/shared/ui/SearchInput';
import { StatusBadge, type Tone } from '@/shared/ui/StatusBadge';

import type { Permission } from './api';
import { groupPermissions, normalizeSearch } from './permissionGroups';

// Nature d'une permission : même tonalité partout (lecture neutre, écriture, administration…).
const ACCESS_TONES: Record<string, Tone> = {
  read: 'info',
  write: 'success',
  export: 'neutral',
  admin: 'warning',
  billing: 'danger',
};

/**
 * Sélection des permissions d'un rôle : liste issue de l'API (modules de l'offre), regroupée par
 * module puis ressource, avec recherche. `canGrant` masque ce que l'utilisateur ne peut pas
 * accorder (ergonomie seulement : le backend applique l'anti-escalade).
 */
export function PermissionPicker({
  permissions,
  value,
  onChange,
  readOnly = false,
  canGrant = () => true,
}: {
  permissions: Permission[];
  value: string[];
  onChange?: (codes: string[]) => void;
  readOnly?: boolean;
  canGrant?: (code: string) => boolean;
}) {
  const { t } = useTranslation();
  const [search, setSearch] = useState('');
  const [onlyGranted, setOnlyGranted] = useState(readOnly);
  const selected = new Set(value);
  const label = (p: Permission) => t(`permissions.${p.code}`, p.code);
  const term = normalizeSearch(search);
  const groups = groupPermissions(
    permissions,
    (p) =>
      (!onlyGranted || selected.has(p.code)) &&
      (term === '' ||
        normalizeSearch(`${label(p)} ${p.code} ${t(`modules.${p.module}`)}`).includes(term)),
  );

  const set = (codes: string[], checked: boolean) => {
    const next = new Set(selected);
    for (const code of codes) {
      if (checked) next.add(code);
      else next.delete(code);
    }
    onChange?.([...next].sort());
  };

  return (
    <div className="sm-permission-picker">
      <div className="sm-toolbar">
        <SearchInput
          value={search}
          onChange={setSearch}
          placeholder={t('roles.searchPermission')}
        />
        <div className="sm-checkbox">
          <InputSwitch
            inputId="only-granted"
            checked={onlyGranted}
            onChange={(e) => setOnlyGranted(Boolean(e.value))}
          />
          <label htmlFor="only-granted">{t('roles.onlyGranted')}</label>
        </div>
        <span className="sm-muted" aria-live="polite">
          {t('roles.selectedCount', { count: value.length })}
        </span>
      </div>
      {groups.length === 0 && <p className="sm-muted">{t('roles.noPermission')}</p>}
      {groups.map((group) => {
        const codes = group.resources.flatMap((r) => r.permissions.map((p) => p.code));
        const grantable = codes.filter(canGrant);
        const allChecked = grantable.length > 0 && grantable.every((c) => selected.has(c));
        return (
          <fieldset key={group.module} className="sm-fieldset">
            <legend>{t(`modules.${group.module}`, group.module)}</legend>
            {!readOnly && grantable.length > 0 && (
              <div>
                <Button
                  type="button"
                  text
                  size="small"
                  label={t(allChecked ? 'roles.unselectModule' : 'roles.selectModule')}
                  onClick={() => set(grantable, !allChecked)}
                />
              </div>
            )}
            {group.resources.map((resource) => (
              <div key={resource.resource} className="sm-permission-group">
                {resource.permissions.map((permission) => {
                  const id = `perm-${permission.code}`;
                  const disabled = readOnly || !canGrant(permission.code);
                  return (
                    <div key={permission.code} className="sm-checkbox">
                      <Checkbox
                        inputId={id}
                        checked={selected.has(permission.code)}
                        disabled={disabled}
                        onChange={(e) => set([permission.code], Boolean(e.checked))}
                      />
                      <label htmlFor={id}>{label(permission)}</label>
                      <StatusBadge
                        tone={ACCESS_TONES[permission.access] ?? 'neutral'}
                        label={t(`access.${permission.access}`, permission.access)}
                      />
                    </div>
                  );
                })}
              </div>
            ))}
          </fieldset>
        );
      })}
    </div>
  );
}
