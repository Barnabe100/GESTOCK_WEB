/**
 * Identifiant UUID v4 aléatoire (clés d'idempotence des requêtes).
 *
 * `crypto.randomUUID()` n'existe que dans un contexte SÉCURISÉ (HTTPS ou `localhost`) : ouverte
 * par l'adresse IP du serveur sur le réseau local en HTTP (téléphone, tablette de caisse),
 * l'interface n'en dispose pas et l'appel lève une erreur. `crypto.getRandomValues()`, lui, est
 * disponible dans tous les contextes et fournit le même aléa cryptographique : il prend le relais.
 * Jamais `Math.random()` : une clé prévisible ne doit pas pouvoir être rejouée par un tiers.
 */
export function newUuid(): string {
  const cryptoApi: Crypto | undefined = globalThis.crypto;
  if (typeof cryptoApi?.randomUUID === 'function') return cryptoApi.randomUUID();
  if (typeof cryptoApi?.getRandomValues !== 'function') {
    throw new Error('Générateur aléatoire cryptographique indisponible');
  }
  const bytes = cryptoApi.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6]! & 0x0f) | 0x40; // version 4
  bytes[8] = (bytes[8]! & 0x3f) | 0x80; // variante RFC 4122
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
