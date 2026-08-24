// Date formatting for values the server has already resolved.
//
// `scheduled_at_local` is a *naive* wall-clock time — the value someone typed,
// in the zone they typed it in. Feeding it to `new Date(...)` would re-read it
// in the browser's zone and silently shift every session by the offset between
// the two. So the parts are pulled out of the string, and anything handed to
// Intl is anchored to UTC and formatted with `timeZone: 'UTC'`, which is what
// keeps the browser's own zone out of the answer.

const NAIVE = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/

const LONG_DATE = new Intl.DateTimeFormat(undefined, {
  weekday: 'long',
  day: 'numeric',
  month: 'long',
  year: 'numeric',
  timeZone: 'UTC',
})

function parts(value) {
  if (!value) return null
  const m = NAIVE.exec(String(value))
  if (!m) return null
  return {
    year: Number(m[1]),
    month: Number(m[2]),
    day: Number(m[3]),
    hour: m[4],
    minute: m[5],
  }
}

/** "2026-08-24 09:01", plus whatever suffix the caller wants on the end. */
function stamp(value, fallback, suffix) {
  const p = parts(value)
  if (!p) return fallback
  const mm = String(p.month).padStart(2, '0')
  const dd = String(p.day).padStart(2, '0')
  return `${p.year}-${mm}-${dd} ${p.hour}:${p.minute}${suffix}`
}

/** "2026-08-24 09:01" */
export function fmtDateTime(value, fallback = '—') {
  return stamp(value, fallback, '')
}

/** A UTC instant, trimmed of microseconds: "2026-08-24 09:01 UTC" */
export function fmtStamp(value, fallback = '—') {
  return stamp(value, fallback, ' UTC')
}

/** "Monday 24 August 2026, 09:01" — the date part in the reader's locale. */
export function fmtLong(value, fallback = '') {
  const p = parts(value)
  if (!p) return fallback
  // Built in UTC and formatted in UTC, so the weekday is the one that was typed.
  const date = LONG_DATE.format(new Date(Date.UTC(p.year, p.month - 1, p.day)))
  return `${date}, ${p.hour}:${p.minute}`
}
