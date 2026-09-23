import type { ReactNode } from 'react';

export function PageHeader({ title, actions }: { title: string; actions?: ReactNode }) {
  return (
    <header className="sm-page-header">
      <h1>{title}</h1>
      {actions && <div className="sm-page-actions">{actions}</div>}
    </header>
  );
}
