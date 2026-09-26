import { Button } from 'primereact/button';
import { ConfirmDialog } from 'primereact/confirmdialog';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { NavLink, Navigate, Outlet } from 'react-router';

import { LoadingState } from '@/shared/ui/LoadingState';

import { useConsoleAuth } from './ConsoleAuth';

export const CONSOLE_BASE = '/tech-admin';

const NAV = [
  { to: `${CONSOLE_BASE}/dashboard`, icon: 'pi pi-home', label: 'console:nav.dashboard' },
  { to: `${CONSOLE_BASE}/plans`, icon: 'pi pi-tags', label: 'console:nav.plans' },
  { to: `${CONSOLE_BASE}/catalog`, icon: 'pi pi-book', label: 'console:nav.catalog' },
  { to: `${CONSOLE_BASE}/tenants`, icon: 'pi pi-building', label: 'console:nav.tenants' },
  { to: `${CONSOLE_BASE}/payments`, icon: 'pi pi-wallet', label: 'console:nav.payments' },
  { to: `${CONSOLE_BASE}/audit`, icon: 'pi pi-history', label: 'console:nav.audit' },
] as const;

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('');
}

/**
 * Coquille de la console TechNova : mêmes composants et jetons que StockManager (Design
 * System), accent TechNova distinct ; aucune entreprise, aucun site, aucun module de tenant.
 */
export function ConsoleLayout() {
  const { t } = useTranslation();
  const auth = useConsoleAuth();
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    if (!menuOpen) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMenuOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [menuOpen]);

  if (auth.status === 'loading') return <LoadingState />;
  if (auth.status === 'anonymous' || !auth.admin) {
    return <Navigate to={`${CONSOLE_BASE}/login`} replace />;
  }

  return (
    <div
      className={`sm-shell sm-console${menuOpen ? ' sm-menu-open' : ''}`}
      data-accent="technova"
      data-density="comfortable"
    >
      <a className="sm-skip-link" href="#main-content">
        {t('layout.skipToContent')}
      </a>
      <aside className="sm-sidebar" id="console-sidebar">
        <div className="sm-brand">
          <span className="sm-logo" aria-hidden>
            TN
          </span>
          <div className="sm-brand-text">
            <span className="sm-brand-product">{t('console:brand.product')}</span>
            <div className="sm-strong">{t('console:brand.title')}</div>
            <small className="sm-muted">{t('console:brand.subtitle')}</small>
          </div>
        </div>
        <nav aria-label={t('layout.menu')} className="sm-sidebar-nav">
          <ul className="sm-nav">
            {NAV.map((item) => (
              <li key={item.to}>
                <NavLink to={item.to} onClick={() => setMenuOpen(false)}>
                  <i className={item.icon} aria-hidden />
                  <span>{t(item.label)}</span>
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
      </aside>
      <div className="sm-backdrop" aria-hidden onClick={() => setMenuOpen(false)} />
      <div className="sm-main">
        <header className="sm-topbar">
          <Button
            icon="pi pi-bars"
            text
            className="sm-menu-toggle"
            aria-label={t('layout.menu')}
            aria-controls="console-sidebar"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((open) => !open)}
          />
          <span className="sm-console-badge" data-testid="console-badge">
            <i className="pi pi-shield" aria-hidden /> {t('console:brand.badge')}
          </span>
          <div className="sm-topbar-user">
            <span className="sm-avatar" aria-hidden>
              {initials(auth.admin.full_name)}
            </span>
            <span className="sm-user-name" data-testid="console-admin">
              {auth.admin.full_name}
            </span>
            <Button
              icon="pi pi-sign-out"
              text
              rounded
              aria-label={t('actions.logout')}
              tooltip={t('actions.logout')}
              tooltipOptions={{ position: 'bottom' }}
              onClick={() => void auth.logout()}
            />
          </div>
        </header>
        <main className="sm-content" id="main-content" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
      <ConfirmDialog />
    </div>
  );
}
