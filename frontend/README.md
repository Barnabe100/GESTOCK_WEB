# StockManager Web — Frontend

SPA React + TypeScript (Vite). Voir [l'architecture](../docs/architecture/ARCHITECTURE.md) (§7).

## Commandes

```bash
npm install
npm run dev            # http://localhost:5173 (proxy /api -> http://localhost:8000)
npm run lint
npm run format:check
npm run typecheck
npm test
npm run build
```

Configuration : variables `VITE_*` (voir `.env.example`).

## Organisation

| Dossier        | Contenu                                                    |
| -------------- | ---------------------------------------------------------- |
| `src/app/`     | Amorçage : providers, routeur, client de requêtes          |
| `src/core/`    | Client API, (à venir) auth, capacités, registre de modules |
| `src/modules/` | Modules fonctionnels — à partir de la phase 1/2            |
| `src/shared/`  | Composants UI et utilitaires génériques                    |
| `src/pages/`   | Pages hors module                                          |

Rappel : le frontend n'est **pas** une frontière de sécurité et ne porte aucune
logique métier faisant foi.
