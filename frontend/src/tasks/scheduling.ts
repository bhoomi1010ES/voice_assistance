export const DEFAULT_TIMEZONE = 'UTC';

export function deviceTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || DEFAULT_TIMEZONE;
  } catch {
    return DEFAULT_TIMEZONE;
  }
}

export function dateInputForOffset(days: number, now = new Date()): string {
  const value = new Date(now.getTime());
  value.setDate(value.getDate() + days);
  return [value.getFullYear(), value.getMonth() + 1, value.getDate()]
    .map((part, index) =>
      index === 0 ? String(part) : String(part).padStart(2, '0'),
    )
    .join('-');
}

export function isValidDateInput(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const [year, month, day] = value.split('-').map(Number);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  return (
    parsed.getUTCFullYear() === year &&
    parsed.getUTCMonth() === month - 1 &&
    parsed.getUTCDate() === day
  );
}

export function isValidTimeInput(value: string): boolean {
  if (!/^\d{2}:\d{2}$/.test(value)) return false;
  const [hour, minute] = value.split(':').map(Number);
  return hour >= 0 && hour <= 23 && minute >= 0 && minute <= 59;
}

/**
 * The API interprets a naive timestamp in the supplied IANA timezone. Keeping
 * this value offset-free avoids silently converting it using the phone's zone.
 */
export function buildLocalDateTime(date: string, time: string): string | null {
  if (!isValidDateInput(date) || !isValidTimeInput(time)) return null;
  return `${date}T${time}:00`;
}

export function schedulePreview(
  date: string,
  time: string,
  timezone: string,
): string {
  return `${date || 'Select a date'} at ${time || 'Select a time'} (${
    timezone || 'timezone required'
  })`;
}

export function formatScheduledTime(
  value: string | null,
  timezone: string,
): string {
  if (!value) return 'No due time';
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short',
      timeZone: timezone,
    }).format(new Date(value));
  } catch {
    return `${value} (${timezone})`;
  }
}

export function localInputParts(
  value: string | null,
  timezone: string,
): { date: string; time: string } {
  if (!value) return { date: '', time: '' };
  try {
    const parts = new Intl.DateTimeFormat('en-CA', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
      timeZone: timezone,
    }).formatToParts(new Date(value));
    const values = Object.fromEntries(
      parts.map(part => [part.type, part.value]),
    );
    return {
      date: `${values.year}-${values.month}-${values.day}`,
      time: `${values.hour}:${values.minute}`,
    };
  } catch {
    return { date: '', time: '' };
  }
}

export function recurrenceRule(
  value: 'none' | 'daily' | 'weekly',
): string | null {
  if (value === 'daily') return 'FREQ=DAILY';
  if (value === 'weekly') return 'FREQ=WEEKLY';
  return null;
}

export function recurrenceChoice(
  rule: string | null,
): 'none' | 'daily' | 'weekly' {
  if (!rule) return 'none';
  if (rule.toUpperCase().startsWith('FREQ=DAILY')) return 'daily';
  if (rule.toUpperCase().startsWith('FREQ=WEEKLY')) return 'weekly';
  return 'none';
}
