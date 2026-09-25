import { useTranslation } from 'react-i18next';

import { PageHeader } from '@/shared/ui/PageHeader';

import { CashJournal } from './CashJournal';

/** Journal de caisse de tous les sites visibles (toutes caisses, toutes sessions). */
export default function JournalPage() {
  const { t } = useTranslation();
  return (
    <>
      <PageHeader title={t('cash.journalTitle')} description={t('cash.journalSubtitle')} />
      <CashJournal />
    </>
  );
}
