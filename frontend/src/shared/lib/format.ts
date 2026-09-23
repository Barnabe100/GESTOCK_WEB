export function formatDate(iso: string, locale = 'fr', timeZone?: string): string {
  return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeZone }).format(new Date(iso));
}

export function formatDateTime(iso: string, locale = 'fr', timeZone?: string): string {
  return new Intl.DateTimeFormat(locale, {
    dateStyle: 'short',
    timeStyle: 'medium',
    timeZone,
  }).format(new Date(iso));
}
