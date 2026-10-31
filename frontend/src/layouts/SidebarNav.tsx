import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
} from 'react';
import { Tooltip } from 'primereact/tooltip';
import { useTranslation } from 'react-i18next';
import { NavLink, matchPath, useLocation } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { buildNavigationSections } from '@/core/modules/registry';
import type { FrontendModule, NavItem, NavSection } from '@/core/modules/types';

/**
 * Barre latérale par rubriques (refonte UX) : PRÉSENTATION seulement. Les rubriques, leur ordre
 * et leurs entrées viennent toujours de `buildNavigationSections` (modules effectifs et
 * implémentés, permissions, fonctionnalités, profil UX du site) : rien n'est ajouté ni retiré ici.
 *
 * - Rubrique « home » (tableau de bord) : liens directs.
 * - Autres rubriques : un bouton (icône, libellé, indicateur) ouvrant le panneau de ses
 *   entrées — au SURVOL sur un écran à pointeur précis (fermeture différée, le panneau reste
 *   ouvert pendant le déplacement du curseur) et au CLIC partout (épinglé : ne se ferme qu'au
 *   second clic, à `Échap`, à un clic extérieur ou à la navigation).
 * - Tiroir (tablette, mobile) : rubriques dépliées sur place, au clic seulement.
 */

/** Icône de chaque rubrique (PrimeIcons, système d'icônes du projet) ; repli : dossier. */
export const NAV_GROUP_ICONS: Record<string, string> = {
  home: 'pi pi-objects-column',
  catalog: 'pi pi-box',
  stock: 'pi pi-warehouse',
  sales: 'pi pi-shopping-cart',
  cash: 'pi pi-wallet',
  restaurant: 'pi pi-shop',
  reports: 'pi pi-chart-bar',
  workshop: 'pi pi-wrench',
  admin: 'pi pi-cog',
};
const FALLBACK_GROUP_ICON = 'pi pi-folder';

/**
 * Délai de fermeture d'un panneau ouvert au survol, et de bascule vers une autre rubrique quand
 * un panneau est déjà ouvert (évite disparitions et remplacements intempestifs).
 */
export const HOVER_CLOSE_DELAY_MS = 250;

/** Requête média suivie (repli : `false` si `matchMedia` est indisponible). */
export function useMediaQuery(query: string): boolean {
  const get = () =>
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia(query).matches
      : false;
  const [matches, setMatches] = useState(get);
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return undefined;
    const list = window.matchMedia(query);
    const onChange = () => setMatches(list.matches);
    onChange();
    list.addEventListener('change', onChange);
    return () => list.removeEventListener('change', onChange);
  }, [query]);
  return matches;
}

/** Tablette et mobile : barre latérale en tiroir (même seuil que la feuille de styles). */
export const DRAWER_QUERY = '(max-width: 1024px)';
/** Survol pertinent : pointeur précis capable de survoler (souris, pavé tactile). */
export const HOVER_QUERY = '(hover: hover) and (pointer: fine)';

function isActive(item: NavItem, pathname: string): boolean {
  return matchPath({ path: item.path, end: item.path === '/' }, pathname) !== null;
}

/**
 * Choix de l'utilisateur, valable pour la page où il a été fait : une navigation le périme
 * (le panneau contextuel se referme ; le tiroir revient à la rubrique de la page courante).
 */
interface OpenChoice {
  group: string | null;
  /** Ouvert au clic : reste ouvert quand le curseur s'éloigne. */
  pinned: boolean;
  path: string;
}

function DirectLink({
  item,
  compact,
  onNavigate,
}: {
  item: NavItem;
  compact: boolean;
  onNavigate?: () => void;
}) {
  const { t } = useTranslation();
  const label = t(item.labelKey);
  return (
    <li className="sm-nav-entry">
      <NavLink
        to={item.path}
        end={item.path === '/'}
        onClick={onNavigate}
        className="sm-nav-main"
        data-pr-tooltip={compact ? label : undefined}
        data-pr-position="right"
        data-pr-event="both"
      >
        <i className={item.icon} aria-hidden />
        <span className="sm-nav-label">{label}</span>
      </NavLink>
    </li>
  );
}

function GroupEntry({
  section,
  open,
  active,
  compact,
  drawer,
  hover,
  onHoverOpen,
  onHoverLeave,
  onToggle,
  onClose,
  onNavigate,
}: {
  section: NavSection;
  open: boolean;
  active: boolean;
  compact: boolean;
  drawer: boolean;
  hover: boolean;
  onHoverOpen: () => void;
  onHoverLeave: () => void;
  onToggle: () => void;
  onClose: () => void;
  onNavigate?: () => void;
}) {
  const { t } = useTranslation();
  const { group, items } = section;
  const panelId = `nav-panel-${group}`;
  const labelId = `nav-${group}`;
  const title = t(`navGroups.${group}`);
  const button = useRef<HTMLButtonElement>(null);
  const [position, setPosition] = useState<CSSProperties>({});
  const flyout = !drawer;

  // Panneau contextuel adjacent à la barre (position fixe : jamais rogné par le défilement).
  // Recalculée au défilement de la barre et au redimensionnement, tant qu'il est ouvert.
  useEffect(() => {
    if (!open || !flyout) return undefined;
    const place = () => {
      if (!button.current) return;
      const rect = button.current.getBoundingClientRect();
      const sidebar = button.current.closest('.sm-sidebar')?.getBoundingClientRect();
      setPosition({ top: Math.max(8, rect.top), left: sidebar ? sidebar.right : rect.right });
    };
    place();
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    return () => {
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', place, true);
    };
  }, [open, flyout]);

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === 'Escape' && open) {
      event.stopPropagation();
      onClose();
      button.current?.focus();
    }
  };

  return (
    <li
      className={`sm-nav-entry sm-nav-section${open ? ' sm-nav-open' : ''}`}
      data-group={group}
      onMouseEnter={hover && flyout ? onHoverOpen : undefined}
      onMouseLeave={hover && flyout ? onHoverLeave : undefined}
      onKeyDown={onKeyDown}
    >
      <button
        ref={button}
        type="button"
        className={`sm-nav-main${active ? ' active' : ''}`}
        aria-expanded={open}
        aria-controls={panelId}
        // Survol d'une rubrique : son panneau s'ouvre, titré par son nom (aucune infobulle
        // superposée) ; l'infobulle sert au focus clavier, panneau fermé.
        data-pr-tooltip={compact ? title : undefined}
        data-pr-position="right"
        data-pr-event="focus"
        data-pr-disabled={open || undefined}
        onClick={onToggle}
      >
        <i className={NAV_GROUP_ICONS[group] ?? FALLBACK_GROUP_ICON} aria-hidden />
        <span className="sm-nav-label sm-nav-group-title" id={labelId}>
          {title}
        </span>
        <i
          className={`sm-nav-caret pi ${drawer ? 'pi-angle-down' : 'pi-angle-right'}`}
          aria-hidden
        />
      </button>
      <div
        id={panelId}
        className={flyout ? 'sm-nav-flyout' : 'sm-nav-inline'}
        hidden={!open}
        style={flyout ? position : undefined}
      >
        {flyout && (
          <p className="sm-nav-flyout-title" aria-hidden>
            {title}
          </p>
        )}
        <ul className="sm-nav" aria-labelledby={labelId}>
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
    </li>
  );
}

/** Infobulle : survol, ou focus au CLAVIER seulement (un toucher ne la laisse pas affichée). */
function keyboardFocusOrHover(event: { type?: string; target?: EventTarget | null } | undefined) {
  if (event?.type !== 'focus' || !(event.target instanceof Element)) return true;
  try {
    return event.target.matches(':focus-visible');
  } catch {
    return true;
  }
}

/** Menu construit à partir des capacités : rubriques et ordre du profil UX du site actif. */
export function SidebarNav({
  modules,
  compact = false,
  onNavigate,
}: {
  modules: readonly FrontendModule[];
  compact?: boolean;
  onNavigate?: () => void;
}) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const { pathname } = useLocation();
  const drawer = useMediaQuery(DRAWER_QUERY);
  const hover = useMediaQuery(HOVER_QUERY);
  const navId = useId();
  const nav = useRef<HTMLElement>(null);
  const sections = useMemo(
    () => buildNavigationSections(modules, capabilities),
    [modules, capabilities],
  );
  const activeGroup = sections.find(
    (s) => s.group !== 'home' && s.items.some((item) => isActive(item, pathname)),
  )?.group;
  const [choice, setChoice] = useState<OpenChoice | null>(null);
  const current = choice?.path === pathname ? choice : null;
  // Sans choix sur cette page : tiroir = rubrique de la page courante dépliée ; sinon fermé.
  const openGroup = current ? current.group : drawer ? (activeGroup ?? null) : null;
  const pinned = current ? current.pinned : drawer;
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cancelClose = () => {
    if (closeTimer.current) clearTimeout(closeTimer.current);
    closeTimer.current = null;
  };
  const close = useCallback(() => {
    cancelClose();
    setChoice({ group: null, pinned: false, path: pathname });
  }, [pathname]);
  useEffect(() => () => cancelClose(), []);

  // Panneau épinglé : fermé par un clic hors de la barre latérale.
  useEffect(() => {
    if (!pinned || openGroup === null || drawer) return undefined;
    const onPointer = (event: MouseEvent) => {
      if (nav.current && !nav.current.contains(event.target as Node)) close();
    };
    document.addEventListener('mousedown', onPointer);
    return () => document.removeEventListener('mousedown', onPointer);
  }, [pinned, openGroup, drawer, close]);

  const later = (action: () => void) => {
    cancelClose();
    closeTimer.current = setTimeout(action, HOVER_CLOSE_DELAY_MS);
  };
  const hoverOpen = (group: string) => {
    cancelClose();
    if (openGroup === group) return;
    const show = () => setChoice({ group, pinned: false, path: pathname });
    // Un autre panneau est ouvert : bascule différée, annulée si le curseur rejoint ce panneau
    // (un trajet en diagonale qui traverse d'autres rubriques ne le remplace pas).
    if (openGroup === null) show();
    else later(show);
  };
  // Curseur sorti : un panneau ouvert au survol se referme après le délai (jamais un épinglé).
  const hoverLeave = () =>
    later(() =>
      setChoice((c) => (c && c.path === pathname && !c.pinned ? { ...c, group: null } : c)),
    );
  // Fermé : ouvre (épinglé) ; ouvert au survol : épingle ; épinglé : referme.
  const toggle = (group: string) => {
    cancelClose();
    if (openGroup === group && pinned) setChoice({ group: null, pinned: false, path: pathname });
    else setChoice({ group, pinned: true, path: pathname });
  };

  return (
    <nav
      ref={nav}
      aria-label={t('layout.menu')}
      className="sm-sidebar-nav"
      id={navId}
      data-mode={drawer ? 'drawer' : compact ? 'compact' : 'expanded'}
    >
      <ul className="sm-nav-main-list">
        {sections.map((section) =>
          section.group === 'home' ? (
            section.items.map((item) => (
              <DirectLink
                key={item.key}
                item={item}
                compact={compact && !drawer}
                onNavigate={onNavigate}
              />
            ))
          ) : (
            <GroupEntry
              key={section.group}
              section={section}
              open={openGroup === section.group}
              active={section.group === activeGroup}
              compact={compact && !drawer}
              drawer={drawer}
              hover={hover}
              onHoverOpen={() => hoverOpen(section.group)}
              onHoverLeave={hoverLeave}
              onToggle={() => toggle(section.group)}
              onClose={close}
              onNavigate={() => {
                close();
                onNavigate?.();
              }}
            />
          ),
        )}
      </ul>
      {/* Mode compact : infobulle au survol et au focus de chaque rubrique (libellé masqué). */}
      {compact && !drawer && (
        <Tooltip
          key={sections.map((s) => s.group).join()}
          target={`[id="${navId}"] [data-pr-tooltip]`}
          showDelay={150}
          onBeforeShow={({ originalEvent }) => keyboardFocusOrHover(originalEvent)}
        />
      )}
    </nav>
  );
}
