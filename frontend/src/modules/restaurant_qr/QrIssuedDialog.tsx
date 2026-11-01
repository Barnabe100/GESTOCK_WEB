import { Button } from 'primereact/button';
import { Dialog } from 'primereact/dialog';
import { Message } from 'primereact/message';
import { SelectButton } from 'primereact/selectbutton';
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { useToast } from '@/shared/ui/toast';

import type { QrPointIssued } from './api';

/**
 * QR Code émis (création ou régénération) — R7, ADR-0052. Seule occasion de l'afficher : le
 * jeton n'est jamais conservé en clair ; réimprimer plus tard = régénérer. L'image vient du
 * serveur (SVG Segno) et s'affiche par `<img>` (aucun script possible) ; l'adresse vient de la
 * configuration du serveur, jamais du navigateur.
 */

export type PrintLayout = 'table' | 'poster';

const PAGE_RULES: Record<PrintLayout, string> = {
  // Carte de table : format A6 (105 × 148 mm), à plier ou glisser dans un chevalet.
  table: '@page { size: 105mm 148mm; margin: 6mm; }',
  // Affiche de comptoir : A4 portrait.
  poster: '@page { size: A4 portrait; margin: 15mm; }',
};

export function qrImageSrc(svg: string): string {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

export function QrPrintCard({
  issued,
  layout,
  establishment,
}: {
  issued: QrPointIssued;
  layout: PrintLayout;
  establishment: string;
}) {
  const { t } = useTranslation();
  const { point } = issued;
  return (
    <div className={`sm-qr-card sm-qr-card-${layout}`} data-testid="qr-print-card">
      <p className="sm-qr-card-establishment">{establishment}</p>
      <p className="sm-qr-card-site">{point.site_name}</p>
      <img className="sm-qr-card-code" src={qrImageSrc(issued.svg)} alt={t('restaurantQr.qrAlt')} />
      <p className="sm-qr-card-point">
        {point.kind === 'TABLE'
          ? t('restaurantQr.tableCaption', { label: point.display_name })
          : point.display_name}
      </p>
      <p className="sm-qr-card-help">{t('restaurantQr.scanHelp')}</p>
    </div>
  );
}

function QrPrinter({
  issued,
  layout,
  establishment,
  onDone,
}: {
  issued: QrPointIssued;
  layout: PrintLayout;
  establishment: string;
  onDone: () => void;
}) {
  const printed = useRef(false);
  useEffect(() => {
    if (printed.current) return;
    printed.current = true;
    const page = document.createElement('style');
    page.textContent = PAGE_RULES[layout];
    document.head.append(page);
    document.body.classList.add('sm-printing');
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      page.remove();
      document.body.classList.remove('sm-printing');
      onDone();
    };
    window.addEventListener('afterprint', finish, { once: true });
    window.print();
    finish();
  }, [layout, onDone]);
  return createPortal(
    <div id="sm-print-root">
      <QrPrintCard issued={issued} layout={layout} establishment={establishment} />
    </div>,
    document.body,
  );
}

export function QrIssuedDialog({
  issued,
  onClose,
}: {
  issued: QrPointIssued;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const { capabilities } = useCapabilities();
  const establishment = capabilities.tenant.name;
  const [layout, setLayout] = useState<PrintLayout>(
    issued.point.kind === 'TABLE' ? 'table' : 'poster',
  );
  const [printing, setPrinting] = useState(false);
  const download = () => {
    const blob = new Blob([issued.svg], { type: 'image/svg+xml' });
    const href = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = href;
    link.download = `qr-${issued.point.display_name.replace(/[^\p{L}\p{N}_-]+/gu, '-')}.svg`;
    link.click();
    URL.revokeObjectURL(href);
  };
  const copy = () => {
    void navigator.clipboard?.writeText(issued.url).then(
      () => toast.success(t('restaurantQr.copied')),
      () => toast.error(t('restaurantQr.copyFailed')),
    );
  };
  return (
    <Dialog
      header={t('restaurantQr.issuedTitle', { name: issued.point.display_name })}
      visible
      onHide={onClose}
      className="sm-dialog"
    >
      <div className="sm-form">
        <Message severity="warn" text={t('restaurantQr.issuedWarning')} />
        <div className="sm-qr-preview">
          <QrPrintCard issued={issued} layout={layout} establishment={establishment} />
        </div>
        <SelectButton
          value={layout}
          allowEmpty={false}
          onChange={(e) => setLayout(e.value as PrintLayout)}
          options={(['table', 'poster'] as const).map((v) => ({
            value: v,
            label: t(`restaurantQr.layouts.${v}`),
          }))}
          aria-label={t('restaurantQr.layout')}
        />
        <div className="sm-dialog-actions">
          <Button
            type="button"
            icon="pi pi-copy"
            label={t('restaurantQr.copyLink')}
            text
            onClick={copy}
          />
          <Button
            type="button"
            icon="pi pi-download"
            label={t('restaurantQr.downloadSvg')}
            outlined
            onClick={download}
          />
          <Button
            type="button"
            icon="pi pi-print"
            label={t('restaurantQr.print')}
            onClick={() => setPrinting(true)}
          />
          <Button type="button" label={t('actions.close')} text onClick={onClose} />
        </div>
      </div>
      {printing && (
        <QrPrinter
          issued={issued}
          layout={layout}
          establishment={establishment}
          onDone={() => setPrinting(false)}
        />
      )}
    </Dialog>
  );
}
