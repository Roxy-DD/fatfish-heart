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

/**
 * 假 agent：记录 steer 调用
 *
 * E-422：session 结构必须与真实 DSH 一致，否则测试会骗人。
 *
 * 真实结构（从 asar 提取 dsh-agent-loop/lib/index.js 确认）：
 *   agent.session.surface.nodes   → seq 列表
 *   agent.session.eventAt(seq)    → 取事件
 *
 * 之前假 agent 用的是 session.events，
 * 而生产代码 countToolCalls 也读 session.events ——
 * 两边一致所以测试一直"通过"，但真实环境里
 * agent.session.events 根本不存在，恒返回 0。
 *
 * 这就是"测试和生产用了同一个错误假设"的典型。
 */
function makeAgent({ toolCalls = 0 } = {}) {
  const evs = new Map()
  const nodes = []
  function push(ev) {
    const seq = nodes.length + 1
    nodes.push(seq)
    evs.set(seq, ev)
  }
  for (let i = 0; i < toolCalls; i++) {
    push({ type: 'tool/result', n: i })
  }
  return {
    steerCalls: [],
    steer(msg) {
      this.steerCalls.push(msg)
    },
    // 测试辅助：模拟新增一次工具调用
    addToolCall() {
      push({ type: 'tool/result', n: nodes.length })
    },
    session: {
      surface: { nodes },
      eventAt(seq) { return evs.get(seq) },
    },
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
console.log('\n【2】同一 turn 多次触发（E-371：应每次都 steer）')

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

  // E-371 修：同一 turn 内允许多次 steer
  //
  // 原断言「只 steer 1 次」固化的正是**导致永久停机**的行为。
  // 源码确认：turn() 是 while(true) 循环，steer 后 target="next-step"，
  // **turn 号不变**。所以同一 turn 的多次 turn-stopping 是正常工作流，
  // 不是重入。用 turn 号做防重入会让第二次起永久静默 return。
  check('同一 turn 内可多次 steer（E-371）',
    agent.steerCalls.length === 3,
    `实际 ${agent.steerCalls.length}`)

  // 同时确认没有把 reentry 当成重入挡掉
  const reentry = logs.filter((l) => l.kind === 'lifecycle-reentry-skip')
  check('未误判为重入', reentry.length === 0, `${reentry.length} 条`)
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
    agent.addToolCall()
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

  // 第二次：工具调用数没变
  //
  // E-356 修：旧行为是「第 2 次无进展就永久放行」，
  // 导致一轮纯文本总结后系统彻底停止（真实发生过）。
  // 新语义：无进展也继续 steer，累计 streak，达到 maxNoProgress(默认3) 才放行。
  await ctx.fire('agent/turn-stopping', { agent, turn: 2 })
  const after2 = agent.steerCalls.length

  check('第一次 steer（有进展）', after1 === 1, `${after1}`)
  check('第二次仍 steer（无进展也继续）', after2 === 2, `${after2}`)

  // 连续到 maxNoProgress 才放行
  await ctx.fire('agent/turn-stopping', { agent, turn: 3 })
  const after3 = agent.steerCalls.length
  await ctx.fire('agent/turn-stopping', { agent, turn: 4 })
  const after4 = agent.steerCalls.length
  check('第三次仍 steer（streak=2 < 3）', after3 === 3, `${after3}`)
  check('第四次放行（streak=3 达上限）', after4 === 3, `${after4}`)

  const noProg = logs.filter((l) => l.kind === 'lifecycle-no-progress')
  check('记录了 no-progress 日志', noProg.length > 0, `${noProg.length} 条`)
  const gaveUp = noProg.filter((l) => l.action === 'give-up')
  check('记录了 give-up 动作', gaveUp.length >= 1, `${gaveUp.length} 条`)
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

// ---------- ⑧ countToolCalls 必须真的数出来 ----------
console.log('\n【8】countToolCalls 不能恒返回 0（E-422）')
{
  const sup = createLifecycleSupervisor(
    { lifecycle: { enabled: true } }, () => {}
  )
  const counter = sup.countToolCalls
  if (typeof counter !== 'function') {
    check('暴露了 countToolCalls', false, '未导出')
  } else {
    // 0 次
    const a0 = makeAgent({ toolCalls: 0 })
    check('0 次工具调用 → 0', counter(a0) === 0, String(counter(a0)))

    // 5 次
    const a5 = makeAgent({ toolCalls: 5 })
    check('5 次工具调用 → 5', counter(a5) === 5, String(counter(a5)))

    // 增长
    const a10 = makeAgent({ toolCalls: 10 })
    check('10 次工具调用 → 10', counter(a10) === 10, String(counter(a10)))

    // 空 agent 不抛
    let threw2 = false
    try { counter(null) } catch (e) { threw2 = true }
    check('null agent 不抛异常', !threw2)

    // ★ 关键：如果接口不对，必须返回 0 但也说明"数不出来"
    //   这里反向验证：给一个没有 surface 的 session，应返回 0
    const brokenAgent = { session: { foo: 1 } }
    check('不支持的 session 形状 → 0（不谎报）',
      counter(brokenAgent) === 0, String(counter(brokenAgent)))

    // ★ E-423：真实环境里工具调用记为 tool/result，不是 tool/call
    //   这条断言防止将来又猜错事件类型（E-422 就栽在这里）
    const realShape = {
      session: {
        surface: { nodes: [1, 2, 3] },
        eventAt(seq) {
          const types = {
            1: 'assistant/message',
            2: 'tool/result',
            3: 'user/message',
          }
          return { type: types[seq] }
        },
      },
    }
    check('真实类型分布下能数出 1 次（tool/result）',
      counter(realShape) === 1, String(counter(realShape)))

    // 全是 assistant/message → 0 次工具调用
    const noTools = {
      session: {
        surface: { nodes: [1, 2] },
        eventAt(seq) { return { type: 'assistant/message' } },
      },
    }
    check('全是 assistant/message → 0', counter(noTools) === 0,
      String(counter(noTools)))
  }
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
