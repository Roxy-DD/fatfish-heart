/**
 * E-483：在全新 Node 进程里端到端验证 mem_done
 *
 * 为什么必须这样做（E-479 排查得到的教训）：
 *
 *   mem_done 之前反复报「supervisor 未挂载」，
 *   我改了 globalThis、改了三级回退、加了日志，
 *   但一直无法确认修好没有 —— 因为运行中的 DSH 进程
 *   加载的是旧代码，而自重启只写 patch 文件、不重新执行 apply()。
 *
 *   换句话说：我一直在**旧代码**上验证**新代码**的行为。
 *
 *   本测试启动一个全新的 Node 进程，用最小 mock ctx 真正跑一遍
 *   apply()，然后调用 mem_done 工具，验证：
 *     ① apply() 能跑完不抛异常
 *     ② mem_done 工具被注册了
 *     ③ 工具 execute 能拿到 supervisor（globalThis 回退链有效）
 *     ④ declare 后再触发 turn-stopping 不再 steer
 *
 *   ★ 这正是"重启后能不能用"的提前验证。
 *     如果这里过了，重启后就应该能用。
 */
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)

const results = []
function check(name, cond, detail = '') {
  results.push({ name, ok: !!cond, detail })
  console.log(`  ${cond ? '✅' : '❌'} ${name}${detail ? '  ' + detail : ''}`)
}

console.log('='.repeat(66))
console.log('E-483 全新进程端到端验证 mem_done')
console.log('='.repeat(66))

// ---------- 构造最小可用的 ctx ----------
const registered = new Map()   // 工具名 -> 定义
const services = new Map()     // ctx.set 存放
const listeners = new Map()    // 事件 -> handlers
const logs = []

function makeCtx() {
  return {
    tools: {
      register(def) {
        if (!def || !def.name) throw new Error('工具缺 name')
        registered.set(def.name, def)
      },
    },
    get(name) {
      return services.get(name)
    },
    set(name, val) {
      services.set(name, val)
      return val
    },
    on(evt, fn) {
      if (!listeners.has(evt)) listeners.set(evt, [])
      listeners.get(evt).push(fn)
      return () => {}
    },
    effect(fn) {
      try {
        const d = typeof fn === 'function' ? fn() : undefined
        return typeof d === 'function' ? d : () => {}
      } catch {
        return () => {}
      }
    },
    // 插件 inject 声明了 webServer，但这里不需要真实 HTTP
    webServer: undefined,
    logger: () => {},
  }
}

const ctx = makeCtx()
const config = {
  debugLog: false,       // 故意关闭：验证不依赖日志也能工作
  autoInject: false,
  autoRemember: false,
  kernelDir: '',
  dbPath: '',
  pythonPath: 'python',
}

// ---------- ① 加载并 apply ----------
console.log('\n【1】加载插件并执行 apply()')

let mod
try {
  // 本文件在 proto/ 下，插件在 ../dsh-plugin/
  const { pathToFileURL } = await import('node:url')
  const { dirname, join } = await import('node:path')
  const { fileURLToPath } = await import('node:url')
  const here = dirname(fileURLToPath(import.meta.url))
  const pluginPath = join(here, '..', 'dsh-plugin', 'index.js')
  mod = await import(pathToFileURL(pluginPath).href)
  check('模块可 import', true, pluginPath.replace(/.*肥鱼之心/, '…'))
} catch (e) {
  check('模块可 import', false, String(e && e.message).slice(0, 80))
  console.log('\n无法继续')
  process.exit(1)
}

check('导出 name', typeof mod.name === 'string', mod.name)
check('导出 apply 函数', typeof mod.apply === 'function')

let applyErr = null
try {
  await mod.apply(ctx, config)
} catch (e) {
  applyErr = e
}
check('apply() 不抛异常', applyErr === null,
  applyErr ? String(applyErr.message).slice(0, 100) : '')

// ---------- ② 工具注册 ----------
console.log('\n【2】工具注册情况')

check('注册了工具', registered.size > 0, `${registered.size} 个`)
check('mem_done 已注册', registered.has('mem_done'),
  registered.has('mem_done') ? '' : `已有: ${[...registered.keys()].join(',')}`)

const memDone = registered.get('mem_done')
if (memDone) {
  check('mem_done 有 execute', typeof memDone.execute === 'function')
  check('mem_done 有 parameters', !!memDone.parameters)
  check('mem_done 有 render', !!memDone.output && !!memDone.output.render)
}

// ---------- ③ supervisor 是否可达 ----------
console.log('\n【3】supervisor 可达性')

const sup = globalThis.__FFH_LIFECYCLE_SUP__
  || services.get('cosmosMemLifecycle')
check('supervisor 已挂载到 globalThis',
  !!globalThis.__FFH_LIFECYCLE_SUP__,
  globalThis.__FFH_LIFECYCLE_SUP__ ? '' : '（可能在 ctx 里）')
check('supervisor 可从 ctx 取到',
  !!services.get('cosmosMemLifecycle'))
check('supervisor 有 declareDone',
  !!(sup && typeof sup.declareDone === 'function'))

// ---------- ④ 真正调用 mem_done ----------
console.log('\n【4】调用 mem_done（关键）')

// 构造一个最小 agent（turn-stopping 需要 steer 方法）
const agent = {
  steerCalls: [],
  steer(msg) { this.steerCalls.push(msg) },
  session: {
    surface: { nodes: [1, 2] },
    eventAt(seq) {
      if (seq === 1) return { type: 'user/message' }
      return { type: 'tool/result' }
    },
    countOf(t) { return t === 'tool/result' ? 1 : 1 },
  },
}

let r1 = null
try {
  r1 = await memDone.execute(
    { action: 'status' },
    { agent, signal: undefined })
} catch (e) {
  r1 = { error: 'execute 抛异常: ' + String(e && e.message) }
}
check('execute 不抛异常', !r1 || !r1.error || !String(r1.error).startsWith('execute'),
  JSON.stringify(r1).slice(0, 120))
check('status 拿到 supervisor（不再报未挂载）',
  !(r1 && r1.error === 'lifecycle supervisor 未挂载'),
  r1 && r1.error ? r1.error : 'ok')

// declare
let r2 = null
try {
  r2 = await memDone.execute(
    { action: 'declare', reason: '端到端测试：无剩余可验证工作' },
    { agent })
} catch (e) {
  r2 = { error: String(e && e.message) }
}
check('declare 成功',
  r2 && r2.action === 'declare',
  JSON.stringify(r2).slice(0, 100))

// declare 后 turn-stopping 不应 steer
console.log('\n【5】declare 后 turn-stopping 不再 steer（端到端）')
const before = agent.steerCalls.length
const handlers = listeners.get('agent/turn-stopping') || []
check('注册了 turn-stopping 监听', handlers.length > 0, `${handlers.length} 个`)

for (const fn of handlers) {
  try {
    await fn({ agent, turn: 1 })
  } catch (e) {
    check('turn-stopping 处理器不抛异常', false,
      String(e && e.message).slice(0, 80))
  }
}
check('declare 后没有 steer（回合能结束）',
  agent.steerCalls.length === before,
  `${before} → ${agent.steerCalls.length}`)

// clear 后应恢复
console.log('\n【6】clear 后恢复 steer')
let r3 = null
try {
  r3 = await memDone.execute({ action: 'clear' }, { agent })
} catch (e) {
  r3 = { error: String(e && e.message) }
}
check('clear 成功', r3 && r3.action === 'clear', JSON.stringify(r3).slice(0, 80))

// ---------- ⑦ E-484：工具注册失败时 supervisor 仍能挂载 ----------
//
// 这是 mem_done 一直不可用的**真正根因**（E-479 排查了很久才找到）：
//
//   旧代码把 supervisor 挂载放在**所有 ctx.tools.register() 之后**。
//   只要前面任何一个工具的注册抛异常，apply 就中断，
//   后面的 supervisor 永远不会挂载 ——
//   而工具本身已经注册上了（在抛异常之前），
//   于是表现为「工具有，但拿不到 supervisor」。
//
//   这个 bug 只在真实环境出现，因为 mock 的 register 从不抛异常。
//   ★ 教训：mock 太友善会掩盖真实缺陷。
console.log('\n【7】工具注册抛异常时 supervisor 仍挂载（E-484）')
{
  // 清掉上一轮的状态，模拟全新进程
  delete globalThis.__FFH_LIFECYCLE_SUP__
  delete globalThis.__FFH_LIFECYCLE_ERR__
  delete globalThis.__FFH_APPLY_STAGE__
  delete globalThis.__FFH_APPLY_COMPLETE__

  let n = 0
  const badCtx = {
    tools: { register() { n++; if (n === 1) throw new Error('模拟注册失败') } },
    get() { return undefined },
    set() {},
    on() { return () => {} },
    effect(f) { try { f() } catch { /* ignore */ } return () => {} },
  }

  let threw = null
  try {
    await mod.apply(badCtx, {
      debugLog: false, kernelDir: '', dbPath: '',
      prewarmKernel: false,
    })
  } catch (e) {
    threw = e
  }

  check('注册失败确实会中断 apply', threw !== null,
    threw ? String(threw.message).slice(0, 40) : '没抛出（意外）')
  check('中断时 supervisor 仍然挂载了',
    !!globalThis.__FFH_LIFECYCLE_SUP__,
    globalThis.__FFH_LIFECYCLE_SUP__ ? 'ok' : '未挂载 -> 这就是 mem_done 失效的原因')
  check('apply 缺完成标记（说明确实中途断了）',
    !globalThis.__FFH_APPLY_COMPLETE__,
    String(globalThis.__FFH_APPLY_COMPLETE__))
  check('阶段标记可用于定位', !!globalThis.__FFH_APPLY_STAGE__,
    String(globalThis.__FFH_APPLY_STAGE__))
}

// ---------- 汇总 ----------
const pass = results.filter((x) => x.ok).length
const fail = results.length - pass
console.log(`结果：${pass}/${results.length} 通过`)
if (fail > 0) {
  console.log('\n失败项：')
  results.filter((x) => !x.ok).forEach((x, i) =>
    console.log(`  ${i + 1}. ${x.name}  ${x.detail || ''}`))
}
console.log('='.repeat(66))

console.log(`
★ 这个测试的存在理由：

  E-479 排查花了很久，根本原因是：
    我一直在**旧代码**上验证**新代码**的行为。
    运行中的 DSH 进程加载的是旧模块，
    自重启只写 patch 文件、不重新执行 apply()。

  本测试在全新 Node 进程里真正跑一遍 apply()，
  等于提前做了一次"重启后能不能用"的验证。
  如果这里过了，重启后就应该能用。
`)

process.exit(fail === 0 ? 0 : 1)
