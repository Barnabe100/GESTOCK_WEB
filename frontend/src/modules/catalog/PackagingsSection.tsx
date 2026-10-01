import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { Dialog } from 'primereact/dialog';
import { InputText } from 'primereact/inputtext';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatMoney, formatQuantity, normalizeDecimal } from '@/shared/lib/decimal';
import { translateError } from '@/shared/lib/errors';
import { INITIAL_TABLE, toQueryString, type TableState } from '@/shared/lib/serverTable';
import { confirmAction } from '@/shared/ui/confirm';
import { EmptyState } from '@/shared/ui/EmptyState';
import { FormField } from '@/shared/ui/FormField';
import { RowActions } from '@/shared/ui/RowActions';
import { ServerTable } from '@/shared/ui/ServerTable';
import { ActiveBadge, StatusBadge } from '@/shared/ui/StatusBadge';
import { useToast } from '@/shared/ui/toast';

import {
  ARTICLE_UPDATE,
  PRICE_UPDATE,
  usePackagings,
  useSavePackaging,
  useSetPackagingActive,
  type Article,
  type Packaging,
  type PackagingInput,
} from './api';

const schema = z.object({
  name: z.string().trim().min(1).max(50),
  conversion: z.string().refine((v) => {
    const n = normalizeDecimal(v, 3);
    return n !== null && /[1-9]/.test(n);
  }),
  // Vide : prix non configuré (conditionnement invendable) ; « 0 » : prix configuré à zéro.
  sale_price: z.string().refine((v) => v.trim() === '' || normalizeDecimal(v, 2) !== null),
});
type FormValues = z.infer<typeof schema>;

/**
 * Création / modification d'un conditionnement (Lot 3-B). Nom et conversion :
 * `catalog.article.update` ; prix : `catalog.article.price_update` ; conversion figée dès
 * qu'une vente l'utilise. L'interface n'envoie que ce que l'utilisateur peut modifier ; le
 * serveur revérifie tout (permissions, conversion entière pour un article sans décimales…).
 */
function PackagingDialog({
  article,
  packaging,
  onClose,
}: {
  article: Article;
  packaging: Packaging | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const { can } = useCapabilities();
  const canGeneral = can(ARTICLE_UPDATE);
  const canPrices = can(PRICE_UPDATE);
  const conversionLocked = !canGeneral || packaging?.in_use === true;
  const save = useSavePackaging(article.id);
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      name: packaging?.name ?? '',
      conversion: packaging?.conversion ?? '',
      sale_price: packaging?.sale_price ?? '',
    },
  });
  const errors = form.formState.errors;

  const onSubmit = form.handleSubmit((values) => {
    const input: PackagingInput = {};
    if (canGeneral) input.name = values.name.trim();
    if (!conversionLocked) input.conversion = normalizeDecimal(values.conversion, 3) ?? '';
    // Prix : envoyé seulement par un habilité et s'il est saisi ; sinon il reste non configuré.
    const price = normalizeDecimal(values.sale_price, 2);
    if (canPrices && price !== null) input.sale_price = price;
    save.mutate(
      { id: packaging?.id, input },
      {
        onSuccess: () => {
          toast.success(t(packaging ? 'packagings.updated' : 'packagings.created'));
          onClose();
        },
        onError: (error) => toast.error(translateError(t, error)),
      },
    );
  });

  return (
    <Dialog
      header={packaging ? `${t('packagings.edit')} — ${packaging.name}` : t('packagings.new')}
      visible
      onHide={onClose}
      className="sm-dialog"
    >
      <form onSubmit={onSubmit} className="sm-form" noValidate>
        <FormField
          id="packaging-name"
          label={t('packagings.name')}
          required
          help={t('packagings.nameHelp')}
          error={errors.name && t('validation.required')}
        >
          <InputText id="packaging-name" readOnly={!canGeneral} {...form.register('name')} />
        </FormField>
        <FormField
          id="packaging-conversion"
          label={t('packagings.conversion', { unit: article.unit })}
          required
          help={
            packaging?.in_use
              ? t('packagings.conversionLocked')
              : t(
                  article.decimal_quantity_allowed
                    ? 'packagings.conversionHelp'
                    : 'packagings.conversionWholeHelp',
                  { unit: article.unit },
                )
          }
          error={errors.conversion && t('packagings.invalidConversion')}
        >
          <InputText
            id="packaging-conversion"
            inputMode="decimal"
            readOnly={conversionLocked}
            {...form.register('conversion')}
          />
        </FormField>
        <FormField
          id="packaging-sale_price"
          label={t('articles.salePrice')}
          help={
            canPrices
              ? t('packagings.priceHelp')
              : packaging?.sale_price === null || packaging === null
                ? t('packagings.priceNotSetHelp')
                : t('articles.priceLocked')
          }
          error={errors.sale_price && t('articles.invalidMoney')}
        >
          <InputText
            id="packaging-sale_price"
            inputMode="decimal"
            readOnly={!canPrices}
            {...form.register('sale_price')}
          />
        </FormField>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button type="submit" label={t('actions.save')} loading={save.isPending} />
        </div>
      </form>
    </Dialog>
  );
}

/**
 * Conditionnements de vente de l'article (Lot 3-B) : l'unité de base reste toujours vendable ;
 * chaque conditionnement a sa conversion et son prix propre. Jamais supprimé : désactivé.
 */
export function PackagingsSection({ article }: { article: Article }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { can, capabilities } = useCapabilities();
  const { currency, locale } = capabilities.tenant;
  const [table, setTable] = useState<TableState>({ ...INITIAL_TABLE, rows: 10 });
  const packagings = usePackagings(article.id, toQueryString(table));
  const setActive = useSetPackagingActive();
  const [editing, setEditing] = useState<Packaging | 'new' | null>(null);
  const canEdit = can(ARTICLE_UPDATE) || can(PRICE_UPDATE);

  const toggle = (p: Packaging) => {
    const run = () =>
      setActive.mutate(
        { id: p.id, active: !p.is_active },
        {
          onSuccess: () =>
            toast.success(t(p.is_active ? 'packagings.deactivated' : 'packagings.activated')),
          onError: (error) => toast.error(translateError(t, error)),
        },
      );
    if (!p.is_active) return run();
    confirmAction(t, {
      header: t('packagings.deactivate'),
      message: t('packagings.confirmDeactivate', { name: p.name }),
      acceptLabel: t('packagings.deactivate'),
      danger: true,
      onAccept: run,
    });
  };

  return (
    <section className="sm-block" aria-labelledby="packagings-title">
      <div className="sm-section-header">
        <h2 id="packagings-title">{t('packagings.title')}</h2>
        {can(ARTICLE_UPDATE) && (
          <Button
            icon="pi pi-plus"
            label={t('packagings.new')}
            outlined
            onClick={() => setEditing('new')}
          />
        )}
      </div>
      <p className="sm-help">
        {t('packagings.help', {
          unit: article.unit,
          price: formatMoney(article.sale_price, currency, locale),
        })}
      </p>
      <ServerTable
        query={packagings}
        table={table}
        onTableChange={setTable}
        empty={<EmptyState icon="pi pi-box" title={t('packagings.empty')} />}
      >
        <Column field="name" header={t('packagings.name')} />
        <Column
          header={t('packagings.contains')}
          body={(p: Packaging) => `${formatQuantity(p.conversion, locale)} ${article.unit}`}
        />
        <Column
          header={t('articles.salePrice')}
          body={(p: Packaging) =>
            p.sale_price === null ? (
              <StatusBadge tone="warning" label={t('packagings.priceNotSet')} />
            ) : (
              formatMoney(p.sale_price, currency, locale)
            )
          }
        />
        <Column
          header={t('articles.status')}
          body={(p: Packaging) => (
            <div className="sm-tags">
              <ActiveBadge active={p.is_active} />
              {p.in_use && <StatusBadge tone="neutral" label={t('packagings.inUse')} />}
            </div>
          )}
        />
        {canEdit && (
          <Column
            header={t('common.actions')}
            body={(p: Packaging) => (
              <RowActions
                actions={[
                  {
                    key: 'edit',
                    label: t('actions.edit'),
                    icon: 'pi pi-pencil',
                    onClick: () => setEditing(p),
                  },
                  {
                    key: 'toggle',
                    label: t(p.is_active ? 'packagings.deactivate' : 'packagings.activate'),
                    icon: p.is_active ? 'pi pi-ban' : 'pi pi-check',
                    danger: p.is_active,
                    hidden: !can(ARTICLE_UPDATE),
                    onClick: () => toggle(p),
                  },
                ]}
              />
            )}
          />
        )}
      </ServerTable>
      {editing && (
        <PackagingDialog
          article={article}
          packaging={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
        />
      )}
    </section>
  );
}
