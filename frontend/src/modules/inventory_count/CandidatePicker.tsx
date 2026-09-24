import { AutoComplete } from 'primereact/autocomplete';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatQuantity } from '@/shared/lib/decimal';

import { fetchCandidates, type Candidate } from './api';

interface Option extends Candidate {
  label: string;
}

/**
 * Recherche serveur des articles actifs proposables pour un inventaire du site (référence,
 * désignation, code-barres), avec leur stock courant. Jamais tout le catalogue d'un coup.
 */
export function CandidatePicker({
  id,
  siteId,
  exclude,
  onSelect,
}: {
  id: string;
  siteId: string | null;
  /** Articles déjà retenus : non proposés. */
  exclude: Set<string>;
  onSelect: (candidate: Candidate) => void;
}) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const [suggestions, setSuggestions] = useState<Option[]>([]);
  const [text, setText] = useState('');
  const locale = capabilities.tenant.locale;

  const complete = async (query: string) => {
    if (!siteId) return setSuggestions([]);
    const params = new URLSearchParams({ site_id: siteId, search: query, limit: '20' });
    try {
      const page = await fetchCandidates(params.toString());
      setSuggestions(
        page.items
          .filter((c) => !exclude.has(c.article_id))
          .map((c) => ({
            ...c,
            label: `${c.reference} — ${c.designation} (${t('inventories.stockShort', {
              quantity: formatQuantity(c.quantity, locale),
              unit: c.unit,
            })})`,
          })),
      );
    } catch {
      setSuggestions([]);
    }
  };

  return (
    <AutoComplete
      inputId={id}
      value={text}
      field="label"
      suggestions={suggestions}
      completeMethod={(e) => void complete(e.query)}
      onChange={(e) => {
        if (typeof e.value === 'string') {
          setText(e.value);
        } else if (e.value) {
          onSelect(e.value as Option);
          setText('');
        }
      }}
      placeholder={t('inventories.articleSearch')}
      disabled={!siteId}
      dropdown
      className="sm-article-picker"
    />
  );
}
