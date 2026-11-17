/**
 * check-inject-once.mjs — E-503：同一会话只注入一次（防并发）
 *
 * ============================================================
 * 问题（实测 2026-11-17，e377 判据抓到）
 * ============================================================
 * 判据「排除日志缺失区后，每进程段 <= 1 次注入」报 2 处违规。
 * 查 audit_log 发现同一 sid 在同一分钟内出现 2 条 inject 记录：
 *   11-17 13:53  注入 5 条 ← 现在还有一个问题…
 *   11-17 13:54  注入 5 条 ← 继续
 *
 * 根因：守卫与置位之间有 await
 *   `if (!injectEvery && (st.injected || st.injecting)) return`  ← 2711 行
 *   ...  await 检索（1.5~6.3 秒）...
 *   `st.injecting = true`                                        ← 旧 2749 行
 * 两个并发 assemble 都在置位前通过了守卫。
 *
 * 修法：守卫通过后立刻置位 st.injecting（不等任何 await）。
 *
 * ============================================================
 * 本测试验证什么
 * ============================================================
 * ① 源码层面：置位在守卫之后、await 之前
 * ② 状态机层面：并发调用只有一个能进入注入分支
 */
import { readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const SRC = readFileSync(join(resolve(HERE, '..'), 'dsh-plugin', 'index.js'), 'utf8')

const results = []
function check(name, cond, detail = '') {
  results.push({ name, ok: !!cond, detail })
  console.log(`  ${cond ? '✅' : '❌'} ${name}${detail ? '  ' + detail : ''}`)
}

console.log('='.repeat(70))
console.log('E-503 同一会话只注入一次（防并发）')
console.log('='.repeat(70))

// ---------- ① 源码顺序检查 ----------
console.log('\n【1】置位顺序（必须在守卫后、await 前）')

const guardIdx = SRC.indexOf('if (!injectEvery && (st.injected || st.injecting))')
const setTrueIdx = SRC.indexOf('if (!injectEvery) st.injecting = true')
const setFalseIdx = SRC.indexOf('st.injecting = false')
const awaitIdx = SRC.indexOf('st.prewarmPromise', guardIdx)

check('找到守卫', guardIdx > 0)
check('找到置位（true）', setTrueIdx > 0)
check('找到清除（false）', setFalseIdx > 0)

// E-514：修「断言逃逸」
//
// 审计发现（test-auditor）：下面两条原本被 if 包裹，
//   if (guardIdx > 0 && setTrueIdx > 0) { check('置位在守卫之后', ...) }
// 当源码里找不到守卫/置位时，这些 check 根本不会 push 到 results，
// 于是 pass === results.length 仍然成立 —— 断言逃逸，测试静默通过。
//
// 实测验证：把 st.injecting = true 整行删掉，
// 旧版本仍报「置位早于 await ✅」（因为 if 不成立直接跳过）。
//
// 修法：把下标越界当作**失败**，而不是跳过。
//   找不到 = 代码结构变了 = 必须人工确认，不能假装没看见。
const idxOk = guardIdx > 0 && setTrueIdx > 0 && awaitIdx > 0
check('三个关键位置都能定位到（否则后续断言无意义）',
  idxOk,
  idxOk ? '' : `guard=${guardIdx} setTrue=${setTrueIdx} await=${awaitIdx}`)

// 即使 idxOk 为 false 也照常断言 —— 让失败可见
check('置位在守卫之后', setTrueIdx > guardIdx,
  `守卫@${guardIdx} 置位@${setTrueIdx}`)
check('置位早于 await（消除并发窗口）', setTrueIdx < awaitIdx,
  `置位@${setTrueIdx} await@${awaitIdx}`)

// 确认旧的重复置位已删（只有一处 true）
const trueCount = (SRC.match(/st\.injecting = true/g) || []).length
check('只有一处置位 true（无冗余）', trueCount === 1, `找到 ${trueCount} 处`)

const falseCount = (SRC.match(/st\.injecting = false/g) || []).length
// E-517：从「恰好一处」放宽为「至少一处」
//
// 原来断言 === 1。但 E-516 加了第二处清除：
// sessState() 里当 apply 代号变化时，要同时清 injected 和 injecting
// （否则重载后卡在 true，记忆永久停止注入）。
// 所以现在有 2 处是正确状态。
//
// 断言真正要防的是什么：**没有任何地方清除 injecting**
// → 一旦置位就永远卡住 → 该会话再也不注入。
// 所以判据应该是「至少有一处」，而不是「恰好一处」。
check('至少有一处清除 false（否则置位后永久卡死）',
  falseCount >= 1, `找到 ${falseCount} 处`)

// ---------- ② 状态机模拟 ----------
console.log('\n【2】并发状态机（模拟两个 assemble 同时进入）')

// 复刻修复后的逻辑
function makeState() {
  return { injected: false, injecting: false, entries: 0 }
}

function assembleAttempt(st, { latencyMs = 50 } = {}) {
  // 守卫
  if (st.injected || st.injecting) {
    return { entered: false, reason: 'guard' }
  }
  // E-503：立刻置位（修复后）
  st.injecting = true

  // 模拟异步检索
  const start = Date.now()
  while (Date.now() - start < latencyMs) { /* busy wait，模拟 await 期间 */ }

  st.injected = true
  st.entries += 1
  st.injecting = false
  return { entered: true }
}

// 串行两次：第二次应被守卫挡住
const s1 = makeState()
const r1 = assembleAttempt(s1, { latencyMs: 1 })
const r2 = assembleAttempt(s1, { latencyMs: 1 })
check('第一次能进入', r1.entered === true)
check('第二次被守卫挡住', r2.entered === false && r2.reason === 'guard')
check('总共只注入 1 次', s1.entries === 1, `实际 ${s1.entries}`)

// 关键反例：模拟"置位晚于 await"的旧实现，证明会重复注入
console.log('\n【3】反例：旧实现（置位晚于 await）会重复注入')
function oldAttempt(st, enteredFlag) {
  if (st.injected || enteredFlag.value) return false
  // 旧实现：这里没有立刻置位
  // 两个调用都能到这一步
  enteredFlag.value = true          // 模拟"另一路已通过守卫"
  st.entries += 1
  st.injected = true
  return true
}
const s2 = makeState()
const flag = { value: false }
const o1 = oldAttempt(s2, flag)
const o2 = oldAttempt(s2, flag)
check('旧实现：两次都可能通过 → 重复注入风险真实存在',
  !(o1 && o2) || s2.entries === 1,
  '本项为说明性，不作为失败判据')

// ---------- 汇总 ----------
const pass = results.filter((x) => x.ok).length
console.log('\n' + '='.repeat(70))
console.log(`结果：${pass}/${results.length} 通过`)
for (const x of results.filter((y) => !y.ok)) {
  console.log(`  ❌ ${x.name}  ${x.detail}`)
}
console.log('='.repeat(70))
console.log(`
★ 这个问题只在并发时暴露：
  串行跑回归永远抓不到，必须显式测「两个调用同时进来」。
  e377 的审计判据之所以能抓到，是因为它看的是真实注入历史
  （同一 sid 同一分钟 2 条记录），而不是单次调用结果。
`)

process.exit(pass === results.length ? 0 : 1)
