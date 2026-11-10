"""E-366 验证：pre-step 注入消息能绕过 A/B 提前退出路径

从 DSH 源码（已读）得到的机制：

  while (true) {
      decision = await this.preStep(target, {turn, step})     // ← waterfall，可改 messages
      if (decision.kind === "reject") { ...; return false }
      try {
          if (turnEnds && decision.messages.length === 0) break;            // 路径 A
          if (phase.step === 0 && decision.messages.length === 0) {         // 路径 B
              turnEnds = {completed}; return false;
          }
          ... 正常执行 step ...
      }
      if (turnEnds && this.inbox.nextStep.length === 0) {
          await dispatch.serial("agent/turn-stopping", ...)                 // 路径 C
      }
      if (turnEnds && this.inbox.nextStep.length === 0) break;
      target = "next-step";
  }

关键：
  A 和 B 都依赖 `decision.messages.length === 0`。
  decision 来自 pre-step 的返回值（waterfall 最后一环）。

  → 如果在 pre-step 里返回非空 messages，
     A 和 B 都不会触发，流程继续走到 step()，
     然后走到 C，turn-stopping 就会 dispatch。

但要注意副作用：
  · messages 会被 append 到 session（真的当成用户消息）
  · 这会改变对话内容

所以不能滥用。只在「确实需要续跑」时才注入。

本实验先做**离线验证**：
  用假 agent 模拟 turn() 循环，验证 pre-step 注入能让流程走到 C。
"""
import sys
import os
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))

test = r'''
// 复刻 DSH turn() 循环的关键控制流，验证 pre-step 注入的效果
// 只模拟控制流，不模拟真实 LLM 调用

function makeInbox() {
  return {
    nextStep: [],
    claim(target, turn) { return this.nextStep.splice(0, this.nextStep.length) },
  }
}

/**
 * 模拟一次 turn()
 * @param {object} opts
 *   opts.preStepInject  pre-step 返回的额外消息（[] 表示不注入）
 *   opts.stepProduces   正常 step 是否产生 turnEnds
 */
async function runTurn(opts = {}) {
  const inbox = makeInbox()
  // 初始 inbox 里有一条用户消息（模拟真 user message）
  inbox.nextStep.push({ role: 'user', text: '用户说的话' })

  const trace = []
  let turnEnds = null
  let target = 'next-turn'
  let step = 0
  const MAX_ITER = 5

  for (let iter = 0; iter < MAX_ITER; iter++) {
    step += 1

    // --- preStep：waterfall，可改 messages ---
    const claimed = inbox.claim(target, { turn: 1 })
    const injected = (opts.preStepInject || [])
    const decision = {
      kind: 'enter',
      messages: [...claimed, ...injected],
    }
    trace.push(`pre-step(step=${step}): claimed=${claimed.length} +inject=${injected.length} => msgs=${decision.messages.length}`)

    if (decision.kind === 'reject') {
      turnEnds = { kind: 'blocked' }
      trace.push('  -> REJECT, return false')
      return { trace, ended: 'blocked', dispatched: false }
    }

    // 路径 A
    if (turnEnds && decision.messages.length === 0) {
      trace.push('  -> 路径A: turnEnds有值且无消息 → break')
      break
    }
    // 路径 B
    if (step === 1 && decision.messages.length === 0) {
      turnEnds = { kind: 'completed' }
      trace.push('  -> 路径B: step0且无消息 → return false（跳过dispatch）')
      return { trace, ended: 'completed-no-dispatch', dispatched: false }
    }

    // --- 正常 step ---
    const stepEnd = opts.stepProduces || { kind: 'completed' }
    if (turnEnds === null || turnEnds.kind !== 'max-tokens') {
      turnEnds = stepEnd
    }
    trace.push(`  step 执行完, turnEnds=${JSON.stringify(turnEnds)}`)

    // 路径 C
    if (turnEnds && inbox.nextStep.length === 0) {
      trace.push('  -> 路径C: dispatch turn-stopping ✓')
      return { trace, ended: 'turn-stopping', dispatched: true }
    }
    if (turnEnds && inbox.nextStep.length === 0) break
    target = 'next-step'
  }

  trace.push('(循环结束)')
  return { trace, ended: 'loop-exit', dispatched: false }
}

(async () => {
  console.log('='.repeat(70))
  console.log('E-366 场景 1：无 pre-step 注入，模型 step0 无消息')
  console.log('='.repeat(70))
  const r1 = await runTurn({ preStepInject: [] })
  r1.trace.forEach(t => console.log('  ' + t))
  console.log('  结果: ' + r1.ended + '  dispatched=' + r1.dispatched)

  console.log()
  console.log('='.repeat(70))
  console.log('E-366 场景 2：pre-step 注入一条消息')
  console.log('='.repeat(70))
  const r2 = await runTurn({
    preStepInject: [{ role: 'user', text: '### 继续自主迭代' }],
  })
  r2.trace.forEach(t => console.log('  ' + t))
  console.log('  结果: ' + r2.ended + '  dispatched=' + r2.dispatched)

  console.log()
  console.log('='.repeat(70))
  console.log('结论')
  console.log('='.repeat(70))
  const ok1 = !r1.dispatched
  const ok2 = r2.dispatched
  console.log((ok1 ? '✅' : '❌') + ' 场景1：无注入 → 走路径B，跳过 dispatch（复现当前问题）')
  console.log((ok2 ? '✅' : '❌') + ' 场景2：pre-step 注入 → 绕过路径B，走到 dispatch')

  if (ok2) {
    console.log()
    console.log('★ 验证成立：在 agent/pre-step 里注入消息，')
    console.log('  能绕过 A/B 提前退出路径，让 turn-stopping 真的被 dispatch。')
  }

  process.exit(ok1 && ok2 ? 0 : 1)
})()
'''

p = os.path.join(_HERE, "work", "_test_prestep.mjs")
os.makedirs(os.path.dirname(p), exist_ok=True)
with open(p, "w", encoding="utf-8") as f:
    f.write(test)

r = subprocess.run(["node", p], capture_output=True, text=True,
                   timeout=60, encoding="utf-8", errors="replace")
print(r.stdout)
if r.stderr:
    print("STDERR:", r.stderr[:500])
print(f"\nexit={r.returncode}")
sys.exit(r.returncode)
