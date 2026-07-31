/**
 * Distinct badge colors for entity types (each entity page's frontmatter
 * `type`, lowercased by the backend). Default OpenKB types get stable colors
 * (person/organization match the graph's palette); custom types get a
 * deterministic pick from the fallback palette so every type reads differently.
 */
const TYPE_BADGES: Record<string, string> = {
  person:
    'bg-fuchsia-100 text-fuchsia-700 ring-1 ring-fuchsia-200 dark:bg-fuchsia-500/15 dark:text-fuchsia-300 dark:ring-fuchsia-400/20',
  organization:
    'bg-sky-100 text-sky-700 ring-1 ring-sky-200 dark:bg-sky-500/15 dark:text-sky-300 dark:ring-sky-400/20',
  place:
    'bg-emerald-100 text-emerald-700 ring-1 ring-emerald-200 dark:bg-emerald-500/15 dark:text-emerald-300 dark:ring-emerald-400/20',
  product:
    'bg-purple-100 text-purple-700 ring-1 ring-purple-200 dark:bg-purple-500/15 dark:text-purple-300 dark:ring-purple-400/20',
  work: 'bg-amber-100 text-amber-700 ring-1 ring-amber-200 dark:bg-amber-500/15 dark:text-amber-300 dark:ring-amber-400/20',
  event:
    'bg-rose-100 text-rose-700 ring-1 ring-rose-200 dark:bg-rose-500/15 dark:text-rose-300 dark:ring-rose-400/20',
  other:
    'bg-slate-100 text-slate-700 ring-1 ring-slate-200 dark:bg-slate-500/15 dark:text-slate-300 dark:ring-slate-400/20',
}

const FALLBACK_PALETTE = [
  'bg-teal-100 text-teal-700 ring-1 ring-teal-200 dark:bg-teal-500/15 dark:text-teal-300 dark:ring-teal-400/20',
  'bg-indigo-100 text-indigo-700 ring-1 ring-indigo-200 dark:bg-indigo-500/15 dark:text-indigo-300 dark:ring-indigo-400/20',
  'bg-cyan-100 text-cyan-700 ring-1 ring-cyan-200 dark:bg-cyan-500/15 dark:text-cyan-300 dark:ring-cyan-400/20',
  'bg-lime-100 text-lime-700 ring-1 ring-lime-200 dark:bg-lime-500/15 dark:text-lime-300 dark:ring-lime-400/20',
  'bg-orange-100 text-orange-700 ring-1 ring-orange-200 dark:bg-orange-500/15 dark:text-orange-300 dark:ring-orange-400/20',
  'bg-pink-100 text-pink-700 ring-1 ring-pink-200 dark:bg-pink-500/15 dark:text-pink-300 dark:ring-pink-400/20',
  'bg-violet-100 text-violet-700 ring-1 ring-violet-200 dark:bg-violet-500/15 dark:text-violet-300 dark:ring-violet-400/20',
]

/** Deterministic hash so the same custom type always maps to the same color. */
function hashString(s: string): number {
  let h = 0
  for (let i = 0; i < s.length; i++) {
    h = (h * 31 + s.charCodeAt(i)) >>> 0
  }
  return h
}

export function entityTypeBadgeClass(type: string): string {
  const key = type.toLowerCase()
  return TYPE_BADGES[key] ?? FALLBACK_PALETTE[hashString(key) % FALLBACK_PALETTE.length]
}
