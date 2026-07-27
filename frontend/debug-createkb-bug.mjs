import { chromium } from 'playwright'

const BASE = 'http://127.0.0.1:7566'
const browser = await chromium.launch({
  executablePath: '/root/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome',
})
const page = await (await browser.newContext({ viewport: { width: 1200, height: 800 } })).newPage()
await page.goto(`${BASE}/#/kb`, { waitUntil: 'networkidle' })
await page.waitForTimeout(800)
await page.getByRole('button', { name: /新建知识库|New knowledge base/i }).first().click()
await page.waitForTimeout(800)

// Inspect the toggle button's position and what elementFromPoint hits at its center
const info = await page.evaluate(() => {
  const toggle = [...document.querySelectorAll('button')].find((b) =>
    /自定义路径|Custom path/i.test(b.textContent),
  )
  if (!toggle) return { found: false }
  const r = toggle.getBoundingClientRect()
  const cx = r.left + r.width / 2
  const cy = r.top + r.height / 2
  const hit = document.elementFromPoint(cx, cy)
  // find the Dialog.Content (motion.div) ancestor chain
  const content = document.querySelector('[data-state][role="dialog"], [role="dialog"]')
  return {
    found: true,
    toggleRect: { x: r.x, y: r.y, w: r.width, h: r.height },
    toggleTransform: getComputedStyle(toggle).transform,
    hitTag: hit ? hit.tagName + (hit.className ? '.' + String(hit.className).slice(0, 60) : '') : 'null',
    hitIsToggle: hit === toggle,
    hitInsideContent: content ? content.contains(hit) : 'no-content-found',
    contentRole: content ? content.getAttribute('role') : null,
    contentTransform: content ? getComputedStyle(content).transform : null,
    contentRect: content ? (() => { const c = content.getBoundingClientRect(); return { x: c.x, y: c.y, w: c.width, h: c.height } })() : null,
  }
})
console.log(JSON.stringify(info, null, 2))
await browser.close()
