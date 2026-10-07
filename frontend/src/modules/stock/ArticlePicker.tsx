import { AutoComplete } from 'primereact/autocomplete';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '@/core/api/client';
import type { Article, AssortmentState } from '@/modules/catalog/api';
import { OutOfAssortmentBadge } from '@/modules/catalog/assortment';
import type { Page } from '@/shared/lib/serverTable';

export interface ArticleOption {
  id: string;
  reference: string;
  designation: string;
  unit: string;
  label: string;
  /** Prix de vente catalogue (affichage indicatif ; le serveur fait foi). */
  sale_price?: string;
  /** Lot 3-B / 3-C : quantités décimales autorisées (guidage ; le serveur fait foi). */
  decimal_quantity_allowed?: boolean;
  /** Lot 3-G : article suivi par lot / en péremption (guidage ; le serveur fait foi). */
  lot_tracked?: boolean;
  expiry_tracked?: boolean;
  /** Recette, étape 1 : état dans l'assortiment du site de l'opération (guidage). */
  site_assortment?: AssortmentState;
}

export function toArticleOption(a: {
  id: string;
  reference: string;
  designation: string;
  unit: string;
  sale_price?: string;
  decimal_quantity_allowed?: boolean;
  lot_tracked?: boolean;
  expiry_tracked?: boolean;
  site_assortment?: AssortmentState;
}): ArticleOption {
  return { ...a, label: `${a.reference} — ${a.designation}` };
}

/**
 * Recherche serveur des articles actifs (référence, désignation, code-barres). `siteId` :
 * chaque suggestion indique si le site de l'opération propose l'article (Recette, étape 1,
 * ADR-0046) — « Hors assortiment » sinon ; le serveur refuse de toute façon l'enregistrement
 * (`422 article_not_in_site_assortment`), jamais d'ajout automatique.
 */
export function ArticlePicker({
  id,
  value,
  onChange,
  invalid,
  ariaLabel,
  stockManagedOnly = false,
  siteId = null,
}: {
  id: string;
  value: ArticleOption | null;
  onChange: (value: ArticleOption | null) => void;
  invalid?: boolean;
  ariaLabel?: string;
  /** Documents de stock : articles gérés en stock seulement (Lot 3-A ; le serveur refuse
   *  de toute façon les autres). */
  stockManagedOnly?: boolean;
  siteId?: string | null;
}) {
  const { t } = useTranslation();
  const [suggestions, setSuggestions] = useState<ArticleOption[]>([]);
  const [text, setText] = useState<string | null>(null);

  const complete = async (query: string) => {
    const params = new URLSearchParams({
      search: query,
      status: 'active',
      limit: '20',
      sort: 'reference',
      ...(stockManagedOnly ? { stock_managed: 'true' } : {}),
      ...(siteId ? { site_id: siteId } : {}),
    });
    try {
      const page = await api.get<Page<Article>>(`/catalog/articles?${params.toString()}`);
      setSuggestions(page.items.map((a) => toArticleOption({ ...a, id: a.id })));
    } catch {
      setSuggestions([]);
    }
  };

  return (
    <AutoComplete
      inputId={id}
      value={text ?? value}
      field="label"
      suggestions={suggestions}
      itemTemplate={(option: ArticleOption | null) =>
        option && (
          <span className="sm-article-option">
            <span>{option.label}</span>
            {option.site_assortment !== undefined && option.site_assortment !== 'active' && (
              <OutOfAssortmentBadge />
            )}
          </span>
        )
      }
      completeMethod={(e) => void complete(e.query)}
      onChange={(e) => {
        if (typeof e.value === 'string') {
          setText(e.value);
          if (value) onChange(null);
        } else {
          setText(null);
          onChange((e.value as ArticleOption | null) ?? null);
        }
      }}
      placeholder={t('stock.articleSearch')}
      aria-label={ariaLabel}
      forceSelection
      dropdown
      invalid={invalid}
      className="sm-article-picker"
    />
  );
}
