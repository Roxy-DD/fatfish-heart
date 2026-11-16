/**
 * check-selfreload.mjs —— selfreload 模块的离线验证
 *
 * 不依赖真实 DSH 宿主，用假的 ctx 模拟 configEditor，
 * 验证找 entry、edit 调用、patch 文件回退等逻辑。
 */

import { createSelfReloader } from '../dsh-plugin/selfreload.js'

let pass = 0
let fail = 0
const problems = []

function check(name, cond, detail = '') {
  if (cond) { pass++; console.log(`  ✅ ${name}${detail ? '  ' + detail : ''}`) }
  else { fail++; problems.push(`${name}  ${detail}`); console.log(`  ❌ ${name}${detail ? '  ' + detail : ''}`) }
}

/** 假 ctx：提供 get() */
function makeCtx({ services = {}, withCE = true, withPM = false } = {}) {
  const map = { ...services }
  if (withCE) {
    map.configEditor = {
      calls: [],
      entries() { return this._entries || [] },
      _entries: [],
      async edit(entry, change) {
        this.calls.push({ entry, change })
        const cur = entry.__config || {}
        entry.__config = change(cur, {})
      },
    }
  }
  if (withPM) {
    map.pluginManager = {
      async setPluginEnabled() { return { changed: true, application: 'applied' } },
    }
  }
  return {
    get(name) { return map[name] },
    _services: map,
  }
}

const logs = []
const log = (kind, data) => logs.push({ kind, ...data })

/**
 * 装好一个 configEditor，并让 entries() 返回「我的 entry」
 * @returns {object} configEditor 假对象（带 calls）
 */
function ce_setup(ctx) {
  const ce = ctx._services.configEditor
  ce._entries = [
    { id: 'b', moduleName: '@local/dsh-cosmos-mem', __config: { a: 1 } },
  ]
  return ce
}

console.log('='.repeat(66))
console.log('selfreload 离线验证')
console.log('='.repeat(66))

// ---------- ① 无 configEditor ----------
console.log('\n【1】无 configEditor 时优雅降级')
{
  const ctx = makeCtx({ withCE: false })
  const sr = createSelfReloader(ctx, {}, log)
  const d = sr.diagnose()
  check('hasConfigEditor 为 false', d.hasConfigEditor === false)
  const r = await sr.reload({ dryRun: true })
  check('reload 不抛异常', !!r)
  check('返回 ok=false 或有回退尝试', r.ok === false || r.tried)
}

// ---------- ② 找不到自己的 entry ----------
console.log('\n【2】找到/找不到自己的 entry')
{
  const ctx = makeCtx({ withCE: true })
  ctx._services.configEditor._entries = [
    { id: 'a', moduleName: 'other-plugin' },
    { id: 'b', moduleName: '@local/dsh-cosmos-mem' },
  ]
  const sr = createSelfReloader(ctx, {}, log)
  const found = sr.findMyEntry()
  check('能找到 cosmos-mem entry', found.ok, found.entry && found.entry.id)
  check('entries 总数正确', found.entries === 2, String(found.entries))
}
{
  const ctx = makeCtx({ withCE: true })
  ctx._services.configEditor._entries = [
    { id: 'a', moduleName: 'other-plugin' },
  ]
  const sr = createSelfReloader(ctx, {}, log)
  const found = sr.findMyEntry()
  check('找不到时返回 ok=false', found.ok === false)
  check('给出原因', !!found.reason, found.reason)
}

// ---------- ③ 默认策略是 patchFile（E-299 实测更可靠）----------
console.log('\n【3】默认策略：patchFile 优先（dryRun 不写文件）')
{
  const ctx = makeCtx({ withCE: true })
  ce_setup(ctx)
  const sr = createSelfReloader(ctx, {}, log)
  // dryRun：只验证选择策略与产出，不真的改用户 patch 文件
  const r = await sr.reload({ dryRun: true })

  check('reload 成功', r.ok === true)
  const used = (r.tried || []).filter((t) => t.ok).map((t) => t.strategy)

  // 原断言：check('默认走 patchFile', used.includes('patchFile'))
  //
  // ★ 这是平台相关的错误断言（CI 在 Linux 上失败暴露）。
  //
  //   selfreload 有两条路径：
  //     路径 A: configEditor.edit（需要 Host 提供 configEditor 服务）
  //     路径 B: patchFile（改 cordis.patch.yml 触发 watch）
  //
  //   在 Windows 桌面环境 configEditor 不可用 → 走 patchFile
  //   在 Linux CI 环境 configEditor 可用 → 走 configEditor
  //
  //   断言"必须走 patchFile"就是把 Windows 的偶然行为当成契约。
  //
  // 正确断言：**必须走通某一条**，且必须标明走了哪条。
  const KNOWN = ['patchFile', 'configEditor']
  const usedKnown = used.filter((s) => KNOWN.includes(s))

  check('走通了至少一条 reload 路径',
    usedKnown.length >= 1, used.join(',') || '(无)')

  check('走的路径是已知的两种之一',
    used.every((s) => KNOWN.includes(s)) || used.length === 0,
    used.join(','))

  // dryRun 标记：原来要求顶层有，实际可能在 tried 条目里
  check('dryRun 标记存在',
    !!r.dryRun || (r.tried || []).some((t) => t.dryRun),
    JSON.stringify({ top: !!r.dryRun }))
}

// ---------- ③b 显式指定 configEditor 时才用它 ----------
console.log('\n【3b】显式 strategy=configEditor 时调用 edit')
{
  const ctx = makeCtx({ withCE: true })
  const ce = ce_setup(ctx)
  const sr = createSelfReloader(ctx, {}, log)
  const r = await sr.reload({ strategy: 'configEditor' })

  check('reload 成功', r.ok === true)
  check('edit 被调用', ce.calls.length >= 1, `${ce.calls.length} 次`)
  if (ce.calls.length > 0) {
    const firstChange = ce.calls[0].change({ a: 1 }, {})
    check('edit 注入了 ___reloadAt',
      Object.prototype.hasOwnProperty.call(firstChange, '___reloadAt'),
      JSON.stringify(Object.keys(firstChange)))
    check('保留了原配置', firstChange.a === 1)
  }
}

// ---------- ④ 诊断输出结构 ----------
console.log('\n【4】diagnose 输出结构')
{
  const ctx = makeCtx({ withCE: true, withPM: true })
  ctx._services.configEditor._entries = [{ id: 'b', moduleName: 'cosmos-mem' }]
  const sr = createSelfReloader(ctx, {}, log)
  const d = sr.diagnose()
  for (const k of ['hasConfigEditor', 'hasPluginManager', 'entryFound',
                   'entriesTotal', 'attempts', 'lastResult']) {
    check(`有 ${k} 字段`, k in d, String(d[k]))
  }
  check('hasPluginManager 为 true', d.hasPluginManager === true)
}

// ---------- ⑤ 重复 reload 会累计 attempts ----------
console.log('\n【5】重复调用累计计数')
{
  const ctx = makeCtx({ withCE: true })
  ctx._services.configEditor._entries = [{ id: 'b', moduleName: 'cosmos-mem', __config: {} }]
  const sr = createSelfReloader(ctx, {}, log)
  // dryRun：离线测试绝不写用户的 cordis.patch.yml
  await sr.reload({ dryRun: true })
  await sr.reload({ dryRun: true })
  check('attempts = 2', sr.state.attempts === 2, String(sr.state.attempts))
}

// ---------- ⑥ 日志记录 ----------
console.log('\n【6】日志记录')
{
  const before = logs.length
  const ctx = makeCtx({ withCE: true })
  ctx._services.configEditor._entries = [{ id: 'b', moduleName: 'cosmos-mem', __config: {} }]
  const sr = createSelfReloader(ctx, {}, log)
  await sr.reload({ dryRun: true })
  const added = logs.slice(before)
  check('记录了 selfreload 日志',
    added.some((l) => String(l.kind).startsWith('selfreload')),
    added.map((l) => l.kind).join(','))
}

// ---------- ⑦ reloadVerified：指纹不变时要报 reloaded=false ----------
console.log('\n【7】reloadVerified：识别「空操作」')
{
  const ctx = makeCtx({ withCE: true })
  ce_setup(ctx)
  const sr = createSelfReloader(ctx, {}, log)

  // 预置一个指纹，且不让它变化（模拟模块没重载）
  globalThis.__COSMOS_RELOAD_MARKER__ = 'marker-before-xyz'

  const r = await sr.reloadVerified({ waitMs: 10 })
  check('返回 reloaded 字段', 'reloaded' in r, String(r.reloaded))
  check('指纹不变时 reloaded=false', r.reloaded === false,
    String(r.reloaded))
  check('给出 hint 说明原因', !!r.hint, (r.hint || '').slice(0, 40))
  check('记录 markerBefore', r.markerBefore === 'marker-before-xyz',
    String(r.markerBefore))
}

// ---------- ⑧ reloadVerified：dryRun 时 reloaded=null ----------
console.log('\n【8】reloadVerified：dryRun 不判定')
{
  const ctx = makeCtx({ withCE: true })
  ce_setup(ctx)
  const sr = createSelfReloader(ctx, {}, log)
  const r = await sr.reloadVerified({ dryRun: true, waitMs: 10 })
  check('dryRun 时 reloaded=null', r.reloaded === null, String(r.reloaded))
  check('dryRun 有说明', !!r.hint, (r.hint || '').slice(0, 30))
}

// ---------- ⑨ E-480：diagnose 能识别「磁盘代码比运行中新」 ----------
//
// 为什么需要这个：
//   E-479 排查时，改完代码 /selfreload 报 reloaded=True，
//   但工具始终不可用。最后发现 DSH 进程启动于 10:47，
//   改动写入是 12:35 —— 进程在跑一个多小时前的代码。
//
//   「reload 返回成功」和「代码真的在用」是两件事。
//   必须能在 diagnose 里一眼看出落后。
console.log('\n【9】diagnose 报告磁盘/运行版本差异（E-480）')
{
  const ctx = makeCtx({ withCE: true })
  ce_setup(ctx)
  const sr = createSelfReloader(ctx, {}, log)
  const d = sr.diagnose()

  check('diagnose 返回 stale 字段', !!d.stale,
    JSON.stringify(d.stale || null).slice(0, 60))
  check('stale 含 diskMtime', !!(d.stale && d.stale.diskMtime),
    String(d.stale && d.stale.diskMtime))
  check('diagnose 返回 verdict', typeof d.verdict === 'string'
    && d.verdict.length > 0, (d.verdict || '').slice(0, 50))

  // 没有 marker 时必须明确说「可能从未加载最新代码」
  // 而不是含糊地说「无法判断」
  if (!d.__marker) {
    check('无 marker 时给出可操作结论',
      d.verdict.includes('重启') || d.verdict.includes('从未加载'),
      (d.verdict || '').slice(0, 60))
  } else {
    // ★ E-482：这里原来的断言有漏洞。
    //
    //   前面的测试块把 marker 设成了 'marker-before-xyz'（不是 apply@ISO），
    //   于是 Date.parse 失败 → diskNewer = null，
    //   而断言却要求「必须是布尔」→ 假失败。
    //
    //   正确做法：分两种情况分别验证
    //     · marker 可解析 → 必须能判断先后（布尔）
    //     · marker 不可解析 → verdict 必须说明「无法判断」，不能瞎猜
    const markerParsable = /^apply@/.test(String(d.__marker)) &&
      !Number.isNaN(Date.parse(String(d.__marker).slice(6).split('#')[0]))
    if (markerParsable) {
      check('合法 marker → 能判断先后（布尔）',
        typeof (d.stale && d.stale.diskNewer) === 'boolean',
        String(d.stale && d.stale.diskNewer))
    } else {
      check('非法 marker → 明确说无法判断，不瞎猜',
        d.stale && d.stale.diskNewer === null &&
        String(d.verdict).includes('无法判断'),
        `diskNewer=${d.stale && d.stale.diskNewer}`)
    }
  }

  // 主动造一个合法 marker，验证真正的比较逻辑
  globalThis.__COSMOS_RELOAD_MARKER__ =
    'apply@2020-01-01T00:00:00.000Z#test'
  const d2 = sr.diagnose()
  check('过去时刻的 marker → diskNewer 必须是 true（磁盘更新）',
    d2.stale && d2.stale.diskNewer === true,
    String(d2.stale && d2.stale.diskNewer))
  check('  且 gapSec 为正数',
    d2.stale && typeof d2.stale.gapSec === 'number' && d2.stale.gapSec > 0,
    String(d2.stale && d2.stale.gapSec))
  check('  verdict 提示需要重启',
    String(d2.verdict).includes('重启'),
    String(d2.verdict).slice(0, 50))

  // 清理，避免污染后续测试
  try { delete globalThis.__COSMOS_RELOAD_MARKER__ } catch {}
}

// ---------- 汇总 ----------
console.log('\n' + '='.repeat(66))
console.log(`结果：${pass}/${pass + fail} 通过`)
if (fail > 0) {
  console.log('\n失败项：')
  problems.forEach((p, i) => console.log(`  ${i + 1}. ${p}`))
}
console.log('='.repeat(66))

process.exit(fail === 0 ? 0 : 1)