import { useTranslation } from 'react-i18next';

import { useBusinessProfileTheme } from '@/core/theme/useBusinessProfileTheme';
import { ProfileIllustration } from '@/shared/ui/ProfileIllustration';

/**
 * En-tête du tableau de bord (palier E) : site actif, profil DU SITE et illustration du thème
 * métier. Vue « Tous les sites » : profil du site de référence, présenté comme tel.
 */
export function SiteProfileHero() {
  const { t } = useTranslation();
  const theme = useBusinessProfileTheme();
  const reference = theme.labels.scope === 'reference';
  return (
    <section className="sm-site-hero" data-testid="site-hero" aria-label={t('dashboard.siteHero')}>
      <ProfileIllustration
        icon={theme.visuals.illustration.icon}
        motifs={theme.visuals.illustration.motifs}
      />
      <div className="sm-site-hero-text">
        <small className="sm-muted">
          {t(reference ? 'dashboard.allSitesHero' : 'dashboard.siteHero')}
        </small>
        <strong>{theme.labels.site ?? t('layout.allSites')}</strong>
        <span data-testid="site-hero-profile">
          {theme.labels.profile ?? t('layout.unknownProfile')}
        </span>
        {theme.labels.sector && <small className="sm-muted">{theme.labels.sector}</small>}
        {reference && <small className="sm-muted">{t('dashboard.referenceHint')}</small>}
      </div>
    </section>
  );
}
