/**
 * Illustration du profil d'activité (thème métier, palier E) : icône du profil sur l'accent du
 * site, entourée des motifs des modules mis en avant par le profil. Décorative : aucun texte
 * porteur d'information (le libellé du profil est affiché à côté).
 */
export function ProfileIllustration({
  icon,
  motifs = [],
  size = 'md',
}: {
  icon: string;
  motifs?: string[];
  size?: 'sm' | 'md';
}) {
  return (
    <span className={`sm-profile-illustration sm-profile-illustration--${size}`} aria-hidden>
      <i className={`${icon} sm-profile-illustration-main`} />
      {motifs.map((motif) => (
        <i key={motif} className={`${motif} sm-profile-illustration-motif`} />
      ))}
    </span>
  );
}
