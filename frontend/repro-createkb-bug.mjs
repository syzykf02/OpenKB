import { chromium } from 'playwright'

const BASE = 'http://127.0.0.1:7566'
const shots = '/tmp/legal-shots'

const browser = await chromium.launch({
  executablePath: '/root/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome',
})
const page = await (await browser.newContext({ viewport: { width: 1200, height: 800 } })).newPage()
page.on('pageerror', (e) => console.log('PAGEERROR:', e.message))

const isDialogVisible = async () =>
  page.locator('#create-kb-name').isVisible().catch(() => false)

await page.goto(`${BASE}/#/kb`, { waitUntil: 'networkidle' })
await page.waitForTimeout(800)
await page.screenshot({ path: `${shots}/bug-00-kblist.png` })

// 1. open the dialog via the "New KB" trigger (either language)
const trigger = page.getByRole('button', { name: /新建知识库|New knowledge base/i }).first()
await trigger.click()
await page.waitForTimeout(600)
console.log('after open, dialog visible:', await isDialogVisible())
await page.screenshot({ path: `${shots}/bug-01-open.png` })

// 2. click the "Custom path" toggle - reported bug: dialog closes instead of expanding
const toggle = page.getByRole('button', { name: /自定义路径|Custom path/i }).first()
await toggle.click()
await page.waitForTimeout(500)
console.log('after clicking toggle, dialog visible:', await isDialogVisible())
await page.screenshot({ path: `${shots}/bug-02-after-toggle.png` })

// 3. if still open, click into the name input (content area, non-toggle)
if (await isDialogVisible()) {
  await page.locator('#create-kb-name').click()
  await page.waitForTimeout(400)
  console.log('after clicking name input, dialog visible:', await isDialogVisible())
  await page.screenshot({ path: `${shots}/bug-03-after-input-click.png` })
}

await browser.close()
