import { IconField } from 'primereact/iconfield';
import { InputIcon } from 'primereact/inputicon';
import { InputText } from 'primereact/inputtext';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ApiError } from '@/core/api/client';
import { translateError } from '@/shared/lib/errors';

import { resolveBarcode, type ScanResult } from './api';

/**
 * Champ de scan des écrans opérationnels (Lot 3-D) : un lecteur qui se comporte comme un
 * clavier tape le code puis Entrée. Le serveur résout le code EXACT vers une présentation
 * (article en unité de base, ou article + conditionnement) ; jamais de recherche partielle ni
 * de résultat affiché auparavant. `reject` permet à l'écran appelant de refuser une présentation
 * (ex. prix non configuré pour une vente) avec un message explicite ; rien n'est alors ajouté.
 * L'écran présélectionne l'article et la présentation, sans jamais deviner une quantité
 * (sauf la vente, où le scan ajoute directement 1 présentation).
 */
export function BarcodeScanField({
  id,
  onScan,
  reject,
  disabled = false,
}: {
  id: string;
  onScan: (scan: ScanResult) => void;
  reject?: (scan: ScanResult) => string | null;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [found, setFound] = useState<string | null>(null);

  const submit = async () => {
    const value = code.trim();
    if (!value || busy) return;
    setBusy(true);
    setError(null);
    setFound(null);
    try {
      const scan = await resolveBarcode(value);
      const refused = reject?.(scan) ?? null;
      if (refused) {
        setError(refused);
        return;
      }
      setCode('');
      setFound(
        t('scan.found', {
          article: `${scan.article.reference} — ${scan.article.designation}`,
          presentation: scan.packaging?.name ?? scan.article.unit,
        }),
      );
      onScan(scan);
    } catch (failure) {
      setError(
        failure instanceof ApiError && failure.code === 'barcode_unknown'
          ? t('scan.unknown', { code: value })
          : translateError(t, failure),
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="sm-scan">
      <label htmlFor={id}>{t('scan.label')}</label>
      <IconField iconPosition="left">
        <InputIcon className={busy ? 'pi pi-spin pi-spinner' : 'pi pi-barcode'} />
        <InputText
          id={id}
          value={code}
          maxLength={50}
          disabled={disabled}
          autoComplete="off"
          placeholder={t('scan.placeholder')}
          invalid={error !== null}
          aria-describedby={`${id}-feedback`}
          onChange={(e) => setCode(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault(); // jamais l'envoi du formulaire englobant
              void submit();
            }
          }}
        />
      </IconField>
      <div id={`${id}-feedback`}>
        {error && (
          <small className="p-error" role="alert">
            {error}
          </small>
        )}
        {found && (
          <small className="sm-muted" role="status">
            {found}
          </small>
        )}
      </div>
    </div>
  );
}
