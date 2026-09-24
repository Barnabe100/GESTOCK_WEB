# Module Clients (Phase 2.3)

Référentiel **générique et multi-secteur** des clients d'une entreprise (tenant), socle des
futurs modules ventes, ventes à crédit, créances, paiements, POS, rapports et
restauration. Code du module : `customers` (déjà déclaré dans le registre, les profils et les
plans avant cette phase ; identifiants en anglais, libellés « Clients » via i18n).

## 1. Modèle

| Champ | Règle |
|---|---|
| `code` | Référence `CLI-000001`, attribuée par le serveur (séquence `customer` de `document_sequences`, par tenant, atomique, annulée avec la transaction : pas de trou sur échec). **Unique par tenant**, immuable. |
| `customer_type` | `INDIVIDUAL` (Particulier) ou `BUSINESS` (Entreprise), obligatoire ; libellés traduits côté interface. |
| `name` | Obligatoire (150) : nom et prénom d'un particulier, nom usuel / enseigne d'une entreprise. |
| `legal_name` | Raison sociale (200), saisie pour une entreprise. |
| `tax_id` | Identifiant fiscal (IFU, NIF…) : factures et créances futures. |
| `phone`, `phone2` | Normalisés : séparateurs (espaces, points, tirets, parenthèses, `/`) retirés, `+` initial conservé, 4 à 20 chiffres. `+226 70 11 22 33` → `+22670112233`. |
| `email` | Validé et normalisé (domaine en minuscules). |
| `address`, `city`, `country`, `notes` | Facultatifs ; chaîne vide = champ effacé. |
| `credit_limit` | Plafond de crédit facultatif (`NUMERIC(18,2)`, ≥ 0). **Préparatoire** : aucune règle ne l'applique avant le module de vente à crédit. |
| `is_active` | Vrai à la création ; désactivation logique. |

**Aucun solde n'est stocké sur le client** : le montant dû sera calculé à partir des créances
et paiements (source de vérité unique, pas de dénormalisation incohérente).

## 2. Décisions

- **Tenant, pas site.** Un client appartient à l'entreprise, partagé par tous ses sites
  (un même client achète en boutique et au dépôt). Aucun rattachement client/site n'est
  créé : les futures ventes, créances et paiements porteront **leur** `site_id`, ce qui
  permettra de savoir où chaque opération a eu lieu, sans dupliquer le client par site.
- **Unicité.** Seul le code est unique (`UNIQUE (tenant_id, code)`). Téléphone et email
  **ne sont pas uniques** : plusieurs membres d'une même famille ou entreprise peuvent les
  partager ; une contrainte bloquerait des cas réels. Le nom n'est pas unique (homonymes).
- **Désactivation, jamais de suppression.** Un client inactif reste consultable (historique,
  futures ventes passées) mais n'est plus proposé pour une nouvelle opération commerciale ;
  les futurs modules le vérifient via `customers/api.py` (`CustomerRef.is_active`). Le rôle
  applicatif n'a pas le droit `DELETE` sur la table.
- **Recherche caisse / POS** : « contient », casse ignorée, sur code, nom, raison sociale,
  téléphones et email ; un terme ressemblant à un numéro (`70 11 22`) est comparé aux
  téléphones normalisés. Index trigrammes `pg_trgm` ([ADR-0016](../adr/0016-recherche-trigrammes.md)).

## 3. Permissions et rôles de base

| Permission | Nature | Administrateur | Gestionnaire | Vendeur | Consultant |
|---|---|---|---|---|---|
| `customers.customer.view` | read | ✓ | ✓ | ✓ (sélection en vente) | ✓ |
| `customers.customer.create` | write | ✓ | ✓ | — | — |
| `customers.customer.update` | write | ✓ | ✓ | — | — |
| `customers.customer.status` | write (activer / désactiver) | ✓ | ✓ | — | — |

Les permissions sont la seule source d'autorisation ; un rôle personnalisé peut combiner ces
droits à volonté (ex. un « Caissier » autorisé à créer des clients). Le module n'est visible
que s'il est effectif (profil ∩ plan ∩ activation) ; un abonnement expiré laisse la
consultation et bloque les écritures (politique d'abonnement, ADR-0011).

## 4. API

Voir [`API.md`](API.md) : `GET/POST /customers`, `GET/PATCH /customers/{id}`,
`POST /customers/{id}/activate|deactivate`. Audit : `customer.created`,
`customer.updated` (avant/après par champ), `customer.activated`, `customer.deactivated`,
dans la transaction de l'opération.

## 5. Intégration avec les modules commerciaux

La ligne « Ventes » est réalisée en Phase 2.4 ([`SALES.md`](SALES.md)) : client facultatif,
actif exigé à l'enregistrement et à la validation. Les autres restent à venir.

```text
Ventes     : Client ─► Vente (site_id, customer_id FK composite) ─► Lignes ─► StockService
Crédit     : Client ─► Vente à crédit ─► Créance ─► Paiements   (plafond : credit_limit)
POS        : Client (recherche rapide, facultatif) ─► Panier ─► Encaissement
Restaurant : Client facultatif sur une commande
```

Les modules futurs référencent `customers(tenant_id, id)` par clé étrangère composite
(contrainte `UNIQUE (tenant_id, id)` déjà en place) et passent par `customers/api.py` ; le
module Clients ne dépend d'aucun d'eux. La fiche client affichera les indicateurs (achats,
montant dû, dernière vente…) quand ces données existeront — aucun chiffre fictif d'ici là.

**Hors périmètre de cette phase** : ventes, caisse, POS, paiements, créances, fidélité,
restaurant, import Excel, export PDF, comptabilité.
