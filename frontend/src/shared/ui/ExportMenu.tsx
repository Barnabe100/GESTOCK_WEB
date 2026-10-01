import { Button } from 'primereact/button';
import { Menu } from 'primereact/menu';
import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { apiDownload } from '@/core/api/client';
import { translateError } from '@/shared/lib/errors';
import { useToast } from '@/shared/ui/toast';

/** Formats d'export (ADR-0038) : chaque fonctionnalité propose ceux qui lui sont pertinents. */
export type ExportFormat = 'xlsx' | 'csv' | 'pdf';

const ICONS: Record<ExportFormat, string> = {
  xlsx: 'pi pi-file-excel',
  csv: 'pi pi-file',
  pdf: 'pi pi-file-pdf',
};

/** Enregistre un fichier reçu de l'API (lien temporaire, aucune donnée conservée). */
export function saveFile(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/**
 * UNE action « Exporter » par fonctionnalité, qui ouvre le choix du format parmi `formats`.
 * `path(format)` : URL de l'export avec les filtres de la liste affichée (le serveur applique
 * le même périmètre et la même permission ; il audite chaque export).
 */
export function ExportMenu({
  formats,
  path,
  fallbackName = 'export',
}: {
  formats: readonly ExportFormat[];
  path: (format: ExportFormat) => string;
  fallbackName?: string;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const menu = useRef<Menu>(null);
  const [pending, setPending] = useState<ExportFormat | null>(null);

  const run = async (format: ExportFormat) => {
    setPending(format);
    try {
      const file = await apiDownload(path(format), `${fallbackName}.${format}`);
      saveFile(file.blob, file.filename);
    } catch (error) {
      toast.error(translateError(t, error));
    } finally {
      setPending(null);
    }
  };

  return (
    <>
      <Menu
        ref={menu}
        popup
        id="export-formats"
        model={formats.map((format) => ({
          label: t(`exports.formats.${format}`),
          icon: ICONS[format],
          command: () => void run(format),
        }))}
      />
      <Button
        type="button"
        icon="pi pi-download"
        label={t('exports.action')}
        outlined
        loading={pending !== null}
        aria-haspopup="menu"
        aria-controls="export-formats"
        onClick={(e) => menu.current?.toggle(e)}
      />
    </>
  );
}
