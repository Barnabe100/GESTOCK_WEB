import { Button } from 'primereact/button';
import { InputText } from 'primereact/inputtext';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatQuantity } from '@/shared/lib/decimal';
import { translateError } from '@/shared/lib/errors';
import { confirmAction } from '@/shared/ui/confirm';
import { StatusBadge } from '@/shared/ui/StatusBadge';
import { useToast } from '@/shared/ui/toast';

import {
  ARTICLE_UPDATE,
  useAddBarcode,
  useBarcodes,
  usePackagings,
  useRemoveBarcode,
  type Article,
  type Barcode,
} from './api';

/** Liste de codes d'une présentation, avec retrait (codes supplémentaires et de conditionnement). */
function CodeList({
  codes,
  onRemove,
  canEdit,
}: {
  codes: Barcode[];
  onRemove: (barcode: Barcode) => void;
  canEdit: boolean;
}) {
  const { t } = useTranslation();
  if (codes.length === 0) return <p className="sm-muted">{t('barcodes.none')}</p>;
  return (
    <ul className="sm-chips">
      {codes.map((b) => (
        <li key={b.id} className={b.is_active ? undefined : 'sm-muted'}>
          <code>{b.code}</code>
          {!b.is_active && <StatusBadge tone="neutral" label={t('barcodes.released')} />}
          {canEdit && (
            <Button
              type="button"
              icon="pi pi-times"
              text
              rounded
              size="small"
              severity="danger"
              aria-label={t('barcodes.remove', { code: b.code })}
              onClick={() => onRemove(b)}
            />
          )}
        </li>
      ))}
    </ul>
  );
}

/** Saisie d'un nouveau code (texte libre, 50 caractères ; le serveur contrôle l'unicité). */
function AddCode({
  articleId,
  packagingId,
  target,
}: {
  articleId: string;
  packagingId: string | null;
  target: string;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const add = useAddBarcode();
  const [code, setCode] = useState('');
  const submit = () => {
    const value = code.trim();
    if (!value) return;
    add.mutate(
      { articleId, packagingId, code: value },
      {
        onSuccess: () => {
          toast.success(t('barcodes.added', { code: value }));
          setCode('');
        },
        onError: (error) => toast.error(translateError(t, error)),
      },
    );
  };
  return (
    <div className="sm-inline-form">
      <InputText
        value={code}
        maxLength={50}
        placeholder={t('barcodes.placeholder')}
        aria-label={t('barcodes.newFor', { target })}
        onChange={(e) => setCode(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            submit();
          }
        }}
      />
      <Button
        type="button"
        icon="pi pi-plus"
        outlined
        label={t('barcodes.add')}
        aria-label={t('barcodes.addFor', { target })}
        disabled={!code.trim()}
        loading={add.isPending}
        onClick={submit}
      />
    </div>
  );
}

/**
 * Codes-barres de l'article (Lot 3-D) : un code identifie UNE présentation — l'article en unité
 * de base (code principal, modifié sur l'article, et codes supplémentaires) ou un
 * conditionnement (ses propres codes). Unicité commune au tenant parmi les éléments actifs,
 * contrôlée par le serveur ; un élément désactivé libère ses codes (affichés « libéré »).
 */
export function BarcodesSection({ article }: { article: Article }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { can, capabilities } = useCapabilities();
  const { locale } = capabilities.tenant;
  const canEdit = can(ARTICLE_UPDATE);
  const barcodes = useBarcodes(article.id);
  const packagings = usePackagings(article.id, 'status=all&limit=100&sort=conversion');
  const remove = useRemoveBarcode();
  const codes = barcodes.data?.items ?? [];
  const additional = codes.filter((b) => b.kind === 'ADDITIONAL');

  const onRemove = (barcode: Barcode) =>
    confirmAction(t, {
      header: t('barcodes.removeTitle'),
      message: t('barcodes.confirmRemove', { code: barcode.code }),
      acceptLabel: t('barcodes.removeTitle'),
      danger: true,
      onAccept: () =>
        remove.mutate(barcode.id, {
          onSuccess: () => toast.success(t('barcodes.removed', { code: barcode.code })),
          onError: (error) => toast.error(translateError(t, error)),
        }),
    });

  const baseTarget = t('barcodes.baseUnit', { unit: article.unit });
  return (
    <section className="sm-block" aria-labelledby="barcodes-title">
      <div className="sm-section-header">
        <h2 id="barcodes-title">{t('barcodes.title')}</h2>
      </div>
      <p className="sm-help">{t('barcodes.help')}</p>
      <div className="sm-barcode-group" role="group" aria-label={baseTarget}>
        <h3>{t('barcodes.article', { unit: article.unit })}</h3>
        <dl className="sm-details">
          <div>
            <dt>{t('barcodes.primary')}</dt>
            <dd>
              {article.barcode ? <code>{article.barcode}</code> : t('barcodes.noPrimary')}
              <small className="sm-help"> {t('barcodes.primaryHelp')}</small>
            </dd>
          </div>
        </dl>
        <h4>{t('barcodes.additional')}</h4>
        <CodeList codes={additional} onRemove={onRemove} canEdit={canEdit} />
        {canEdit && <AddCode articleId={article.id} packagingId={null} target={baseTarget} />}
      </div>
      {(packagings.data?.items ?? []).map((p) => {
        const target = `${p.name} (${formatQuantity(p.conversion, locale)} ${article.unit})`;
        return (
          <div key={p.id} className="sm-barcode-group" role="group" aria-label={p.name}>
            <h3>
              {target} {!p.is_active && <StatusBadge tone="neutral" label={t('common.inactive')} />}
            </h3>
            <CodeList
              codes={codes.filter((b) => b.packaging_id === p.id)}
              onRemove={onRemove}
              canEdit={canEdit}
            />
            {canEdit && <AddCode articleId={article.id} packagingId={p.id} target={p.name} />}
          </div>
        );
      })}
    </section>
  );
}
