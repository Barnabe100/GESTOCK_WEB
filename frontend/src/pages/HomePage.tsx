import { Card } from 'primereact/card';
import { ProgressSpinner } from 'primereact/progressspinner';
import { Tag } from 'primereact/tag';

import { useHealth } from '@/core/health';

export function HomePage() {
  const health = useHealth();

  return (
    <main style={{ maxWidth: 640, margin: '4rem auto', padding: '0 1rem' }}>
      <Card title="StockManager Web" subTitle="TechNova — squelette technique">
        <p>Aucune fonctionnalité métier n'est encore implémentée.</p>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <span>API :</span>
          {health.isPending && <ProgressSpinner style={{ width: 20, height: 20 }} />}
          {health.isSuccess && <Tag severity="success" value={`OK — v${health.data.version}`} />}
          {health.isError && <Tag severity="danger" value="Injoignable" />}
        </div>
      </Card>
    </main>
  );
}
