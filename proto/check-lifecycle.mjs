/**
 * check-lifecycle.mjs —— lifecycle 模块的离线验证
 *
 * 不依赖真实 DSH 宿主，用假的 agent/ctx 模拟 turn-stopping，
 * 验证拦截逻辑与安全边界是否符合预期。
 *
 * 这是常驻检查，防止 lifecycle 逻辑出问题。
 */

import { createLifecycleSupervisor } from '../dsh-plugin/lifecycle.js'

let pass = 0
let fail = 0
const problems = []

function check(name, cond, detail = '') {
  if (cond) {
    pass++
    console.log(`  ✅ ${name}${detail ? '  ' + detail : ''}`)
  } else {
    fail++
    problems.push(`${name}  ${detail}`)
    console.log(`  ❌ ${name}${detail ? '  ' + detail : ''}`)
  }
}

/** 假 agent：记录 steer 调用 */
function makeAgent({ toolCalls = 0 } = {}) {
  const events = []
  for (let i = 0; i < toolCalls; i++) {
    events.push({ type: 'tool/call', n: i })
  }
  return {
    steerCalls: [],
    steer(msg) {
      this.steerCalls.push(msg)
    },
    session: { events },
  }
}

/** 假 ctx：收集事件监听器 */
function makeCtx() {
  const handlers = {}
  return {
    on(evt, fn) {
      handlers[evt] = handlers[evt] || []
      handlers[evt].push(fn)
    },
    async fire(evt, payload) {
      for (const fn of handlers[evt] || []) {
        await fn(payload)
      }
    },
    handlers,
  }
}

const logs = []
const log = (kind, data) => logs.push({ kind, ...data })

console.log('='.repeat(66))
console.log('lifecycle 模块离线验证')
console.log('='.repeat(66))

// ---------- ① 基本拦截 ----------
console.log('\n【1】基本拦截：有进展时应 steer')

{
  const sup = createLifecycleSupervisor(
    { lifecycle: { enabled: true, maxConsecutive: 20, requireProgress: true } },
    log
  )
  const ctx = makeCtx()
  sup.attach(ctx)

  const agent = makeAgent({ toolCalls: 5 })
  await ctx.fire('agent/turn-stopping', { agent, turn: 1 })

  check('steer 被调用 1 次', agent.steerCalls.length === 1,
    `${agent.steerCalls.length}`)

  const msg = agent.steerCalls[0]
  check('消息 role 是 user', msg && msg.role === 'user', msg && msg.role)
  check('消息有 id', !!(msg && msg.id), msg && String(msg.id).slice(0, 12))
  check('内容是非空文本',
    msg && msg.content && msg.content[0] && msg.content[0].type === 'text' &&
    msg.content[0].text.length > 20,
    msg && msg.content && msg.content[0] && msg.content[0].type)
}

// ---------- ② 防重入：同一 turn 只 steer 一次 ----------
console.log('\n【2】防重入：同一 turn 重复触发只 steer 一次')

{
  const sup = createLifecycleSupervisor(
    { lifecycle: { enabled: true, maxConsecutive: 20, requireProgress: false } },
    log
  )
  const ctx = makeCtx()
  sup.attach(ctx)

  const agent = makeAgent({ toolCalls: 3 })
  await ctx.fire('agent/turn-stopping', { agent, turn: 5 })
  await ctx.fire('agent/turn-stopping', { agent, turn: 5 })
  await ctx.fire('agent/turn-stopping', { agent, turn: 5 })

  check('只 steer 1 次', agent.steerCalls.length === 1,
    `实际 ${agent.steerCalls.length}`)
}

// ---------- ③ 上限保护 ----------
console.log('\n【3】上限保护：达到 maxConsecutive 后放行')

{
  const MAX = 3
  const sup = createLifecycleSupervisor(
    { lifecycle: { enabled: true, maxConsecutive: MAX, requireProgress: false } },
    log
  )
  const ctx = makeCtx()
  sup.attach(ctx)

  const agent = makeAgent({ toolCalls: 10 })
  // 每次用不同的 turn，且工具调用数持续增长
  for (let t = 1; t <= 10; t++) {
    agent.session.events.push({ type: 'tool/call', n: t })
    await ctx.fire('agent/turn-stopping', { agent, turn: t })
  }

  check(`最多 steer ${MAX} 次`, agent.steerCalls.length === MAX,
    `实际 ${agent.steerCalls.length}`)

  const maxLog = logs.filter((l) => l.kind === 'lifecycle-max-reached')
  check('记录了 max-reached 日志', maxLog.length > 0, `${maxLog.length} 条`)
}

// ---------- ④ 进展检测 ----------
console.log('\n【4】进展检测：没有新工具调用时不 steer')

{
  const sup = createLifecycleSupervisor(
    { lifecycle: { enabled: true, maxConsecutive: 20, requireProgress: true } },
    log
  )
  const ctx = makeCtx()
  sup.attach(ctx)

  const agent = makeAgent({ toolCalls: 5 })
  // 第一次：从 -1 → 5，有进展，steer
  await ctx.fire('agent/turn-stopping', { agent, turn: 1 })
  const after1 = agent.steerCalls.length

  // 第二次：工具调用数没变，应被拦
  await ctx.fire('agent/turn-stopping', { agent, turn: 2 })
  const after2 = agent.steerCalls.length

  check('第一次 steer（有进展）', after1 === 1, `${after1}`)
  check('第二次不 steer（无进展）', after2 === 1, `${after2}`)

  const noProg = logs.filter((l) => l.kind === 'lifecycle-no-progress')
  check('记录了 no-progress 日志', noProg.length > 0, `${noProg.length} 条`)
}

// ---------- ⑤ 用户消息清零 ----------
console.log('\n【5】用户发消息时清零计数')

{
  const sup = createLifecycleSupervisor(
    { lifecycle: { enabled: true, maxConsecutive: 3, requireProgress: false } },
    log
  )
  const ctx = makeCtx()
  sup.attach(ctx)

  const agent = makeAgent({ toolCalls: 5 })
  for (let t = 1; t <= 3; t++) {
    await ctx.fire('agent/turn-stopping', { agent, turn: t })
  }
  const beforeReset = agent.steerCalls.length

  // 用户发消息 → 清零
  await ctx.fire('agent/user-message', { agent })

  // 再次触发应该能 steer
  await ctx.fire('agent/turn-stopping', { agent, turn: 99 })
  const afterReset = agent.steerCalls.length

  check('清零前已到上限', beforeReset === 3, `${beforeReset}`)
  check('清零后可再次 steer', afterReset === 4, `${afterReset}`)
}

// ---------- ⑥ 关闭开关 ----------
console.log('\n【6】配置关闭时不注册监听')

{
  const sup = createLifecycleSupervisor(
    { lifecycle: { enabled: false } },
    log
  )
  const ctx = makeCtx()
  sup.attach(ctx)

  const hasListener = !!(ctx.handlers['agent/turn-stopping'] || []).length
  check('未注册 turn-stopping 监听', !hasListener)

  const disabled = logs.filter((l) => l.kind === 'lifecycle-disabled')
  check('记录了 disabled 日志', disabled.length > 0)
}

// ---------- ⑦ agent 无 steer 方法时安全跳过 ----------
console.log('\n【7】agent 缺少 steer 时安全跳过')

{
  const sup = createLifecycleSupervisor(
    { lifecycle: { enabled: true, requireProgress: false } },
    log
  )
  const ctx = makeCtx()
  sup.attach(ctx)

  let threw = false
  try {
    await ctx.fire('agent/turn-stopping', { agent: {}, turn: 1 })
  } catch (e) {
    threw = true
  }
  check('不抛异常', !threw)
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
