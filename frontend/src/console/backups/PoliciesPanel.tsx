import { Button } from 'primereact/button';
import { Card } from 'primereact/card';
import { Dropdown } from 'primereact/dropdown';
import { InputNumber } from 'primereact/inputnumber';
import { InputSwitch } from 'primereact/inputswitch';
import { InputText } from 'primereact/inputtext';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { LoadingState } from '@/shared/ui/LoadingState';
import { useToast } from '@/shared/ui/toast';

import { useBackupPolicies, useUpdateBackupPolicy } from '../queries';
import type { BackupCategory, BackupPolicy, BackupPolicyUpdate } from '../types';
import { ReasonDialog, whenUtc } from './shared';

const CATEGORIES: BackupCategory[] = ['physical', 'logical', 'verification', 'restore_test'];
const WEEKDAYS = [1, 2, 3, 4, 5, 6, 7];

function timezones(current: string): string[] {
  const intl = Intl as unknown as { supportedValuesOf?: (key: string) => string[] };
  const zones = intl.supportedValuesOf ? intl.supportedValuesOf('timeZone') : [];
  return Array.from(new Set(['UTC', current, ...zones]));
}

/**
 * « Paramètres des sauvegardes » : une ligne par fréquence (quotidienne, hebdomadaire,
 * mensuelle, automatique toutes les N heures, manuelle = conservation des sauvegardes à la
 * demande). Physiques (cluster PostgreSQL et WAL : reprise de toute la plateforme, PITR) et
 * logiques (une entreprise : restauration individuelle) sont distinctes. Chaque enregistrement
 * est motivé et journalisé ; la prochaine exécution est recalculée par le serveur et c'est
 * celle que l'agent exécutera.
 */
export function PoliciesPanel() {
  const { t } = useTranslation();
  const policies = useBackupPolicies();
  if (policies.isLoading) return <LoadingState />;
  if (policies.isError || !policies.data) {
    return <ErrorMessage error={policies.error} onRetry={() => void policies.refetch()} />;
  }
  return (
    <div className="sm-stack">
      <p className="sm-help">{t('console:bk.policiesIntro')}</p>
      {CATEGORIES.map((category) => {
        const rows = policies.data.filter((p) => p.category === category);
        if (rows.length === 0) return null;
        return (
          <Card
            key={category}
            title={t(`console:bk.kinds.${category}`)}
            subTitle={t(`console:bk.kindHelp.${category}`)}
          >
            <div className="sm-policy-list">
              {rows.map((p) => (
                <PolicyRow key={p.id} policy={p} />
              ))}
            </div>
          </Card>
        );
      })}
    </div>
  );
}

function PolicyRow({ policy }: { policy: BackupPolicy }) {
  const { t } = useTranslation();
  const toast = useToast();
  const update = useUpdateBackupPolicy();
  const initial = useMemo<Omit<BackupPolicyUpdate, 'reason'>>(
    () => ({
      enabled: policy.enabled,
      run_time: policy.run_time ? policy.run_time.slice(0, 5) : null,
      weekday: policy.weekday,
      month_day: policy.month_day,
      interval_hours: policy.interval_hours,
      start_date: policy.start_date,
      timezone: policy.timezone,
      retention_days: policy.retention_days,
    }),
    [policy],
  );
  const [form, setForm] = useState(initial);
  const [saving, setSaving] = useState(false);
  const dirty = JSON.stringify(form) !== JSON.stringify(initial);
  const uses = (field: string) => policy.fields.includes(field);
  const id = `${policy.category}-${policy.frequency}`;
  const set = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) =>
    setForm((f) => ({ ...f, [key]: value }));
  return (
    <section className="sm-policy" data-testid={`policy-${id}`}>
      <header className="sm-policy-head">
        <h4>{t(`console:bk.frequencies.${policy.frequency}`)}</h4>
        {policy.schedulable ? (
          <label className="sm-checkbox-row" htmlFor={`${id}-enabled`}>
            <InputSwitch
              inputId={`${id}-enabled`}
              checked={form.enabled}
              onChange={(e) => set('enabled', Boolean(e.value))}
              data-testid={`${id}-enabled`}
            />
            <span>{form.enabled ? t('console:bk.enabled') : t('console:bk.disabled')}</span>
          </label>
        ) : (
          <span className="sm-muted">{t('console:bk.manualOnly')}</span>
        )}
      </header>
      <div className="sm-policy-fields">
        {uses('interval_hours') && (
          <label>
            <span>{t('console:bk.fields.interval_hours')}</span>
            <InputNumber
              inputId={`${id}-interval`}
              value={form.interval_hours}
              min={1}
              max={168}
              onValueChange={(e) => set('interval_hours', e.value ?? null)}
              suffix=" h"
            />
          </label>
        )}
        {uses('run_time') && (
          <label>
            <span>
              {t(
                policy.frequency === 'automatic'
                  ? 'console:bk.fields.anchor_time'
                  : 'console:bk.fields.run_time',
              )}
            </span>
            <InputText
              type="time"
              value={form.run_time ?? ''}
              onChange={(e) => set('run_time', e.target.value || null)}
              data-testid={`${id}-time`}
            />
          </label>
        )}
        {uses('weekday') && (
          <label>
            <span>{t('console:bk.fields.weekday')}</span>
            <Dropdown
              value={form.weekday}
              options={WEEKDAYS.map((d) => ({ label: t(`console:bk.weekdays.${d}`), value: d }))}
              onChange={(e) => set('weekday', e.value as number)}
              data-testid={`${id}-weekday`}
            />
          </label>
        )}
        {uses('month_day') && (
          <label>
            <span>{t('console:bk.fields.month_day')}</span>
            <InputNumber
              value={form.month_day}
              min={1}
              max={31}
              onValueChange={(e) => set('month_day', e.value ?? null)}
            />
          </label>
        )}
        {policy.schedulable && (
          <>
            <label>
              <span>{t('console:bk.fields.start_date')}</span>
              <InputText
                type="date"
                value={form.start_date}
                onChange={(e) => set('start_date', e.target.value)}
              />
            </label>
            <label>
              <span>{t('console:bk.fields.timezone')}</span>
              <Dropdown
                value={form.timezone}
                options={timezones(policy.timezone)}
                filter
                onChange={(e) => set('timezone', e.value as string)}
                data-testid={`${id}-timezone`}
              />
            </label>
          </>
        )}
        <label>
          <span>{t('console:bk.fields.retention_days')}</span>
          <InputNumber
            value={form.retention_days}
            min={1}
            max={3650}
            onValueChange={(e) => set('retention_days', e.value ?? 1)}
            suffix={` ${t('console:bk.daysUnit')}`}
            data-testid={`${id}-retention`}
          />
        </label>
      </div>
      <footer className="sm-policy-foot">
        <span data-testid={`${id}-next`}>
          {policy.schedulable
            ? policy.enabled && policy.next_run_at
              ? t('console:bk.nextRun', { when: whenUtc(policy.next_run_at) })
              : t('console:bk.noNextRun')
            : t('console:bk.manualRetention')}
        </span>
        <span>
          {dirty && (
            <Button
              label={t('console:bk.reset')}
              text
              size="small"
              onClick={() => setForm(initial)}
            />
          )}
          <Button
            label={t('console:bk.save')}
            size="small"
            icon="pi pi-save"
            disabled={!dirty}
            onClick={() => setSaving(true)}
            data-testid={`${id}-save`}
          />
        </span>
      </footer>
      {saving && (
        <ReasonDialog
          header={t('console:bk.savePolicy', {
            name: `${t(`console:bk.kinds.${policy.category}`)} — ${t(`console:bk.frequencies.${policy.frequency}`)}`,
          })}
          acceptLabel={t('console:bk.save')}
          pending={update.isPending}
          error={update.error}
          onClose={() => setSaving(false)}
          onAccept={(reason) =>
            update.mutate(
              { category: policy.category, frequency: policy.frequency, body: { ...form, reason } },
              {
                onSuccess: (saved) => {
                  toast.success(
                    saved.next_run_at
                      ? t('console:bk.savedNext', { when: whenUtc(saved.next_run_at) })
                      : t('console:bk.saved'),
                  );
                  setSaving(false);
                },
              },
            )
          }
        />
      )}
    </section>
  );
}
