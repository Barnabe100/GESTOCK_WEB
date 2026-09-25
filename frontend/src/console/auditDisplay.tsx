import { useTranslation } from 'react-i18next';

import type { PlatformAuditEntry } from './types';

function display(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'boolean') return value ? '✓' : '✗';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

/** Modifications d'une entrée du journal : « champ : avant → après » (jamais un JSON brut). */
export function AuditChanges({ entry }: { entry: PlatformAuditEntry }) {
  const { t } = useTranslation();
  const fields = [...new Set([...Object.keys(entry.before), ...Object.keys(entry.after)])];
  if (fields.length === 0) {
    const data = Object.entries(entry.data);
    if (data.length === 0) return null;
    return (
      <dl className="sm-kv">
        {data.map(([key, value]) => (
          <div key={key}>
            <dt>{key}</dt>
            <dd>{display(value)}</dd>
          </div>
        ))}
      </dl>
    );
  }
  return (
    <dl className="sm-kv" data-testid="audit-changes">
      {fields.map((field) => (
        <div key={field}>
          <dt>{t(`console:plan.fields.${field}`, { defaultValue: field })}</dt>
          <dd>
            <span title={t('console:audit.before')}>{display(entry.before[field])}</span>
            {' → '}
            <span title={t('console:audit.after')} className="sm-strong">
              {display(entry.after[field])}
            </span>
          </dd>
        </div>
      ))}
    </dl>
  );
}
