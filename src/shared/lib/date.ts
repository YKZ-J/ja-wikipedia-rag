const TOKYO_LOCALE = "en-CA";
const TOKYO_TIMEZONE = "Asia/Tokyo";

/**
 * Returns a stable YYYY-MM-DD date string in Asia/Tokyo timezone.
 */
export function getTokyoDateString(date = new Date()): string {
  return new Intl.DateTimeFormat(TOKYO_LOCALE, {
    timeZone: TOKYO_TIMEZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}
