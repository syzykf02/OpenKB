/**
 * Shared visual language for graph entity types. SVG colors stay explicit for
 * D3, while the same type receives a matching badge in the surrounding UI.
 */
export interface GraphNodeStyle {
  fill: string
  stroke: string
  badgeClass: string
}

const TYPE_STYLES: Record<string, GraphNodeStyle> = {
  statute: {
    fill: '#dbeafe', stroke: '#2563eb',
    badgeClass: 'bg-blue-100 text-blue-700 ring-1 ring-blue-200 dark:bg-blue-500/15 dark:text-blue-300 dark:ring-blue-400/20',
  },
  regulation: {
    fill: '#e0e7ff', stroke: '#4f46e5',
    badgeClass: 'bg-indigo-100 text-indigo-700 ring-1 ring-indigo-200 dark:bg-indigo-500/15 dark:text-indigo-300 dark:ring-indigo-400/20',
  },
  case: {
    fill: '#dcfce7', stroke: '#16a34a',
    badgeClass: 'bg-emerald-100 text-emerald-700 ring-1 ring-emerald-200 dark:bg-emerald-500/15 dark:text-emerald-300 dark:ring-emerald-400/20',
  },
  concept: {
    fill: '#f3e8ff', stroke: '#9333ea',
    badgeClass: 'bg-purple-100 text-purple-700 ring-1 ring-purple-200 dark:bg-purple-500/15 dark:text-purple-300 dark:ring-purple-400/20',
  },
  court: {
    fill: '#ffedd5', stroke: '#ea580c',
    badgeClass: 'bg-orange-100 text-orange-700 ring-1 ring-orange-200 dark:bg-orange-500/15 dark:text-orange-300 dark:ring-orange-400/20',
  },
  judge: {
    fill: '#ffe4e6', stroke: '#e11d48',
    badgeClass: 'bg-rose-100 text-rose-700 ring-1 ring-rose-200 dark:bg-rose-500/15 dark:text-rose-300 dark:ring-rose-400/20',
  },
  precedent: {
    fill: '#fef3c7', stroke: '#d97706',
    badgeClass: 'bg-amber-100 text-amber-700 ring-1 ring-amber-200 dark:bg-amber-500/15 dark:text-amber-300 dark:ring-amber-400/20',
  },
  evidence: {
    fill: '#ccfbf1', stroke: '#0f766e',
    badgeClass: 'bg-teal-100 text-teal-700 ring-1 ring-teal-200 dark:bg-teal-500/15 dark:text-teal-300 dark:ring-teal-400/20',
  },
  doctrine: {
    fill: '#cffafe', stroke: '#0891b2',
    badgeClass: 'bg-cyan-100 text-cyan-700 ring-1 ring-cyan-200 dark:bg-cyan-500/15 dark:text-cyan-300 dark:ring-cyan-400/20',
  },
  person: {
    fill: '#fae8ff', stroke: '#c026d3',
    badgeClass: 'bg-fuchsia-100 text-fuchsia-700 ring-1 ring-fuchsia-200 dark:bg-fuchsia-500/15 dark:text-fuchsia-300 dark:ring-fuchsia-400/20',
  },
  organization: {
    fill: '#e0f2fe', stroke: '#0284c7',
    badgeClass: 'bg-sky-100 text-sky-700 ring-1 ring-sky-200 dark:bg-sky-500/15 dark:text-sky-300 dark:ring-sky-400/20',
  },
  document: {
    fill: '#f1f5f9', stroke: '#64748b',
    badgeClass: 'bg-slate-100 text-slate-700 ring-1 ring-slate-200 dark:bg-slate-500/15 dark:text-slate-300 dark:ring-slate-400/20',
  },
}

const DEFAULT_STYLE: GraphNodeStyle = {
  fill: '#f1f5f9',
  stroke: '#64748b',
  badgeClass: 'bg-muted text-muted-foreground ring-1 ring-[hsl(var(--glass-border))]',
}

/** Stable colors for known types, with a neutral fallback for custom types. */
export function graphNodeStyle(type: string): GraphNodeStyle {
  return TYPE_STYLES[type.toLowerCase()] ?? DEFAULT_STYLE
}
