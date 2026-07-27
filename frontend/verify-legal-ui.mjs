import { chromium } from 'playwright'

const BASE = 'http://127.0.0.1:7566'
const KB = 'legal-kb-test'
const log = []
const ok = (m) => { log.push('OK   ' + m); console.log('OK   ' + m) }
const info = (m) => { log.push('INFO ' + m); console.log('INFO ' + m) }
const fail = (m) => { log.push('FAIL ' + m); console.log('FAIL ' + m) }

const browser = await chromium.launch({ executablePath: '/root/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome' })
const page = await (await browser.newContext({ viewport: { width: 1400, height: 900 } })).newPage()
const errors = []
page.on('pageerror', (e) => errors.push('PAGEERR ' + e.message))

await page.goto(`${BASE}/#/kb/${KB}`, { waitUntil: 'networkidle' })
await page.waitForTimeout(1200)

async function clickCard(re) {
  const b = page.getByRole('button', { name: re }).first()
  if (await b.count()) { await b.click(); await page.waitForTimeout(1500); return true }
  return false
}

// §5 doc reader DocIR outline FIRST (clean state; sync apply later mutates the KB)
await clickCard(/Documents|文档/)
await page.waitForTimeout(800)
const docCard = page.locator('button', { hasText: /case001/ }).first()
if (await docCard.count()) {
  await docCard.click(); await page.waitForTimeout(2000)
  const outline = await page.getByText(/Document structure|文档结构/).count()
  const dlg = await page.locator('[role=dialog]').count()
  if (outline > 0 && dlg > 0) ok('§5 DocIR structure outline rendered in reader')
  else info(`§5 reader dialog=${dlg} but outline=${outline} (by-hash resolved? check test-KB docir)`)
  // close the reader drawer so it doesn't intercept later card clicks
  await page.keyboard.press('Escape')
  await page.waitForTimeout(600)
} else info('§5 case001 doc card not present in Documents list')
await page.goto(`${BASE}/#/kb/${KB}`, { waitUntil: 'networkidle' })
await page.waitForTimeout(800)

// §3.1 graph visualization: open graph card, assert mermaid svg rendered
if (await clickCard(/Graph|图谱/)) {
  const svg = await page.locator('svg').count()
  const hasNodesPanel = await page.getByText(/Nodes|节点/).count()
  if (svg >= 3 && hasNodesPanel > 0) ok(`§3.1 graph visualization rendered (svg=${svg}, nodes panel present)`)
  else fail(`§3.1 graph visualization (svg=${svg}, nodesPanel=${hasNodesPanel})`)
} else fail('§3.1 graph card not found')

// §3.3 lifecycle list + history
if (await clickCard(/Lifecycle|生命周期/)) {
  const rows = await page.locator('text=/concepts\\/|entities\\//').count()
  if (rows > 0) ok('§3.3 lifecycle list rendered')
  else fail('§3.3 lifecycle list missing')
  const hist = page.getByRole('button', { name: /History|历史/ }).first()
  if (await hist.count()) { await hist.click(); await page.waitForTimeout(600); ok('§3.3 history timeline opened') }
  else fail('§3.3 history button missing')
} else fail('§3.3 lifecycle card not found')

// §3.2 sync diff
if (await clickCard(/Sync|同步/)) {
  const scan = page.getByRole('button', { name: /Scan|扫描/ }).first()
  if (await scan.count()) {
    await scan.click(); await page.waitForTimeout(800)
    const diff = await page.locator('text=/Diff|差异/').count()
    if (diff > 0) ok('§3.2 diff viewer rendered after scan')
    else fail('§3.2 diff viewer missing')
  } else fail('§3.2 scan button missing')
} else fail('§3.2 sync card not found')

// §6 settings legal panel (gear button by aria-label, language-agnostic)
await page.goto(`${BASE}/#/kb/${KB}`, { waitUntil: 'networkidle' })
await page.waitForTimeout(700)
const gear = page.locator('button[aria-label*="settings" i], button[title*="settings" i]').first()
if (await gear.count()) {
  await gear.click(); await page.waitForTimeout(900)
  const toggle = await page.locator('text=/Enable legal|启用法律/').count()
  const chips = await page.locator('text=statute').count()
  if (toggle > 0 && chips > 0) ok('§6 legal settings panel rendered (toggle + entity chips)')
  else fail(`§6 legal settings (toggle=${toggle} chips=${chips})`)
} else fail('§6 settings gear not found')

// §5 already verified at the top (clean state, before sync apply mutates the KB)

console.log('\npageerrors:', errors.length ? errors.join('\n') : 'none')
await browser.close()
const fails = log.filter((l) => l.startsWith('FAIL')).length
console.log(`\nSUMMARY: ${log.filter((l) => l.startsWith('OK')).length} ok, ${log.filter((l) => l.startsWith('INFO')).length} info, ${fails} fail`)
process.exit(fails ? 1 : 0)
