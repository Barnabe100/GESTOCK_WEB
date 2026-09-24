import { Button } from 'primereact/button';
import { Card } from 'primereact/card';
import { Column } from 'primereact/column';
import { DataTable } from 'primereact/datatable';
import { Dropdown } from 'primereact/dropdown';
import { InputTextarea } from 'primereact/inputtextarea';
import { Message } from 'primereact/message';
import { RadioButton } from 'primereact/radiobutton';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatQuantity } from '@/shared/lib/decimal';
import { translateError } from '@/shared/lib/errors';
import { EmptyState } from '@/shared/ui/EmptyState';
import { FormField } from '@/shared/ui/FormField';
import { FormSection } from '@/shared/ui/FormSection';
import { PageHeader } from '@/shared/ui/PageHeader';
import { RowActions } from '@/shared/ui/RowActions';
import { useToast } from '@/shared/ui/toast';

import {
  INVENTORY_TYPES,
  useCandidates,
  useInventoryMutations,
  type Candidate,
  type InventoryType,
} from './api';
import { CandidatePicker } from './CandidatePicker';

/** Nouvel inventaire : site, type ; articles choisis (ciblé) ou repris du site (complet). */
export default function InventoryCreatePage() {
  const { t } = useTranslation();
  const toast = useToast();
  const navigate = useNavigate();
  const { capabilities } = useCapabilities();
  const { create } = useInventoryMutations();
  const selectedSite = capabilities.site?.id ?? null;
  const [siteId, setSiteId] = useState<string | null>(
    selectedSite ?? (capabilities.sites.length === 1 ? (capabilities.sites[0]?.id ?? null) : null),
  );
  const [type, setType] = useState<InventoryType>('FULL');
  const [articles, setArticles] = useState<Candidate[]>([]);
  const [comment, setComment] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const locale = capabilities.tenant.locale;

  // Inventaire complet : nombre d'articles gérés sur le site (aperçu, une seule ligne lue).
  const stocked = useCandidates(
    new URLSearchParams({ site_id: siteId ?? '', stocked_only: 'true', limit: '1' }).toString(),
    type === 'FULL' && siteId !== null,
  );
  const siteError = submitted && !siteId;
  const articlesError = submitted && type === 'TARGETED' && articles.length === 0;

  const submit = () => {
    setSubmitted(true);
    if (!siteId || (type === 'TARGETED' && articles.length === 0)) return;
    create.mutate(
      {
        site_id: siteId,
        inventory_type: type,
        article_ids: type === 'TARGETED' ? articles.map((a) => a.article_id) : [],
        comment: comment.trim() || null,
      },
      {
        onSuccess: (inventory) => {
          toast.success(t('inventories.created', { number: inventory.number }));
          void navigate(`/inventories/${inventory.id}`, { replace: true });
        },
        onError: (error) => toast.error(translateError(t, error)),
      },
    );
  };

  return (
    <>
      <PageHeader
        title={t('inventories.new')}
        description={t('inventories.newSubtitle')}
        breadcrumbs={[
          { label: t('inventories.title'), to: '/inventories' },
          { label: t('inventories.new') },
        ]}
      />
      <Card className="sm-form-card">
        <form
          className="sm-form"
          noValidate
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <FormField
            id="inventory-site"
            label={t('layout.site')}
            required
            error={siteError ? t('validation.required') : undefined}
          >
            <Dropdown
              inputId="inventory-site"
              value={siteId}
              onChange={(e) => {
                setSiteId((e.value as string | undefined) ?? null);
                setArticles([]);
              }}
              options={capabilities.sites.map((s) => ({ value: s.id, label: s.name }))}
              placeholder={t('stock.chooseSite')}
              disabled={selectedSite !== null}
              invalid={siteError}
            />
          </FormField>
          <FormSection title={t('inventories.type')}>
            <div className="sm-stack" role="radiogroup" aria-label={t('inventories.type')}>
              {INVENTORY_TYPES.map((value) => (
                <div key={value} className="sm-checkbox">
                  <RadioButton
                    inputId={`inventory-type-${value}`}
                    name="inventory-type"
                    value={value}
                    checked={type === value}
                    onChange={() => setType(value)}
                  />
                  <label htmlFor={`inventory-type-${value}`}>
                    <strong>{t(`inventories.types.${value}`)}</strong>
                    <span className="sm-help"> — {t(`inventories.typeHelp.${value}`)}</span>
                  </label>
                </div>
              ))}
            </div>
          </FormSection>
          {type === 'FULL' ? (
            siteId && (
              <Message
                severity="info"
                text={
                  stocked.data
                    ? t('inventories.fullPreview', { count: stocked.data.total })
                    : t('common.loading')
                }
              />
            )
          ) : (
            <FormSection
              title={t('inventories.articles')}
              description={t('inventories.targetedHelp')}
            >
              <FormField
                id="inventory-article"
                label={t('inventories.addArticle')}
                required
                error={articlesError ? t('inventories.articlesRequired') : undefined}
              >
                <CandidatePicker
                  id="inventory-article"
                  siteId={siteId}
                  exclude={new Set(articles.map((a) => a.article_id))}
                  onSelect={(c) => setArticles((list) => [...list, c])}
                />
              </FormField>
              <DataTable
                className="sm-table"
                value={articles}
                dataKey="article_id"
                tableStyle={{ minWidth: '30rem' }}
                emptyMessage={
                  <EmptyState icon="pi pi-box" title={t('inventories.noArticleSelected')} />
                }
              >
                <Column field="reference" header={t('articles.reference')} />
                <Column field="designation" header={t('articles.designation')} />
                <Column
                  header={t('inventories.stockTheoretical')}
                  headerClassName="sm-num"
                  bodyClassName="sm-num"
                  body={(c: Candidate) => `${formatQuantity(c.quantity, locale)} ${c.unit}`}
                />
                <Column
                  header={t('common.actions')}
                  body={(c: Candidate) => (
                    <RowActions
                      actions={[
                        {
                          key: 'remove',
                          label: t('inventories.removeArticle'),
                          icon: 'pi pi-times',
                          danger: true,
                          onClick: () =>
                            setArticles((list) =>
                              list.filter((a) => a.article_id !== c.article_id),
                            ),
                        },
                      ]}
                    />
                  )}
                />
              </DataTable>
            </FormSection>
          )}
          <FormField id="inventory-comment" label={t('stock.comment')}>
            <InputTextarea
              id="inventory-comment"
              rows={2}
              maxLength={500}
              value={comment}
              onChange={(e) => setComment(e.target.value)}
            />
          </FormField>
          <div className="sm-form-actions">
            <Button
              type="button"
              label={t('actions.cancel')}
              text
              onClick={() => void navigate('/inventories')}
            />
            <Button
              type="submit"
              icon="pi pi-check"
              label={t('inventories.createDraft')}
              loading={create.isPending}
            />
          </div>
        </form>
      </Card>
    </>
  );
}
