import { Link } from 'react-router';

import type { Tone } from './StatusBadge';

/** Indicateur clé du tableau de bord : valeur, libellé, lien éventuel vers le détail. */
export function MetricCard({
  label,
  value,
  icon,
  tone = 'neutral',
  hint,
  to,
}: {
  label: string;
  value: string | number;
  icon: string;
  tone?: Tone;
  hint?: string;
  to?: string;
}) {
  const content = (
    <>
      <span className={`sm-metric-icon sm-metric-icon--${tone}`} aria-hidden>
        <i className={icon} />
      </span>
      <span className="sm-metric-body">
        <span className="sm-metric-value">{value}</span>
        <span className="sm-metric-label">{label}</span>
        {hint && <span className="sm-metric-hint">{hint}</span>}
      </span>
    </>
  );
  return to ? (
    <Link to={to} className="sm-metric sm-metric--link">
      {content}
    </Link>
  ) : (
    <div className="sm-metric">{content}</div>
  );
}
