import { Button } from 'primereact/button';
import { Dropdown } from 'primereact/dropdown';
import { useMemo, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { NavLink, useNavigate } from 'react-router';

import { useAuth } from '@/core/auth/AuthContext';
import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { buildNavigation } from '@/core/modules/registry';
import type { FrontendModule } from '@/core/modules/types';

const ALL_SITES = '__all__';

export function Sidebar({
  modules,
  onNavigate,
}: {
  modules: readonly FrontendModule[];
  onNavigate?: () => void;
}) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const items = useMemo(() => buildNavigation(modules, capabilities), [modules, capabilities]);
  return (
    <nav aria-label={t('layout.menu')}>
      <ul className="sm-nav">
        {items.map((item) => (
          <li key={item.key}>
            <NavLink to={item.path} end={item.path === '/'} onClick={onNavigate}>
              <i className={item.icon} aria-hidden />
              <span>{t(item.labelKey)}</span>
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}

export function AppLayout({
  modules,
  children,
}: {
  modules: readonly FrontendModule[];
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const auth = useAuth();
  const navigate = useNavigate();
  const { capabilities, siteId, setSiteId } = useCapabilities();
  const [menuOpen, setMenuOpen] = useState(false);

  const siteOptions = [
    { value: ALL_SITES, label: t('layout.allSites') },
    ...capabilities.sites.map((s) => ({ value: s.id, label: s.name })),
  ];

  return (
    <div className={`sm-shell${menuOpen ? ' sm-menu-open' : ''}`}>
      <aside className="sm-sidebar">
        <div className="sm-brand">
          <span className="sm-logo">SM</span>
          <div className="sm-brand-text">
            <div className="sm-strong">{capabilities.tenant.name}</div>
            <small className="sm-muted">{capabilities.profile.name}</small>
          </div>
        </div>
        <Sidebar modules={modules} onNavigate={() => setMenuOpen(false)} />
      </aside>
      <div className="sm-main">
        <header className="sm-topbar">
          <Button
            icon="pi pi-bars"
            text
            className="sm-menu-toggle"
            aria-label={t('layout.menu')}
            onClick={() => setMenuOpen((open) => !open)}
          />
          <div className="sm-topbar-site">
            <label htmlFor="site-selector" className="sm-muted">
              {t('layout.site')}
            </label>
            <Dropdown
              inputId="site-selector"
              value={siteId ?? ALL_SITES}
              options={siteOptions}
              onChange={(e) => setSiteId(e.value === ALL_SITES ? null : (e.value as string))}
            />
          </div>
          <div className="sm-topbar-user">
            {auth.memberships.length > 1 && (
              <Button
                icon="pi pi-sync"
                text
                className="sm-switch-tenant"
                aria-label={t('layout.switchTenant')}
                label={t('layout.switchTenant')}
                onClick={() => navigate('/select-tenant')}
              />
            )}
            <span className="sm-user-name">{capabilities.user.full_name}</span>
            <Button
              icon="pi pi-sign-out"
              text
              aria-label={t('actions.logout')}
              onClick={() => void auth.logout()}
            />
          </div>
        </header>
        <main className="sm-content">{children}</main>
      </div>
    </div>
  );
}
