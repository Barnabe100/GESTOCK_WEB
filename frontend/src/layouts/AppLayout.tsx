import { Button } from 'primereact/button';
import { ConfirmDialog } from 'primereact/confirmdialog';
import { Dropdown } from 'primereact/dropdown';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { NavLink, useNavigate } from 'react-router';

import { useAuth } from '@/core/auth/AuthContext';
import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { profileLabel } from '@/core/capabilities/profile';
import { buildNavigationSections, buildTopbarItems } from '@/core/modules/registry';
import type { FrontendModule } from '@/core/modules/types';
import { resolveBusinessProfileTheme } from '@/core/theme/businessProfileTheme';
import { ProfileIllustration } from '@/shared/ui/ProfileIllustration';

const ALL_SITES = '__all__';

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('');
}

/** Menu construit à partir des capacités : rubriques et ordre du profil UX du tenant. */
export function Sidebar({
  modules,
  onNavigate,
}: {
  modules: readonly FrontendModule[];
  onNavigate?: () => void;
}) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const sections = useMemo(
    () => buildNavigationSections(modules, capabilities),
    [modules, capabilities],
  );
  return (
    <nav aria-label={t('layout.menu')} className="sm-sidebar-nav">
      {sections.map(({ group, items }) => (
        <div key={group} className="sm-nav-group">
          {group !== 'home' && (
            <p className="sm-nav-group-title" id={`nav-${group}`}>
              {t(`navGroups.${group}`)}
            </p>
          )}
          <ul className="sm-nav" aria-labelledby={group !== 'home' ? `nav-${group}` : undefined}>
            {items.map((item) => (
              <li key={item.key}>
                <NavLink to={item.path} end={item.path === '/'} onClick={onNavigate}>
                  <i className={item.icon} aria-hidden />
                  <span>{t(item.labelKey)}</span>
                </NavLink>
              </li>
            ))}
          </ul>
        </div>
      ))}
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

  useEffect(() => {
    if (!menuOpen) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMenuOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [menuOpen]);

  // Thème métier du SITE actif (palier E) : accent, densité, icône, libellés — dérivés du profil
  // du site par les capacités ; présentation seulement, jamais une preuve de droit.
  const theme = useMemo(
    () => resolveBusinessProfileTheme(capabilities, t, modules),
    [capabilities, t, modules],
  );
  const topbarItems = useMemo(
    () => buildTopbarItems(modules, capabilities),
    [modules, capabilities],
  );

  // Chaque site avec SON profil : le profil n'est pas une propriété de l'entreprise.
  const siteOptions = [
    { value: ALL_SITES, label: t('layout.allSites'), profile: null as string | null },
    ...capabilities.sites.map((s) => ({
      value: s.id,
      label: s.name,
      profile: s.profile ? profileLabel(t, s.profile) : t('layout.unknownProfile'),
    })),
  ];

  return (
    <div
      className={`sm-shell${menuOpen ? ' sm-menu-open' : ''}`}
      data-accent={theme.colors.accent}
      data-density={theme.density}
      data-profile-theme={theme.fallback ? 'neutral' : 'profile'}
    >
      <a className="sm-skip-link" href="#main-content">
        {t('layout.skipToContent')}
      </a>
      <aside className="sm-sidebar" id="app-sidebar">
        <div className="sm-brand">
          <span className="sm-logo" aria-hidden>
            SM
          </span>
          <div className="sm-brand-text">
            <span className="sm-brand-product">{t('app.name')}</span>
            <div className="sm-strong">{capabilities.tenant.name}</div>
            {/* Profil du SITE sélectionné ; vue « Tous les sites » : profil du site de
                référence, jamais présenté comme celui de tous les sites (profils par site). */}
            <small className="sm-muted" data-testid="business-profile-scope">
              {theme.labels.scope === 'reference'
                ? t('layout.referenceProfile')
                : t('layout.siteProfile', { site: theme.labels.site ?? '' })}
            </small>
            <small className="sm-muted sm-brand-profile" data-testid="business-profile">
              <ProfileIllustration icon={theme.icons.profile} size="sm" />
              <span>{theme.labels.profile ?? t('layout.unknownProfile')}</span>
            </small>
          </div>
        </div>
        <Sidebar modules={modules} onNavigate={() => setMenuOpen(false)} />
      </aside>
      <div className="sm-backdrop" aria-hidden onClick={() => setMenuOpen(false)} />
      <div className="sm-main">
        <header className="sm-topbar">
          <Button
            icon="pi pi-bars"
            text
            className="sm-menu-toggle"
            aria-label={t('layout.menu')}
            aria-controls="app-sidebar"
            aria-expanded={menuOpen}
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
              itemTemplate={(option: (typeof siteOptions)[number]) => (
                <span className="sm-site-option">
                  <span>{option.label}</span>
                  {option.profile && <small className="sm-muted">{option.profile}</small>}
                </span>
              )}
              onChange={(e) => setSiteId(e.value === ALL_SITES ? null : (e.value as string))}
            />
          </div>
          <div className="sm-topbar-user">
            {topbarItems.map(({ key, component: Item }) => (
              <Item key={key} />
            ))}
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
            <span className="sm-avatar" aria-hidden>
              {initials(capabilities.user.full_name)}
            </span>
            <span className="sm-user-name">{capabilities.user.full_name}</span>
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
          {children}
        </main>
      </div>
      {/* Dialogue de confirmation unique (confirmAction), partagé par tous les écrans. */}
      <ConfirmDialog />
    </div>
  );
}
