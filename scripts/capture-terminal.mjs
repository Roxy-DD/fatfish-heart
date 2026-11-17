/**
 * capture-terminal.mjs — 用真实命令输出生成 README 用的终端截图
 *
 * ============================================================
 * 为什么不用 DSH Web 截图
 * ============================================================
 * 尝试过：DSH Web 需要 URL 里的鉴权 token，裸地址返回 401
 * （"dsh web authentication required; reopen the URL printed by dsh web"）。
 * 而拿 token 要读用户的鉴权凭据 —— 那是隐私边界，不该为了 README 图片去碰。
 *
 * 替代方案（更好）：
 *   用户真正会看到的是**终端输出**：doctor 的自检结果、回归通过情况。
 *   这些是真实命令跑出来的，截图不掺假，也不需要任何鉴权。
 *
 * ============================================================
 * 做法
 * ============================================================
 *   1. 真实执行命令（doctor.py / iterate.py），捕获真实 stdout
 *   2. 渲染成深色终端风格的 HTML
 *   3. playwright 截图 → PNG
 *   4. 存到 docs/images/
 *
 * 关键：图里的每一行都是**真实跑出来的输出**，不是手写的假样例。
 */
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, readdirSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')
const OUT = join(ROOT, 'docs', 'images')
mkdirSync(OUT, { recursive: true })

console.log('='.repeat(66))
console.log('生成终端截图（真实命令输出）')
console.log('='.repeat(66))

function run(cmd, args, opts = {}) {
  try {
    return execFileSync(cmd, args, {
      cwd: opts.cwd || ROOT,
      encoding: 'utf-8',
      stdio: ['ignore', 'pipe', 'pipe'],
      timeout: opts.timeout || 300000,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', KMP_DUPLICATE_LIB_OK: 'TRUE' },
    })
  } catch (e) {
    return String(e.stdout || '') + String(e.stderr || '')
  }
}

// ---------- 1. 真实执行 doctor ----------
//
// ★ 路径坑：cwd 已经是 proto/，所以参数只能是 'doctor.py'
//   （第一版写成 'proto/doctor.py' → 拼成 proto/proto/doctor.py → 报错）
console.log('\n[1] 跑 doctor.py')
const doctorOut = run('python', ['doctor.py'], {
  cwd: join(ROOT, 'proto'),
})
const doctorLines = doctorOut.split('\n').filter((l) => l.trim()).slice(-16)
console.log(`  捕获 ${doctorLines.length} 行`)

// ---------- 2. 真实跑一次回归 ----------
//
// ★ 标签坑：iterate.py check <tag> 会按标签筛测试，
//   随便起的标签可能匹配到不相关的失败测试（第一版就这样截到了红的）。
//   所以这里用完整 check（无标签），拿全绿的真实结果。
console.log('\n[2] 跑全量回归')
const regOut = run('python', ['iterate.py', 'check'], {
  cwd: join(ROOT, 'proto'),
  timeout: 1800000,
})
const allReg = regOut.split('\n').filter((l) => l.trim())
// 取尾部：结论行附近
const regLines = allReg.slice(-18)
console.log(`  捕获 ${regLines.length} 行`)

// ---------- 3. 渲染 HTML ----------
const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

function termBlock(title, lines) {
  const body = lines.map((l) => {
    let cls = ''
    if (l.includes('✅')) cls = 'ok'
    else if (l.includes('❌')) cls = 'err'
    else if (l.startsWith('==')) cls = 'sep'
    return `<div class="${cls}">${esc(l) || '&nbsp;'}</div>`
  }).join('')
  return `
  <div class="term">
    <div class="bar"><span class="dot r"></span><span class="dot y"></span><span class="dot g"></span>
      <span class="title">${esc(title)}</span></div>
    <div class="body">${body}</div>
  </div>`
}

const html = `<!doctype html><html><head><meta charset="utf-8">
<style>
  body { margin:0; padding:36px; background:#0d1117;
         font-family:'Cascadia Mono','Consolas','Menlo',monospace; }
  .term { background:#161b22; border:1px solid #30363d; border-radius:10px;
          margin-bottom:26px; overflow:hidden;
          box-shadow:0 10px 30px rgba(0,0,0,.5); }
  .bar { display:flex; align-items:center; gap:8px; padding:11px 16px;
         background:#21262d; border-bottom:1px solid #30363d; }
  .dot { width:12px; height:12px; border-radius:50%; display:inline-block; }
  .r { background:#ff5f56; } .y { background:#ffbd2e; } .g { background:#27c93f; }
  .title { color:#8b949e; font-size:13px; margin-left:12px; }
  .body { padding:18px 20px; font-size:14.5px; line-height:1.75; color:#c9d1d9; }
  .ok { color:#3fb950; } .err { color:#f85149; }
  .sep { color:#58a6ff; font-weight:600; }
</style></head><body>
${termBlock('python proto/doctor.py  —  环境自检', doctorLines)}
${termBlock('python proto/iterate.py check  —  回归验证', regLines)}
</body></html>`

const htmlPath = join(OUT, '_terminal.html')
writeFileSync(htmlPath, html, 'utf-8')

// ---------- 4. 截图 ----------
console.log('\n[3] 截图')

function findChrome() {
  const base = join(process.env.USERPROFILE || process.env.HOME || '',
    'AppData', 'Local', 'ms-playwright')
  if (!existsSync(base)) return undefined
  for (const d of readdirSync(base)) {
    if (!d.startsWith('chromium-')) continue
    for (const rel of ['chrome-win64/chrome.exe', 'chrome-linux/chrome',
                       'chrome-mac/Chromium.app/Contents/MacOS/Chromium']) {
      const p = join(base, d, rel)
      if (existsSync(p)) return p
    }
  }
  return undefined
}

const browser = await chromium.launch({ headless: true, executablePath: findChrome() })
const page = await browser.newPage({
  viewport: { width: 1000, height: 900 },
  deviceScaleFactor: 2,
})
await page.goto('file:///' + htmlPath.replace(/\\/g, '/'))
await page.waitForTimeout(500)

const shot = join(OUT, 'terminal-proof.png')
await page.screenshot({ path: shot, fullPage: true })
await browser.close()

// 清理中间文件
try { execFileSync('cmd', ['/c', 'del', htmlPath], { stdio: 'ignore' }) } catch { /* ignore */ }

const ok = existsSync(shot)
console.log(`  ${ok ? '✅' : '❌'} ${shot}`)
console.log('\n' + '='.repeat(66))
console.log(ok ? '完成 —— 图里每行都是真实命令输出' : '失败')
console.log('='.repeat(66))
process.exit(ok ? 0 : 1)
