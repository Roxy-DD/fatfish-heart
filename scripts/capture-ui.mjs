/**
 * capture-ui.mjs — 给 README 抓 UI 截图
 *
 * ============================================================
 * 为什么需要这个
 * ============================================================
 * FISH-18 调研结论：mem0 / Letta 这类高星项目第一屏就有 demo 视觉，
 * 而我们没有任何截图。纯文字 README 对普通用户来说「想象不出界面」。
 *
 * ============================================================
 * 做法
 * ============================================================
 * 插件 UI 是注入 DSH Web 的，没有独立页面。所以：
 *   1. 打开 DSH Web（本地地址 + 端口）
 *   2. 等页面加载
 *   3. 找到插件的 Tab（肥鱼之心 / 记忆）并点击
 *   4. 截图
 *
 * 若 DSH 需要登录而无法自动进入，本脚本会明确失败并说明原因，
 * 不会伪造一张空白图冒充成功。
 *
 * 用法：
 *   node scripts/capture-ui.mjs [baseUrl] [outDir]
 */
import { chromium } from 'playwright'
import { mkdirSync, existsSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')

const BASE = process.argv[2] || 'http://127.0.0.1:52385'
const OUT = process.argv[3] || join(ROOT, 'docs', 'images')

mkdirSync(OUT, { recursive: true })

console.log('='.repeat(66))
console.log('抓取插件 UI 截图')
console.log('='.repeat(66))
console.log(`  DSH: ${BASE}`)
console.log(`  输出: ${OUT}`)

const browser = await chromium.launch({ headless: true })
const ctx = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 2,          // 高清，README 里不糊
  locale: 'zh-CN',
})
const page = await ctx.newPage()

let ok = false
try {
  console.log('\n[1] 打开 DSH Web')
  const resp = await page.goto(BASE, {
    waitUntil: 'domcontentloaded',
    timeout: 45000,
  }).catch((e) => { throw new Error('打开失败: ' + e.message) })
  console.log(`  HTTP ${resp ? resp.status() : '?'}`)

  // 等应用壳挂载（DSH 是 SPA，需要 __DSH_BOOT__）
  await page.waitForTimeout(3000)

  const bodyText = await page.evaluate(() => document.body?.innerText || '')
  console.log(`  页面文本长度: ${bodyText.length}`)

  if (bodyText.length < 20) {
    throw new Error('页面几乎是空的 —— 可能需要登录，或 DSH 未注入 boot')
  }

  console.log('\n[2] 找插件入口')
  // 插件可能在侧栏或设置里，先尝试几种常见文本
  const candidates = ['肥鱼之心', '记忆', 'cosmos-mem', 'Fat Fish']
  let clicked = false
  for (const label of candidates) {
    const loc = page.getByText(label, { exact: false }).first()
    const n = await loc.count().catch(() => 0)
    if (n > 0) {
      try {
        await loc.click({ timeout: 5000 })
        console.log(`  点到了「${label}」`)
        clicked = true
        break
      } catch { /* 试下一个 */ }
    }
  }
  if (!clicked) {
    console.log('  ⚠ 没找到插件入口文本，直接截全屏（也有参考价值）')
  }

  await page.waitForTimeout(2000)

  console.log('\n[3] 截图')
  const mainShot = join(OUT, 'ui-overview.png')
  await page.screenshot({ path: mainShot, fullPage: false })
  console.log(`  ✅ ${mainShot} (${existsSync(mainShot) ? '已生成' : '失败'})`)

  // 全页截图（滚动内容更完整）
  const fullShot = join(OUT, 'ui-full.png')
  await page.screenshot({ path: fullShot, fullPage: true })
  console.log(`  ✅ ${fullShot}`)

  ok = true
} catch (e) {
  console.error(`\n❌ 截图失败: ${e.message}`)
  console.error(`   这不是「看起来不好」的问题，是真的没抓到。`)
  console.error(`   可能原因：DSH 需要登录 / 端口变了 / 插件未加载。`)
} finally {
  await browser.close()
}

console.log('\n' + '='.repeat(66))
console.log(ok ? '完成' : '未完成（见上方原因）')
console.log('='.repeat(66))
process.exit(ok ? 0 : 1)
