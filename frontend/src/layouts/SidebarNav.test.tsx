// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { FRONTEND_MODULES } from '@/app/modules';
import type { Capabilities } from '@/core/api/types';
import { AuthContext } from '@/core/auth/AuthContext';
import { CapabilitiesContext } from '@/core/capabilities/CapabilitiesContext';
import { buildNavigationSections } from '@/core/modules/registry';
import '@/core/i18n';

import { AppLayout } from './AppLayout';
import {
  DRAWER_QUERY,
  FLYOUT_MARGIN,
  HOVER_CLOSE_DELAY_MS,
  HOVER_QUERY,
  NAV_GROUP_ICONS,
  SidebarNav,
  flyoutPosition,
} from './SidebarNav';

/**
 * Refonte UX de la barre latérale : PRÉSENTATION seulement. Les rubriques et les entrées restent
 * celles de `buildNavigationSections` (modules effectifs et implémentés, permissions, profil).
 */

const CORE = ['dashboard', 'organization', 'users', 'subscription', 'audit'];
const ALL = ['stock.level.view', 'stock.entry.view', 'sales.sale.view', 'users.member.view'];

function caps(permissions: string[] = ALL): Capabilities {
  return {
    user: { id: 'u', email: 'a@b.c', full_name: 'Awa', locale: 'fr', must_change_password: false },
    tenant: { id: 't', name: 'Faso', slug: 'faso', currency: 'XOF', locale: 'fr', timezone: 'UTC' },
    is_owner: false,
    profile: { code: 'retail.alimentation', name: 'Alimentation', sector: null, ux_profile: null },
    plan: { code: 'STANDARD', name: 'Standard' },
    subscription: {
      status: 'active',
      billing_period: 'monthly',
      current_period_end: '2030-01-01',
      allowed_access: [],
    },
    site: null,
    sites: [],
    modules: [
      ...[...CORE, 'stock', 'sales'].map((code) => ({
        code,
        status: 'available' as const,
        core: CORE.includes(code),
      })),
      // Module PLANIFIÉ : jamais au menu, même déclaré dans la navigation du profil.
      { code: 'restaurant.tables', status: 'planned' as const, core: false },
    ],
    permissions,
    restricted_permissions: [],
    navigation: ['dashboard', 'stock', 'sales', 'restaurant.tables', 'users'],
    terminology: {},
    ux: {
      code: 'retail.default',
      navigation: [
        { group: 'home', modules: ['dashboard'] },
        { group: 'stock', modules: ['stock'] },
        { group: 'sales', modules: ['sales'] },
        { group: 'restaurant', modules: ['restaurant.tables'] },
        { group: 'admin', modules: ['users'] },
      ],
      dashboard: { widgets: [], shortcuts: [] },
      theme: { accent: null, density: null, icon: null },
      upcoming: ['restaurant.tables'],
    },
    features: [],
    limits: {},
  };
}

/** Écran simulé : ordinateur à souris (défaut), tablette tactile ou mobile (tiroir). */
function screenAs(kind: 'desktop' | 'touch-desktop' | 'drawer') {
  const matches: Record<string, boolean> = {
    [DRAWER_QUERY]: kind === 'drawer',
    [HOVER_QUERY]: kind === 'desktop',
  };
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches: matches[query] ?? false,
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    })),
  );
}

function Location() {
  return <output data-testid="location">{useLocation().pathname}</output>;
}

function Providers({
  children,
  capabilities,
  path,
}: {
  children: React.ReactNode;
  capabilities: Capabilities;
  path: string;
}) {
  return (
    <MemoryRouter initialEntries={[path]}>
      <AuthContext.Provider
        value={{
          status: 'authenticated',
          user: null,
          tenantId: 't',
          memberships: [],
          login: async () => undefined,
          signup: async () => undefined,
          logout: async () => undefined,
          selectTenant: async () => undefined,
          changePassword: async () => undefined,
        }}
      >
        <CapabilitiesContext.Provider
          value={{
            capabilities,
            can: (p) => capabilities.permissions.includes(p),
            isRestricted: () => false,
            hasModule: () => true,
            siteId: null,
            setSiteId: () => undefined,
          }}
        >
          {children}
          <Location />
        </CapabilitiesContext.Provider>
      </AuthContext.Provider>
    </MemoryRouter>
  );
}

function renderNav({
  permissions = ALL,
  path = '/',
  compact = false,
  onNavigate,
}: { permissions?: string[]; path?: string; compact?: boolean; onNavigate?: () => void } = {}) {
  return render(
    <Providers capabilities={caps(permissions)} path={path}>
      <SidebarNav modules={FRONTEND_MODULES} compact={compact} onNavigate={onNavigate} />
    </Providers>,
  );
}

function renderLayout(path = '/') {
  return render(
    <Providers capabilities={caps()} path={path}>
      <AppLayout modules={FRONTEND_MODULES}>
        <p>contenu</p>
      </AppLayout>
    </Providers>,
  );
}

const nav = () => within(screen.getByRole('navigation', { name: 'Menu' }));
const groupButton = (name: string) =>
  nav()
    .getAllByRole('button')
    .find((b) => b.querySelector('.sm-nav-group-title')?.textContent === name)!;
const entry = (name: string) => groupButton(name).closest('li')!;
const panel = (name: string) =>
  document.getElementById(groupButton(name).getAttribute('aria-controls')!)!;
const isOpen = (name: string) =>
  groupButton(name).getAttribute('aria-expanded') === 'true' && !panel(name).hidden;

describe('barre latérale par rubriques', () => {
  beforeEach(() => {
    screenAs('desktop');
    sessionStorage.clear();
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('1. menus principaux : tableau de bord direct, rubriques avec icône et indicateur', () => {
    renderNav();
    const main = nav()
      .getAllByRole('listitem')
      .filter((li) => li.parentElement?.matches('.sm-nav-main-list'));
    expect(main.map((li) => li.querySelector('.sm-nav-label')?.textContent)).toEqual([
      'Tableau de bord',
      'Stock',
      'Ventes et clients',
      'Administration',
    ]);
    expect(nav().getByRole('link', { name: 'Tableau de bord' }).getAttribute('href')).toBe('/');
    for (const [name, group] of [
      ['Stock', 'stock'],
      ['Ventes et clients', 'sales'],
      ['Administration', 'admin'],
    ] as const) {
      const button = groupButton(name);
      expect(button.querySelector(`i.${NAV_GROUP_ICONS[group]!.split(' ')[1]}`)).not.toBeNull();
      // Indicateur de sous-menu : flèche et état déplié annoncé.
      expect(button.querySelector('.sm-nav-caret')).not.toBeNull();
      expect(button.getAttribute('aria-expanded')).toBe('false');
    }
    // Le lien direct n'a pas d'indicateur de sous-menu.
    expect(
      nav().getByRole('link', { name: 'Tableau de bord' }).querySelector('.sm-nav-caret'),
    ).toBeNull();
  });

  it('2. regroupement : chaque panneau contient exactement les entrées de sa rubrique', () => {
    renderNav();
    const sections = buildNavigationSections(FRONTEND_MODULES, caps());
    for (const [name, group] of [
      ['Stock', 'stock'],
      ['Ventes et clients', 'sales'],
      ['Administration', 'admin'],
    ] as const) {
      const expected = sections.find((s) => s.group === group)!.items.map((i) => i.path);
      const hrefs = within(panel(name))
        .getAllByRole('link', { hidden: true })
        .map((a) => a.getAttribute('href'));
      expect(hrefs).toEqual(expected);
      // Panneau nommé par sa rubrique.
      expect(
        within(panel(name)).getByRole('list', { hidden: true }).getAttribute('aria-labelledby'),
      ).toBe(`nav-${group}`);
    }
    expect(
      within(panel('Stock'))
        .getAllByRole('link', { hidden: true })
        .map((a) => a.textContent),
    ).toContain('Stock par site');
  });

  it('3. survol (ordinateur) : le panneau contextuel s’ouvre à côté de la barre', () => {
    vi.useFakeTimers();
    renderNav();
    expect(isOpen('Stock')).toBe(false);
    fireEvent.mouseEnter(entry('Stock'));
    expect(isOpen('Stock')).toBe(true);
    expect(panel('Stock').classList.contains('sm-nav-flyout')).toBe(true);
    expect(within(panel('Stock')).getByRole('link', { name: 'Stock par site' })).toBeTruthy();
    // Curseur posé sur une autre rubrique : bascule après le délai, un seul panneau ouvert.
    fireEvent.mouseLeave(entry('Stock'));
    fireEvent.mouseEnter(entry('Ventes et clients'));
    expect(isOpen('Stock')).toBe(true);
    act(() => vi.advanceTimersByTime(HOVER_CLOSE_DELAY_MS));
    expect(isOpen('Ventes et clients')).toBe(true);
    expect(isOpen('Stock')).toBe(false);
    // Trajet en diagonale vers le panneau, à travers une autre rubrique : panneau conservé.
    fireEvent.mouseLeave(entry('Ventes et clients'));
    fireEvent.mouseEnter(entry('Administration'));
    act(() => vi.advanceTimersByTime(HOVER_CLOSE_DELAY_MS - 50));
    fireEvent.mouseLeave(entry('Administration'));
    fireEvent.mouseEnter(panel('Ventes et clients'));
    act(() => vi.advanceTimersByTime(HOVER_CLOSE_DELAY_MS * 4));
    expect(isOpen('Ventes et clients')).toBe(true);
    expect(isOpen('Administration')).toBe(false);
  });

  it('4. clic : ouvre puis referme ; clic extérieur et Échap referment', () => {
    renderNav();
    fireEvent.click(groupButton('Stock'));
    expect(isOpen('Stock')).toBe(true);
    fireEvent.click(groupButton('Stock'));
    expect(isOpen('Stock')).toBe(false);
    // Clic extérieur.
    fireEvent.click(groupButton('Stock'));
    fireEvent.mouseDown(document.body);
    expect(isOpen('Stock')).toBe(false);
    // Clic DANS le panneau : reste ouvert.
    fireEvent.click(groupButton('Stock'));
    fireEvent.mouseDown(panel('Stock'));
    expect(isOpen('Stock')).toBe(true);
    // Échap : refermé, focus rendu au bouton de la rubrique.
    const link = within(panel('Stock')).getByRole('link', { name: 'Stock par site' });
    link.focus();
    fireEvent.keyDown(link, { key: 'Escape' });
    expect(isOpen('Stock')).toBe(false);
    expect(document.activeElement).toBe(groupButton('Stock'));
  });

  it('5. le panneau reste ouvert pendant le déplacement du curseur, se ferme après un délai', () => {
    vi.useFakeTimers();
    renderNav();
    fireEvent.mouseEnter(entry('Stock'));
    // Le curseur quitte le bouton vers le panneau (enfant de la même entrée) : délai annulé.
    fireEvent.mouseLeave(entry('Stock'));
    act(() => vi.advanceTimersByTime(HOVER_CLOSE_DELAY_MS - 50));
    expect(isOpen('Stock')).toBe(true);
    fireEvent.mouseEnter(panel('Stock'));
    act(() => vi.advanceTimersByTime(HOVER_CLOSE_DELAY_MS * 2));
    expect(isOpen('Stock')).toBe(true);
    // Le curseur s'éloigne vraiment : fermeture après le délai, pas avant.
    fireEvent.mouseLeave(entry('Stock'));
    act(() => vi.advanceTimersByTime(HOVER_CLOSE_DELAY_MS - 1));
    expect(isOpen('Stock')).toBe(true);
    act(() => vi.advanceTimersByTime(1));
    expect(isOpen('Stock')).toBe(false);
    // Ouvert au survol PUIS cliqué : épinglé, le départ du curseur ne le ferme plus.
    fireEvent.mouseEnter(entry('Stock'));
    fireEvent.click(groupButton('Stock'));
    fireEvent.mouseLeave(entry('Stock'));
    act(() => vi.advanceTimersByTime(HOVER_CLOSE_DELAY_MS * 4));
    expect(isOpen('Stock')).toBe(true);
  });

  it('6–7. mode compact et mode développé : bascule en haut de la barre', () => {
    const { container } = renderLayout();
    const shell = () => container.querySelector('.sm-shell')!;
    const mode = () => screen.getByRole('navigation', { name: 'Menu' }).dataset.mode;
    expect(mode()).toBe('expanded');
    expect(shell().classList.contains('sm-sidebar-compact')).toBe(false);
    const toggle = screen.getByRole('button', { name: 'Réduire le menu' });
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(toggle.getAttribute('aria-controls')).toBe('app-sidebar');
    fireEvent.click(toggle);
    // Compact : largeur réduite (classe de la coquille), icônes seules ; libellés conservés
    // pour les lecteurs d'écran ; les sous-menus s'ouvrent toujours dans le panneau.
    expect(mode()).toBe('compact');
    expect(shell().classList.contains('sm-sidebar-compact')).toBe(true);
    expect(groupButton('Stock').querySelector('.sm-nav-label')?.textContent).toBe('Stock');
    fireEvent.mouseEnter(entry('Stock'));
    expect(isOpen('Stock')).toBe(true);
    const expand = screen.getByRole('button', { name: 'Développer le menu' });
    expect(expand.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(expand);
    expect(mode()).toBe('expanded');
    expect(shell().classList.contains('sm-sidebar-compact')).toBe(false);
    expect(screen.getByRole('button', { name: 'Réduire le menu' })).toBeTruthy();
  });

  it('8. infobulles en mode compact seulement : lien au survol, rubrique au focus clavier', async () => {
    renderNav({ compact: true });
    expect(groupButton('Stock').dataset.prTooltip).toBe('Stock');
    expect(nav().getByRole('link', { name: 'Tableau de bord' }).dataset.prTooltip).toBe(
      'Tableau de bord',
    );
    const tooltip = () => document.querySelector('.p-tooltip .p-tooltip-text')?.textContent;
    // Lien direct : infobulle au survol.
    fireEvent.mouseEnter(nav().getByRole('link', { name: 'Tableau de bord' }));
    await waitFor(() => expect(tooltip()).toBe('Tableau de bord'));
    fireEvent.mouseLeave(nav().getByRole('link', { name: 'Tableau de bord' }));
    await waitFor(() => expect(document.querySelector('.p-tooltip')).toBeNull());
    // Rubrique : focus au toucher / à la souris (non `:focus-visible`, seul cas de jsdom) :
    // aucune infobulle ne reste affichée.
    act(() => groupButton('Administration').focus());
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(document.querySelector('.p-tooltip')).toBeNull();
    act(() => groupButton('Administration').blur());
    // Rubrique au clavier (focus visible, simulé : jsdom ne l'évalue pas) : infobulle à son nom.
    const matches = Element.prototype.matches;
    vi.spyOn(Element.prototype, 'matches').mockImplementation(function (this: Element, selector) {
      return selector === ':focus-visible' ? true : matches.call(this, selector);
    });
    act(() => groupButton('Administration').focus());
    await waitFor(() => expect(tooltip()).toBe('Administration'));
    act(() => groupButton('Administration').blur());
    await waitFor(() => expect(document.querySelector('.p-tooltip')).toBeNull());
    // Rubrique au survol : son panneau s'ouvre, titré par son nom, sans infobulle superposée.
    fireEvent.mouseEnter(entry('Stock'));
    expect(isOpen('Stock')).toBe(true);
    expect(panel('Stock').querySelector('.sm-nav-flyout-title')?.textContent).toBe('Stock');
    expect(groupButton('Stock').dataset.prDisabled).toBe('true');
    cleanup();
    renderNav();
    expect(groupButton('Stock').dataset.prTooltip).toBeUndefined();
    expect(nav().getByRole('link', { name: 'Tableau de bord' }).dataset.prTooltip).toBeUndefined();
  });

  it('9. permissions : entrées et rubriques sans droit absentes', () => {
    renderNav({ permissions: ['stock.level.view'] });
    const labels = () =>
      nav()
        .getAllByRole('button')
        .map((b) => b.querySelector('.sm-nav-group-title')?.textContent);
    expect(labels()).toEqual(['Stock']);
    const links = within(panel('Stock'))
      .getAllByRole('link', { hidden: true })
      .map((a) => a.textContent);
    expect(links).toContain('Stock par site');
    expect(links).not.toContain('Entrées de stock');
    expect(
      nav()
        .getAllByRole('link', { hidden: true })
        .map((a) => a.getAttribute('href')),
    ).not.toContain('/users');
  });

  it('10. module planifié : ni rubrique ni entrée', () => {
    renderNav({ permissions: [...ALL, 'restaurant.tables.table.view'] });
    const links = nav()
      .getAllByRole('link', { hidden: true })
      .map((a) => a.getAttribute('href'));
    expect(links.some((href) => href?.startsWith('/restaurant'))).toBe(false);
    expect(document.querySelector('[data-group="restaurant"]')).toBeNull();
  });

  it('11. routes conservées : URL inchangées, rubrique active, page courante, panneau refermé', () => {
    const onNavigate = vi.fn();
    renderNav({ path: '/stock/levels', onNavigate });
    expect(groupButton('Stock').classList.contains('active')).toBe(true);
    expect(groupButton('Ventes et clients').classList.contains('active')).toBe(false);
    const current = within(panel('Stock')).getByRole('link', {
      name: 'Stock par site',
      hidden: true,
    });
    expect(current.getAttribute('aria-current')).toBe('page');
    fireEvent.click(groupButton('Ventes et clients'));
    const sales = within(panel('Ventes et clients')).getAllByRole('link')[0]!;
    expect(sales.getAttribute('href')).toBe('/sales');
    fireEvent.click(sales);
    expect(screen.getByTestId('location').textContent).toBe('/sales');
    expect(onNavigate).toHaveBeenCalledTimes(1);
    expect(isOpen('Ventes et clients')).toBe(false);
    expect(groupButton('Ventes et clients').classList.contains('active')).toBe(true);
  });

  it('12a. tablette tactile : pas de survol, ouverture au clic', () => {
    screenAs('touch-desktop');
    renderNav();
    fireEvent.mouseEnter(entry('Stock'));
    expect(isOpen('Stock')).toBe(false);
    fireEvent.click(groupButton('Stock'));
    expect(isOpen('Stock')).toBe(true);
  });

  it('12b. mobile : tiroir, rubriques dépliées sur place au clic, fermé après la sélection', () => {
    screenAs('drawer');
    const { container } = renderLayout('/stock/levels');
    const shell = () => container.querySelector('.sm-shell')!;
    expect(screen.getByRole('navigation', { name: 'Menu' }).dataset.mode).toBe('drawer');
    fireEvent.click(screen.getByRole('button', { name: 'Menu' }));
    expect(shell().classList.contains('sm-menu-open')).toBe(true);
    // Rubrique de la page courante dépliée, sous forme de liste en place (pas de panneau flottant).
    expect(isOpen('Stock')).toBe(true);
    expect(panel('Stock').classList.contains('sm-nav-inline')).toBe(true);
    // Le survol n'ouvre rien ; le clic ouvre et referme.
    fireEvent.mouseEnter(entry('Ventes et clients'));
    expect(isOpen('Ventes et clients')).toBe(false);
    fireEvent.click(groupButton('Ventes et clients'));
    expect(isOpen('Ventes et clients')).toBe(true);
    fireEvent.click(within(panel('Ventes et clients')).getAllByRole('link')[0]!);
    expect(screen.getByTestId('location').textContent).toBe('/sales');
    expect(shell().classList.contains('sm-menu-open')).toBe(false);
  });

  it('14. panneau d’une rubrique basse : recalé dans la fenêtre, jamais coupé', () => {
    // Fenêtre de 600 px ; rubrique « Administration » affichée à 560 px ; panneau de 300 px.
    vi.stubGlobal('innerHeight', 600);
    vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(300);
    renderNav();
    vi.spyOn(groupButton('Administration'), 'getBoundingClientRect').mockReturnValue({
      top: 560,
      bottom: 596,
      left: 0,
      right: 256,
      width: 256,
      height: 36,
      x: 0,
      y: 560,
      toJSON: () => ({}),
    });
    fireEvent.click(groupButton('Administration'));
    expect(isOpen('Administration')).toBe(true);
    const style = panel('Administration').style;
    // Remonté : bas du panneau à la marge du bas (600 − 8 − 300 = 292), toutes entrées visibles.
    expect(style.top).toBe(`${600 - FLYOUT_MARGIN - 300}px`);
    expect(style.maxHeight).toBe(`${600 - 2 * FLYOUT_MARGIN}px`);
    // Rotation / redimensionnement : position recalculée.
    vi.stubGlobal('innerHeight', 400);
    act(() => {
      window.dispatchEvent(new Event('resize'));
    });
    expect(panel('Administration').style.top).toBe(`${400 - FLYOUT_MARGIN - 300}px`);
  });

  it('15. tiroir : une rubrique dépliée près du bas défile jusqu’à ses entrées', () => {
    screenAs('drawer');
    const scrolled = vi.fn();
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = scrolled;
    try {
      renderLayout('/');
      fireEvent.click(screen.getByRole('button', { name: 'Menu' }));
      fireEvent.click(groupButton('Administration'));
      expect(isOpen('Administration')).toBe(true);
      expect(scrolled).toHaveBeenCalledWith({ block: 'nearest', behavior: 'smooth' });
    } finally {
      Element.prototype.scrollIntoView = original;
    }
  });

  it('13. persistance : préférence compacte conservée dans l’onglet (mécanisme existant)', () => {
    renderLayout();
    fireEvent.click(screen.getByRole('button', { name: 'Réduire le menu' }));
    expect(sessionStorage.getItem('sm.sidebar')).toBe('compact');
    cleanup();
    renderLayout();
    expect(screen.getByRole('navigation', { name: 'Menu' }).dataset.mode).toBe('compact');
    fireEvent.click(screen.getByRole('button', { name: 'Développer le menu' }));
    expect(sessionStorage.getItem('sm.sidebar')).toBeNull();
  });

  it('accessibilité : boutons natifs, ARIA, clavier', () => {
    renderNav();
    const button = groupButton('Stock');
    expect(button.tagName).toBe('BUTTON');
    expect(button.getAttribute('type')).toBe('button');
    // Nom accessible = libellé de la rubrique (l'icône et la flèche sont décoratives).
    expect(button.querySelectorAll('i[aria-hidden]')).toHaveLength(2);
    expect(panel('Stock').id).toBe(button.getAttribute('aria-controls'));
    // Panneau fermé : ses liens sont hors de l'ordre de tabulation (masqués).
    expect(within(panel('Stock')).queryAllByRole('link')).toHaveLength(0);
    button.focus();
    fireEvent.click(button); // Entrée / Espace sur un bouton natif.
    expect(button.getAttribute('aria-expanded')).toBe('true');
    expect(within(panel('Stock')).getAllByRole('link').length).toBeGreaterThan(0);
    fireEvent.keyDown(button, { key: 'Escape' });
    expect(button.getAttribute('aria-expanded')).toBe('false');
    expect(document.activeElement).toBe(button);
  });
});

describe('position du panneau contextuel (toutes hauteurs d’écran)', () => {
  it('aligné sur sa rubrique quand il tient', () => {
    expect(flyoutPosition(100, 200, 800)).toEqual({ top: 100, maxHeight: 784 });
  });

  it('remonté quand la rubrique est près du bas', () => {
    expect(flyoutPosition(700, 300, 800)).toEqual({ top: 492, maxHeight: 784 });
  });

  it('plus haut que la fenêtre : collé en haut, défilement interne', () => {
    expect(flyoutPosition(400, 2000, 500)).toEqual({ top: 8, maxHeight: 484 });
  });

  it('jamais au-dessus de la marge du haut (rubrique partiellement masquée)', () => {
    expect(flyoutPosition(-40, 100, 800)).toEqual({ top: 8, maxHeight: 784 });
  });

  it('fenêtre minuscule (paysage très bas) : hauteur jamais négative', () => {
    expect(flyoutPosition(10, 100, 10)).toEqual({ top: 8, maxHeight: 0 });
  });
});
