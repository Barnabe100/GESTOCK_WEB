import { Card } from 'primereact/card';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

export function AuthCard({ title, children }: { title: string; children: ReactNode }) {
  const { t } = useTranslation();
  return (
    <main className="sm-auth">
      <div className="sm-auth-brand">
        <span className="sm-logo">SM</span>
        <div>
          <div className="sm-strong">{t('app.name')}</div>
          <small className="sm-muted">{t('app.tagline')}</small>
        </div>
      </div>
      <Card title={title} className="sm-auth-card">
        {children}
      </Card>
    </main>
  );
}
