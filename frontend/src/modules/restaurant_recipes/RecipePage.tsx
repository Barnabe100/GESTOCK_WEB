import { Button } from 'primereact/button';
import { InputText } from 'primereact/inputtext';
import { InputTextarea } from 'primereact/inputtextarea';
import { Message } from 'primereact/message';
import { TabPanel, TabView } from 'primereact/tabview';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { PackagingSelect, type SalePackaging } from '@/modules/sales/PackagingSelect';
import { ArticlePicker, toArticleOption, type ArticleOption } from '@/modules/stock/ArticlePicker';
import { formatMoney, formatQuantity } from '@/shared/lib/decimal';
import { translateError } from '@/shared/lib/errors';
import { formatDateTime } from '@/shared/lib/format';
import { FormField } from '@/shared/ui/FormField';
import { LoadingState } from '@/shared/ui/LoadingState';
import { NotFound } from '@/shared/ui/NotFound';
import { PageHeader } from '@/shared/ui/PageHeader';
import { ActiveBadge, StatusBadge } from '@/shared/ui/StatusBadge';
import { confirmAction } from '@/shared/ui/confirm';
import { useToast } from '@/shared/ui/toast';

import {
  RECIPE_MANAGE,
  recipeLabel,
  useRecipe,
  useRecipeAvailability,
  useRecipeCosts,
  useRecipeMutations,
  useRecipeVersions,
  type Recipe,
  type RecipeVersion,
  type RecipeVersionInput,
} from './api';

/**
 * Fiche d'une recette (palier R8, ADR-0053) : composition courante, coût de revient et portions
 * réalisables par site, historique des versions ; création et nouvelle version. Une
 * modification de la composition crée TOUJOURS une nouvelle version (l'ancienne reste
 * consultable). Le serveur fait foi pour toutes les règles (plat non géré en stock,
 * ingrédients gérés en stock, quantités entières, unicité) et pour les coûts.
 */

const DECIMAL = /^\d+(\.\d{1,3})?$/;

interface DraftLine {
  key: number;
  article: ArticleOption | null;
  quantity: string;
  note: string;
}

let nextKey = 1;
const emptyLine = (): DraftLine => ({ key: nextKey++, article: null, quantity: '', note: '' });

function fromVersion(version: RecipeVersion | null): DraftLine[] {
  if (!version || version.lines.length === 0) return [emptyLine()];
  return version.lines.map((line) => ({
    key: nextKey++,
    article: toArticleOption({
      id: line.article_id,
      reference: line.reference,
      designation: line.designation,
      unit: line.unit,
    }),
    quantity: line.quantity,
    note: line.note ?? '',
  }));
}

/** Saisie normalisée « 1,5 » → « 1.5 » (quantités en chaînes, jamais de float). */
const normalized = (value: string) => value.trim().replace(',', '.');

function CompositionEditor({
  recipe,
  onDone,
}: {
  recipe: Recipe | null;
  onDone: (saved: Recipe) => void;
}) {
  const { t } = useTranslation();
  const { create, newVersion } = useRecipeMutations();
  const [dish, setDish] = useState<ArticleOption | null>(null);
  const [packaging, setPackaging] = useState<SalePackaging | null>(null);
  const [yieldQuantity, setYield] = useState(recipe?.current_version?.yield_quantity ?? '1');
  const [instructions, setInstructions] = useState(recipe?.current_version?.instructions ?? '');
  const [notes, setNotes] = useState('');
  const [lines, setLines] = useState<DraftLine[]>(() =>
    fromVersion(recipe?.current_version ?? null),
  );
  const [error, setError] = useState<string | null>(null);
  const creating = recipe === null;
  const pending = create.isPending || newVersion.isPending;
  const setLine = (key: number, patch: Partial<DraftLine>) =>
    setLines((current) => current.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  const filled = lines.filter((l) => l.article !== null || l.quantity !== '');
  const valid =
    (!creating || dish !== null) &&
    DECIMAL.test(normalized(yieldQuantity)) &&
    Number(normalized(yieldQuantity)) > 0 &&
    filled.length > 0 &&
    filled.every(
      (l) =>
        l.article !== null &&
        DECIMAL.test(normalized(l.quantity)) &&
        Number(normalized(l.quantity)) > 0,
    );
  const submit = () => {
    if (!valid) return;
    setError(null);
    const composition: RecipeVersionInput = {
      yield_quantity: normalized(yieldQuantity),
      instructions: instructions.trim() || null,
      lines: filled.map((l) => ({
        article_id: l.article?.id ?? '',
        quantity: normalized(l.quantity),
        note: l.note.trim() || null,
      })),
    };
    const onError = (failure: unknown) => setError(translateError(t, failure));
    if (creating && dish) {
      create.mutate(
        {
          ...composition,
          article_id: dish.id,
          packaging_id: packaging?.id ?? null,
          notes: notes.trim() || null,
        },
        { onSuccess: onDone, onError },
      );
    } else if (recipe) {
      newVersion.mutate({ id: recipe.id, input: composition }, { onSuccess: onDone, onError });
    }
  };
  return (
    <section className="sm-block sm-form" aria-label={t('restaurantRecipes.composition')}>
      {creating ? (
        <div className="sm-form-grid">
          <FormField
            id="recipe-dish"
            label={t('restaurantRecipes.dish')}
            help={t('restaurantRecipes.dishHelp')}
            required
          >
            <ArticlePicker
              id="recipe-dish"
              value={dish}
              unmanagedOnly
              onChange={(v) => {
                setDish(v);
                setPackaging(null);
              }}
            />
          </FormField>
          {dish && (
            <FormField
              id="recipe-presentation"
              label={t('restaurantRecipes.presentation')}
              help={t('restaurantRecipes.presentationHelp')}
            >
              <PackagingSelect
                id="recipe-presentation"
                articleId={dish.id}
                unit={dish.unit}
                basePrice={dish.sale_price}
                value={packaging}
                onChange={setPackaging}
              />
            </FormField>
          )}
        </div>
      ) : (
        <Message severity="info" text={t('restaurantRecipes.newVersionHelp')} />
      )}
      <div className="sm-form-grid">
        <FormField
          id="recipe-yield"
          label={t('restaurantRecipes.yield')}
          help={t('restaurantRecipes.yieldHelp')}
          required
        >
          <InputText
            id="recipe-yield"
            inputMode="decimal"
            value={yieldQuantity}
            onChange={(e) => setYield(e.target.value)}
          />
        </FormField>
      </div>
      <fieldset className="sm-recipe-lines">
        <legend>{t('restaurantRecipes.ingredients')}</legend>
        <p className="sm-help">{t('restaurantRecipes.ingredientsHelp')}</p>
        <ul>
          {lines.map((line, index) => (
            <li key={line.key} className="sm-recipe-line">
              <FormField
                id={`recipe-line-${index}-article`}
                label={t('restaurantRecipes.ingredient', { index: index + 1 })}
                required
              >
                <ArticlePicker
                  id={`recipe-line-${index}-article`}
                  value={line.article}
                  stockManagedOnly
                  onChange={(v) => setLine(line.key, { article: v })}
                />
              </FormField>
              <FormField
                id={`recipe-line-${index}-quantity`}
                label={
                  line.article
                    ? t('restaurantRecipes.quantityIn', { unit: line.article.unit })
                    : t('restaurantRecipes.quantity')
                }
                required
              >
                <InputText
                  id={`recipe-line-${index}-quantity`}
                  inputMode="decimal"
                  value={line.quantity}
                  onChange={(e) => setLine(line.key, { quantity: e.target.value })}
                />
              </FormField>
              <FormField id={`recipe-line-${index}-note`} label={t('restaurantRecipes.note')}>
                <InputText
                  id={`recipe-line-${index}-note`}
                  maxLength={200}
                  value={line.note}
                  onChange={(e) => setLine(line.key, { note: e.target.value })}
                />
              </FormField>
              <Button
                type="button"
                icon="pi pi-trash"
                text
                severity="danger"
                aria-label={t('restaurantRecipes.removeIngredient', { index: index + 1 })}
                disabled={lines.length === 1}
                onClick={() => setLines((current) => current.filter((l) => l.key !== line.key))}
              />
            </li>
          ))}
        </ul>
        <Button
          type="button"
          icon="pi pi-plus"
          outlined
          label={t('restaurantRecipes.addIngredient')}
          disabled={lines.length >= 50}
          onClick={() => setLines((current) => [...current, emptyLine()])}
        />
      </fieldset>
      <FormField id="recipe-instructions" label={t('restaurantRecipes.instructions')}>
        <InputTextarea
          id="recipe-instructions"
          rows={4}
          maxLength={4000}
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
        />
      </FormField>
      {creating && (
        <FormField id="recipe-notes" label={t('restaurantRecipes.notes')}>
          <InputText
            id="recipe-notes"
            maxLength={500}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
          />
        </FormField>
      )}
      {error && <Message severity="error" text={error} />}
      <div className="sm-form-actions">
        <Button
          icon="pi pi-check"
          label={t(creating ? 'restaurantRecipes.create' : 'restaurantRecipes.saveVersion')}
          disabled={!valid}
          loading={pending}
          onClick={submit}
        />
      </div>
    </section>
  );
}

function CompositionView({ version }: { version: RecipeVersion }) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const { locale, timezone } = capabilities.tenant;
  return (
    <div className="sm-form">
      <p>
        <strong>
          {t('restaurantRecipes.versionOf', {
            number: version.number,
            date: formatDateTime(version.created_at, locale, timezone),
          })}
        </strong>
        {version.created_by_name ? ` · ${version.created_by_name}` : ''}
      </p>
      <p>
        {t('restaurantRecipes.yieldValue', {
          value: formatQuantity(version.yield_quantity, locale),
        })}
      </p>
      <table className="sm-plain-table" aria-label={t('restaurantRecipes.ingredients')}>
        <thead>
          <tr>
            <th scope="col">{t('restaurantRecipes.ingredientColumn')}</th>
            <th scope="col">{t('restaurantRecipes.quantity')}</th>
            <th scope="col">{t('restaurantRecipes.note')}</th>
          </tr>
        </thead>
        <tbody>
          {version.lines.map((line) => (
            <tr key={line.article_id}>
              <td>
                {line.reference} — {line.designation}
                {!line.is_active && (
                  <>
                    {' '}
                    <StatusBadge tone="warning" label={t('restaurantRecipes.ingredientInactive')} />
                  </>
                )}
              </td>
              <td>
                {formatQuantity(line.quantity, locale)} {line.unit}
              </td>
              <td>{line.note ?? ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {version.instructions && (
        <div>
          <h3>{t('restaurantRecipes.instructions')}</h3>
          <p className="sm-preline">{version.instructions}</p>
        </div>
      )}
    </div>
  );
}

function CostsPanel({ recipe }: { recipe: Recipe }) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const { currency, locale } = capabilities.tenant;
  const costs = useRecipeCosts(recipe.id);
  if (costs.isPending) return <LoadingState />;
  if (costs.isError) return <Message severity="error" text={translateError(t, costs.error)} />;
  const money = (v: string | null | undefined) => (v ? formatMoney(v, currency, locale) : '—');
  return (
    <div className="sm-form">
      {costs.data.length === 0 && <p className="sm-help">{t('restaurantRecipes.noSite')}</p>}
      {costs.data.map((site) => (
        <section key={site.site_id} className="sm-recipe-site" aria-label={site.site_name}>
          <h3>{site.site_name}</h3>
          {site.portion_cost === undefined ? (
            <p className="sm-help">{t('restaurantRecipes.costsHidden')}</p>
          ) : (
            <>
              <dl className="sm-details">
                <div>
                  <dt>{t('restaurantRecipes.portionCost')}</dt>
                  <dd data-testid="recipe-portion-cost">{money(site.portion_cost)}</dd>
                </div>
                <div>
                  <dt>{t('restaurantRecipes.salePrice')}</dt>
                  <dd>{money(site.sale_price)}</dd>
                </div>
                <div>
                  <dt>{t('restaurantRecipes.margin')}</dt>
                  <dd>
                    {money(site.margin)}
                    {site.margin_rate ? ` (${formatQuantity(site.margin_rate, locale)} %)` : ''}
                  </dd>
                </div>
              </dl>
              {!site.complete && (
                <Message severity="warn" text={t('restaurantRecipes.costIncomplete')} />
              )}
              <table className="sm-plain-table" aria-label={t('restaurantRecipes.costDetail')}>
                <thead>
                  <tr>
                    <th scope="col">{t('restaurantRecipes.ingredientColumn')}</th>
                    <th scope="col">{t('restaurantRecipes.quantity')}</th>
                    <th scope="col">{t('restaurantRecipes.unitCost')}</th>
                    <th scope="col">{t('restaurantRecipes.lineCost')}</th>
                  </tr>
                </thead>
                <tbody>
                  {site.lines.map((line) => (
                    <tr key={line.article_id}>
                      <td>{line.designation}</td>
                      <td>
                        {formatQuantity(line.quantity, locale)} {line.unit}
                      </td>
                      <td>
                        {line.has_cost ? money(line.unit_cost) : t('restaurantRecipes.noCost')}
                      </td>
                      <td>{money(line.line_cost)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </section>
      ))}
    </div>
  );
}

function AvailabilityPanel({ recipe }: { recipe: Recipe }) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const { locale } = capabilities.tenant;
  const availability = useRecipeAvailability(recipe.id);
  if (availability.isPending) return <LoadingState />;
  if (availability.isError) {
    return <Message severity="error" text={translateError(t, availability.error)} />;
  }
  return (
    <div className="sm-form">
      <p className="sm-help">{t('restaurantRecipes.availabilityHelp')}</p>
      {availability.data.map((site) => (
        <section key={site.site_id} className="sm-recipe-site" aria-label={site.site_name}>
          <h3>{site.site_name}</h3>
          <p data-testid="recipe-units">
            {site.units > 0 ? (
              <StatusBadge
                tone="success"
                label={t('restaurantRecipes.unitsAvailable', { count: site.units })}
              />
            ) : (
              <StatusBadge tone="danger" label={t('restaurantRecipes.noneAvailable')} />
            )}
            {site.limiting.length > 0 &&
              ` ${t('restaurantRecipes.limiting', { articles: site.limiting.join(', ') })}`}
          </p>
          <table className="sm-plain-table" aria-label={t('restaurantRecipes.stockDetail')}>
            <thead>
              <tr>
                <th scope="col">{t('restaurantRecipes.ingredientColumn')}</th>
                <th scope="col">{t('restaurantRecipes.required')}</th>
                <th scope="col">{t('restaurantRecipes.available')}</th>
              </tr>
            </thead>
            <tbody>
              {site.lines.map((line) => (
                <tr key={line.article_id}>
                  <td>
                    {line.designation}
                    {!line.in_assortment && (
                      <>
                        {' '}
                        <StatusBadge
                          tone="warning"
                          label={t('restaurantRecipes.notInAssortment')}
                        />
                      </>
                    )}
                  </td>
                  <td>
                    {formatQuantity(line.required, locale)} {line.unit}
                  </td>
                  <td>
                    {formatQuantity(line.available, locale)} {line.unit}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
    </div>
  );
}

function VersionsPanel({ recipe }: { recipe: Recipe }) {
  const { t } = useTranslation();
  const versions = useRecipeVersions(recipe.id, true);
  if (versions.isPending) return <LoadingState />;
  if (versions.isError)
    return <Message severity="error" text={translateError(t, versions.error)} />;
  return (
    <ol className="sm-recipe-versions" aria-label={t('restaurantRecipes.versions')}>
      {versions.data.map((version) => (
        <li key={version.id}>
          <CompositionView version={version} />
        </li>
      ))}
    </ol>
  );
}

function RecipeDetail({ recipe }: { recipe: Recipe }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { can } = useCapabilities();
  const canManage = can(RECIPE_MANAGE);
  const [editing, setEditing] = useState(false);
  const { setActive } = useRecipeMutations();
  const toggle = () => {
    const run = () =>
      setActive.mutate(
        { id: recipe.id, active: !recipe.is_active },
        {
          onSuccess: () => toast.success(t('restaurantRecipes.statusChanged')),
          onError: (failure) => toast.error(translateError(t, failure)),
        },
      );
    if (!recipe.is_active) return run();
    confirmAction(t, {
      header: t('restaurantRecipes.deactivateTitle'),
      message: t('restaurantRecipes.deactivateConfirm', { name: recipeLabel(recipe) }),
      acceptLabel: t('actions.deactivate'),
      danger: true,
      onAccept: run,
    });
  };
  return (
    <>
      <PageHeader
        title={recipeLabel(recipe)}
        description={`${recipe.reference} · ${t('restaurantRecipes.versionCount', {
          count: recipe.version_count,
        })}`}
        breadcrumbs={[
          { label: t('restaurantRecipes.title'), to: '/restaurant/recipes' },
          { label: recipeLabel(recipe) },
        ]}
        actions={
          <>
            <ActiveBadge active={recipe.is_active} />
            {canManage && !editing && (
              <Button
                icon="pi pi-pencil"
                outlined
                label={t('restaurantRecipes.editComposition')}
                onClick={() => setEditing(true)}
              />
            )}
            {canManage && (
              <Button
                icon={recipe.is_active ? 'pi pi-ban' : 'pi pi-check-circle'}
                outlined
                severity={recipe.is_active ? 'danger' : undefined}
                label={t(recipe.is_active ? 'actions.deactivate' : 'actions.activate')}
                loading={setActive.isPending}
                onClick={toggle}
              />
            )}
          </>
        }
      />
      {!recipe.is_active && <Message severity="warn" text={t('restaurantRecipes.inactiveHelp')} />}
      {recipe.notes && <p className="sm-help">{recipe.notes}</p>}
      {editing ? (
        <CompositionEditor
          recipe={recipe}
          onDone={() => {
            setEditing(false);
            toast.success(t('restaurantRecipes.versionSaved'));
          }}
        />
      ) : (
        <TabView>
          <TabPanel header={t('restaurantRecipes.composition')}>
            {recipe.current_version ? (
              <CompositionView version={recipe.current_version} />
            ) : (
              <p className="sm-help">{t('common.noData')}</p>
            )}
          </TabPanel>
          <TabPanel header={t('restaurantRecipes.costs')}>
            <CostsPanel recipe={recipe} />
          </TabPanel>
          <TabPanel header={t('restaurantRecipes.availability')}>
            <AvailabilityPanel recipe={recipe} />
          </TabPanel>
          <TabPanel header={t('restaurantRecipes.versions')}>
            <VersionsPanel recipe={recipe} />
          </TabPanel>
        </TabView>
      )}
    </>
  );
}

export function NewRecipePage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const toast = useToast();
  return (
    <>
      <PageHeader
        title={t('restaurantRecipes.new')}
        description={t('restaurantRecipes.newHelp')}
        breadcrumbs={[
          { label: t('restaurantRecipes.title'), to: '/restaurant/recipes' },
          { label: t('restaurantRecipes.new') },
        ]}
      />
      <CompositionEditor
        recipe={null}
        onDone={(saved) => {
          toast.success(t('restaurantRecipes.created'));
          void navigate(`/restaurant/recipes/${saved.id}`, { replace: true });
        }}
      />
    </>
  );
}

export default function RecipePage() {
  const { id } = useParams();
  const recipe = useRecipe(id);
  if (recipe.isPending) return <LoadingState />;
  if (recipe.isError) return <NotFound />;
  return <RecipeDetail recipe={recipe.data} />;
}
