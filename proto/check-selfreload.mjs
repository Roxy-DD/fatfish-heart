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
  const r = await sr.reload()
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

// ---------- ③ reloadViaConfigEditor 调用 edit ----------
console.log('\n【3】reload 会调用 configEditor.edit')
{
  const ctx = makeCtx({ withCE: true })
  const ce = ctx._services.configEditor
  ce._entries = [{ id: 'b', moduleName: '@local/dsh-cosmos-mem', __config: { a: 1 } }]
  const sr = createSelfReloader(ctx, {}, log)
  const r = await sr.reload()

  check('reload 成功', r.ok === true, JSON.stringify(r).slice(0, 100))
  check('edit 被调用', ce.calls.length >= 1, `${ce.calls.length} 次`)
  // 第一次 edit 加 ___reloadAt
  const firstChange = ce.calls[0].change({ a: 1 }, {})
  check('edit 注入了 ___reloadAt',
    Object.prototype.hasOwnProperty.call(firstChange, '___reloadAt'),
    JSON.stringify(Object.keys(firstChange)))
  check('保留了原配置', firstChange.a === 1)
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
  await sr.reload()
  await sr.reload()
  check('attempts = 2', sr.state.attempts === 2, String(sr.state.attempts))
}

// ---------- ⑥ 日志记录 ----------
console.log('\n【6】日志记录')
{
  const before = logs.length
  const ctx = makeCtx({ withCE: true })
  ctx._services.configEditor._entries = [{ id: 'b', moduleName: 'cosmos-mem', __config: {} }]
  const sr = createSelfReloader(ctx, {}, log)
  await sr.reload()
  const added = logs.slice(before)
  check('记录了 selfreload 日志',
    added.some((l) => String(l.kind).startsWith('selfreload')),
    added.map((l) => l.kind).join(','))
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
