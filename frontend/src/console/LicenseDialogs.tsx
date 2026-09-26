import { Button } from 'primereact/button';
import { Checkbox } from 'primereact/checkbox';
import { Dialog } from 'primereact/dialog';
import { InputNumber } from 'primereact/inputnumber';
import { InputTextarea } from 'primereact/inputtextarea';
import { Message } from 'primereact/message';
import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { translateError } from '@/shared/lib/errors';
import { FormField } from '@/shared/ui/FormField';
import { useToast } from '@/shared/ui/toast';

import { useGenerateLicense, useLicenseAction } from './queries';
import { Details, paymentDay } from './tenantDisplay';
import type { ConsoleLicense, LicenseProposal } from './types';

/** Plafond aligné sur le serveur (`MAX_ACTIVATIONS`), qui revalide de toute façon. */
const MAX_ACTIVATIONS = 10000;

/**
 * Cadre commun : récapitulatif, raison obligatoire, case de confirmation explicite, un seul
 * envoi à la fois ; l'erreur du serveur (qui fait foi) est affichée telle quelle.
 */
function ReasonDialog({
  title,
  summary,
  warning,
  children,
  submitLabel,
  danger,
  pending,
  error,
  onSubmit,
  onClose,
}: {
  title: string;
  summary: [string, ReactNode, string?][];
  warning?: string;
  children?: ReactNode;
  submitLabel: string;
  danger?: boolean;
  pending: boolean;
  error: unknown;
  onSubmit: (reason: string) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [reason, setReason] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [reasonError, setReasonError] = useState(false);
  return (
    <Dialog header={title} visible onHide={onClose} className="sm-dialog">
      <form
        className="sm-form"
        noValidate
        aria-label={title}
        onSubmit={(e) => {
          e.preventDefault();
          if (pending) return;
          if (!reason.trim()) {
            setReasonError(true);
            return;
          }
          onSubmit(reason.trim());
        }}
      >
        <Details items={summary} />
        {warning && <Message severity="warn" text={warning} />}
        {children}
        {error !== null && error !== undefined && (
          <Message severity="error" text={translateError(t, error)} />
        )}
        <FormField
          id="license-reason"
          label={t('console:license.reason')}
          required
          help={t('console:license.reasonHelp')}
          error={reasonError ? t('console:license.reasonRequired') : undefined}
        >
          <InputTextarea
            id="license-reason"
            rows={2}
            maxLength={500}
            value={reason}
            invalid={reasonError}
            onChange={(e) => {
              setReason(e.target.value);
              setReasonError(false);
            }}
            autoFocus
          />
        </FormField>
        <div className="sm-checkbox">
          <Checkbox
            inputId="license-confirm"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.checked === true)}
          />
          <label htmlFor="license-confirm">{t('console:license.confirmCheck')}</label>
        </div>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button
            type="submit"
            label={submitLabel}
            severity={danger ? 'danger' : undefined}
            disabled={!confirmed}
            loading={pending}
          />
        </div>
      </form>
    </Dialog>
  );
}

function ActivationsField({
  value,
  onChange,
  help,
}: {
  value: number | null;
  onChange: (value: number | null) => void;
  help: string;
}) {
  const { t } = useTranslation();
  return (
    <FormField
      id="license-activations"
      label={t('console:license.maxActivations')}
      required
      help={help}
    >
      <InputNumber
        inputId="license-activations"
        value={value}
        min={1}
        max={MAX_ACTIVATIONS}
        useGrouping={false}
        showButtons
        onValueChange={(e) => onChange(e.value ?? null)}
      />
    </FormField>
  );
}

/** Génération depuis un paiement confirmé : période calculée par le serveur, postes demandés
 * proposés, confirmés ou ajustés par TechNova (figés dans la licence). */
export function GenerateLicenseDialog({
  proposal,
  onGenerated,
  onClose,
}: {
  proposal: LicenseProposal;
  onGenerated: (license: ConsoleLicense) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const generate = useGenerateLicense(proposal.payment_id);
  const [activations, setActivations] = useState<number | null>(proposal.max_activations);
  return (
    <ReasonDialog
      title={t('console:license.generateTitle')}
      summary={[
        [t('console:payment.company'), proposal.tenant_name],
        [t('console:payment.site'), proposal.site_name],
        [t('console:payment.plan'), proposal.plan_code],
        [
          t('console:license.validity'),
          t('console:license.validityValue', {
            start: paymentDay(proposal.valid_from),
            end: paymentDay(proposal.valid_until),
          }),
          'proposal-validity',
        ],
        [t('console:license.requestedActivations'), String(proposal.requested_activations)],
      ]}
      warning={t('console:license.generateWarning')}
      submitLabel={t('console:license.generateAction')}
      pending={generate.isPending}
      error={generate.error}
      onClose={onClose}
      onSubmit={(reason) => {
        if (activations === null) return;
        generate.mutate(
          { reason, max_activations: activations },
          {
            onSuccess: (license) => {
              toast.success(t('console:license.generated', { number: license.license_number }));
              onGenerated(license);
            },
          },
        );
      }}
    >
      <ActivationsField
        value={activations}
        onChange={setActivations}
        help={t('console:license.maxActivationsHelp')}
      />
    </ReasonDialog>
  );
}

/** Révocation (définitive, jamais restaurée) ou réémission (nouvelle licence, même période). */
export function LicenseActionDialog({
  license,
  kind,
  onDone,
  onClose,
}: {
  license: ConsoleLicense;
  kind: 'revoke' | 'reissue';
  onDone: (license: ConsoleLicense) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const action = useLicenseAction(license.id);
  const [activations, setActivations] = useState<number | null>(license.max_activations);
  const revoke = kind === 'revoke';
  return (
    <ReasonDialog
      title={t(`console:license.${kind}Title`)}
      summary={[
        [t('console:license.number'), license.license_number],
        [t('console:payment.company'), license.tenant_name],
        [t('console:payment.site'), license.site_name],
        [
          t('console:license.validity'),
          t('console:license.validityValue', {
            start: paymentDay(license.valid_from),
            end: paymentDay(license.valid_until),
          }),
        ],
      ]}
      warning={t(`console:license.${kind}Warning`)}
      submitLabel={t(`console:license.${kind}Action`)}
      danger={revoke}
      pending={action.isPending}
      error={action.error}
      onClose={onClose}
      onSubmit={(reason) => {
        const body = revoke
          ? { kind, reason }
          : {
              kind,
              reason,
              ...(activations !== null && activations !== license.max_activations
                ? { max_activations: activations }
                : {}),
            };
        action.mutate(body, {
          onSuccess: (result) => {
            toast.success(t(`console:license.${kind}d`, { number: result.license_number }));
            onDone(result);
          },
        });
      }}
    >
      {!revoke && (
        <ActivationsField
          value={activations}
          onChange={setActivations}
          help={t('console:license.reissueActivationsHelp')}
        />
      )}
    </ReasonDialog>
  );
}
