import { AutoComplete } from 'primereact/autocomplete';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '@/core/api/client';
import type { Article } from '@/modules/catalog/api';
import type { Page } from '@/shared/lib/serverTable';

export interface ArticleOption {
  id: string;
  reference: string;
  designation: string;
  unit: string;
  label: string;
}

export function toArticleOption(a: {
  id: string;
  reference: string;
  designation: string;
  unit: string;
}): ArticleOption {
  return { ...a, label: `${a.reference} — ${a.designation}` };
}

/** Recherche serveur des articles actifs (référence, désignation, code-barres). */
export function ArticlePicker({
  id,
  value,
  onChange,
  invalid,
}: {
  id: string;
  value: ArticleOption | null;
  onChange: (value: ArticleOption | null) => void;
  invalid?: boolean;
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
      forceSelection
      dropdown
      invalid={invalid}
      className="sm-article-picker"
    />
  );
}
