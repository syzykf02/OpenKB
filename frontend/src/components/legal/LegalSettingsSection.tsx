import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { getKbConfig, patchKbConfig } from '@/api/kb'
import { ApiError } from '@/api/client'

// Legal entity types the toggle injects into the KB's entity_types (mirrors
// openkb.legal.schema.LEGAL_ENTITY_TYPES subset surfaced to the compiler).
const LEGAL_ENTITY_TYPES = ['statute', 'case', 'court', 'judge', 'precedent', 'evidence', 'doctrine']

/** Legal-KB settings panel (UI_INTEGRATION_PLAN §6).
 *  - toggle to enable legal entity types (persisted to KB config entity_types)
 *  - read-only schema summary + visual-analysis & lifecycle policy notes. */
export default function LegalSettingsSection({ kb }: { kb: string }) {
  const { t } = useTranslation('legal')
  const [enabled, setEnabled] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    getKbConfig(kb)
      .then((c) => {
        if (cancelled) return
        setEnabled(LEGAL_ENTITY_TYPES.some((x) => c.entity_types?.includes(x)))
      })
      .catch(() => {
        if (!cancelled) setEnabled(false)
      })
      .finally(() => {
        if (!cancelled) setLoaded(true)
      })
    return () => {
      cancelled = true
    }
  }, [kb])

  const toggle = async () => {
    setBusy(true)
    setError(null)
    try {
      const c = await getKbConfig(kb)
      const cur = new Set(c.entity_types ?? [])
      if (enabled) LEGAL_ENTITY_TYPES.forEach((x) => cur.delete(x))
      else LEGAL_ENTITY_TYPES.forEach((x) => cur.add(x))
      await patchKbConfig(kb, { config: { entity_types: [...cur] } })
      setEnabled(!enabled)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (!loaded) return null
  return (
    <div className="space-y-3 pt-2">
      <h3 className="text-[12px] font-semibold text-muted-foreground tracking-wide">{t('settings.heading')}</h3>
      <div className="rounded-2xl border border-[hsl(var(--glass-border))] glass-2 px-4 py-3.5 space-y-3">
        <label className="flex cursor-pointer items-center gap-2">
          <input type="checkbox" checked={enabled} disabled={busy} onChange={toggle} className="h-4 w-4" />
          <span className="text-[12.5px] font-medium text-foreground">{t('settings.enable')}</span>
        </label>
        {error && <p className="text-[12px] text-red-500">{error}</p>}
        <p className="text-[12px] text-muted-foreground">{t('settings.enableDesc')}</p>
        <div>
          <p className="text-[11px] font-medium text-muted-foreground">{t('settings.entityTypes')}</p>
          <div className="mt-1 flex flex-wrap gap-1">
            {LEGAL_ENTITY_TYPES.map((x) => (
              <span
                key={x}
                className={`rounded px-1.5 py-0.5 text-[10px] ${enabled ? 'bg-indigo-100 text-indigo-700 dark:bg-indigo-500/15 dark:text-indigo-300' : 'bg-muted text-muted-foreground'}`}
              >
                {x}
              </span>
            ))}
          </div>
        </div>
      </div>

      <div className="rounded-2xl border border-[hsl(var(--glass-border))] glass-2 px-4 py-3.5 space-y-2">
        <p className="text-[11px] font-medium text-muted-foreground">{t('settings.visionHeading')}</p>
        <p className="text-[12px] text-muted-foreground">{t('settings.visionDesc')}</p>
      </div>

      <div className="rounded-2xl border border-[hsl(var(--glass-border))] glass-2 px-4 py-3.5 space-y-2">
        <p className="text-[11px] font-medium text-muted-foreground">{t('settings.lifecycleHeading')}</p>
        <ul className="space-y-1 text-[12px] text-muted-foreground">
          <li>· {t('settings.decaySlow')}</li>
          <li>· {t('settings.decayMedium')}</li>
          <li>· {t('settings.decayFast')}</li>
        </ul>
      </div>
    </div>
  )
}
