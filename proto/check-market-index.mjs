/**
 * check-market-index.mjs — 验证市场索引符合 DSH 官方 schema
 *
 * ============================================================
 * 为什么这样测（关键）
 * ============================================================
 * 不用我们自己写的校验器 —— 那只是「我以为对」。
 * 直接调用 DSH 自己的 parser（asar 里的 worker.cjs），
 * 它接受才算真通过。这是外部裁判，不是自我评价。
 *
 * 教训来源：开发时我用自己的自检脚本，结果全部通过；
 *   但用 DSH 官方 parser 一跑就报
 *   「title.zh-CN must be keyed by a lowercase language id」
 *   —— 自检漏了 schema 细节，官方 parser 才是判据。
 *
 * 本测试：
 *   1. 生成索引（调 build-market-index.mjs）
 *   2. 用官方 worker.cjs 解析
 *   3. 断言 format/插件名/版本降序/commit 合法
 */
import { execFileSync } from 'node:child_process'
import { readFileSync, existsSync, mkdtempSync, rmSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { tmpdir } from 'node:os'
import { Worker } from 'node:worker_threads'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')
const ASAR = 'C:\\Users\\xuee\\AppData\\Local\\Programs\\DeepSeek Harness\\resources\\app.asar'
const WORKER_IN_ASAR = 'dsh\\node_modules\\@deepseek-ai\\dsh-plugin-market\\lib\\worker.cjs'

const results = []
function check(name, cond, detail = '') {
  results.push({ name, ok: !!cond, detail })
  console.log(`  ${cond ? '✅' : '❌'} ${name}${detail ? '  ' + detail : ''}`)
}

console.log('='.repeat(70))
console.log('市场索引合规性（用 DSH 官方 parser 裁判）')
console.log('='.repeat(70))

// ---------- 1. 生成索引 ----------
console.log('\n【1】生成索引')
let genOk = true
let genOut = ''
try {
  genOut = execFileSync('node', [join(ROOT, 'scripts', 'build-market-index.mjs')], {
    cwd: ROOT, encoding: 'utf-8', stdio: ['ignore', 'pipe', 'pipe'],
  })
} catch (e) {
  genOk = false
  genOut = String(e.stdout || e.message)
}
check('build-market-index.mjs 执行成功', genOk, genOut.split('\n')[0] || '')

const indexPath = join(ROOT, 'dist', 'market-index.json')
check('索引文件已生成', existsSync(indexPath))
if (!existsSync(indexPath)) process.exit(1)

// ---------- 2. 用官方 parser 解析 ----------
console.log('\n【2】DSH 官方 parser 解析')

// 从 asar 提取 worker.cjs 到临时目录
//
// 注意：这条路径依赖本机 DSH 安装位置，在 CI / 别人机器上可能不存在。
// 所以提取失败时**不判失败**，而是降级为「本地 schema 校验」，
// 并明确标注「未经官方 parser 验证」——诚实标注，不假装通过。
const tmp = mkdtempSync(join(tmpdir(), 'e501_'))
let parseResult = null
let extractOk = false
try {
  // ★ Windows 下用 shell 才能跑 npx(.cmd)，但 asar 路径含空格必须加引号
  //   —— 只 shell 不引号：路径被拆开，静默失败
  //   —— 只引号不 shell：spawnSync npx ENOENT
  //   两个坑叠加曾让测试降级通过 = 假绿
  const isWin = process.platform === 'win32'
  if (isWin) {
    execFileSync(
      `npx --yes @electron/asar extract-file "${ASAR}" "${WORKER_IN_ASAR}"`,
      { cwd: tmp, encoding: 'utf-8', stdio: ['ignore', 'pipe', 'ignore'],
        shell: true, timeout: 180000 })
  } else {
    execFileSync('npx', ['--yes', '@electron/asar', 'extract-file',
      ASAR, WORKER_IN_ASAR],
    { cwd: tmp, encoding: 'utf-8', stdio: ['ignore', 'pipe', 'ignore'],
      timeout: 180000 })
  }
  extractOk = existsSync(join(tmp, 'worker.cjs'))
} catch {
  extractOk = false
}

if (extractOk) {
  check('提取官方 worker.cjs', true)
  const body = new Uint8Array(readFileSync(indexPath))
  parseResult = await new Promise((res) => {
    const w = new Worker(join(tmp, 'worker.cjs'), {
      workerData: { body, url: 'https://example.invalid/market-index.json' },
    })
    const timer = setTimeout(() => { w.terminate(); res({ ok: false, message: '超时' }) }, 30000)
    w.on('message', (r) => { clearTimeout(timer); w.terminate(); res(r) })
    w.on('error', (e) => { clearTimeout(timer); res({ ok: false, message: e.message }) })
  })
  check('官方 parser 接受索引', parseResult && parseResult.ok === true,
    parseResult && parseResult.ok ? '' : JSON.stringify(parseResult).slice(0, 200))
} else {
  console.log('  ⚠ 本机无 DSH asar，跳过官方 parser 验证（降级为本地校验）')
  console.log('    这不是失败 —— 在装了 DSH 的机器上会做真验证')
}

rmSync(tmp, { recursive: true, force: true })

// ---------- 2b. 本地 schema 校验（官方 parser 不可用时的兜底）----------
const localIdx = JSON.parse(readFileSync(indexPath, 'utf-8'))
if (!extractOk) {
  console.log('\n【2b】本地 schema 校验（兜底）')
  check('format == 1', localIdx.format === 1, String(localIdx.format))
  check('plugins 非空',
    Array.isArray(localIdx.plugins) && localIdx.plugins.length > 0)
}

// ---------- 3. 断言结构正确 ----------
if (parseResult && parseResult.ok) {
  console.log('\n【3】索引结构断言')
  const idx = parseResult.index
  check('format == 1', idx.format === 1, String(idx.format))
  check('有 title', !!idx.title)
  check('plugins 非空', Array.isArray(idx.plugins) && idx.plugins.length > 0,
    `${idx.plugins ? idx.plugins.length : 0} 个`)

  const p = idx.plugins[0]
  check('插件名存在', typeof p.name === 'string' && p.name.length > 0, p.name)
  check('versions 非空', Array.isArray(p.versions) && p.versions.length > 0,
    `${p.versions ? p.versions.length : 0} 个`)

  // 降序检查（schema 硬要求）
  let desc = true
  for (let i = 1; i < p.versions.length; i++) {
    const a = p.versions[i - 1].version.split('.').map(Number)
    const b = p.versions[i].version.split('.').map(Number)
    for (let k = 0; k < 3; k++) {
      if ((a[k] || 0) !== (b[k] || 0)) {
        if ((a[k] || 0) < (b[k] || 0)) desc = false
        break
      }
    }
  }
  check('版本按 SemVer 降序', desc)

  // 每个版本可安装
  let allInstallable = true
  for (const v of p.versions) {
    if (v.source.type === 'git') {
      if (!/^[0-9a-f]{40}$/.test(v.source.commit)) allInstallable = false
      if (!v.source.url.startsWith('https://')) allInstallable = false
    }
    if (!Number.isInteger(v.publishTimestamp)) allInstallable = false
  }
  check('所有版本可安装（commit 合法/时间戳整数）', allInstallable)

  // 语言键必须小写（这个坑是官方 parser 教我的）
  const titleKeys = Object.keys(idx.title || {})
  check('语言键全小写', titleKeys.every((k) => k === k.toLowerCase()),
    titleKeys.join(','))
}

// ---------- 汇总 ----------
const pass = results.filter((x) => x.ok).length
console.log('\n' + '='.repeat(70))
console.log(`结果：${pass}/${results.length} 通过`)
for (const x of results.filter((y) => !y.ok)) {
  console.log(`  ❌ ${x.name}  ${x.detail}`)
}
console.log('='.repeat(70))
console.log(`
★ 为什么用官方 parser 而不是自写校验：
  开发时我用自检脚本，全部通过；但 DSH 官方 parser 一跑就报
  「title.zh-CN must be keyed by a lowercase language id」。
  自检漏了 schema 细节 —— 只有外部裁判才能证伪我的假设。
`)

process.exit(pass === results.length ? 0 : 1)
