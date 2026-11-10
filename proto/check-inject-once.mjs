/**
 * check-inject-once.mjs —— 「只注入一次」的回归测试
 *
 * 覆盖 E-348 修的两个 bug：
 *   ① 首轮判断顺序错误：st.injected 先置 true，再用 !st.injected 判断
 *      → full 参数永远 false，"首轮全文"从未生效
 *   ② 并发重复注入：检索耗时 1.5~6.3 秒，期间第二个 assemble
 *      进来时 injected 还是 false → 同轮 2 次注入
 *
 * 这是"静默失败"类 bug：系统表面正常，只是行为悄悄错了。
 */

let pass = 0
let fail = 0
const problems = []

function check(name, cond, detail = '') {
  if (cond) { pass++; console.log(`  ✅ ${name}${detail ? '  ' + detail : ''}`) }
  else { fail++; problems.push(`${name}  ${detail}`); console.log(`  ❌ ${name}${detail ? '  ' + detail : ''}`) }
}

// ---------- 复刻 index.js 的状态与守卫逻辑 ----------
const SESSIONS_KEY = '__COSMOS_MEM_SESSIONS__'

function sessionsMap() {
  let m = globalThis[SESSIONS_KEY]
  if (!(m instanceof Map)) { m = new Map(); globalThis[SESSIONS_KEY] = m }
  return m
}

function sessState(sid) {
  const sessions = sessionsMap()
  if (!sessions.has(sid)) {
    sessions.set(sid, {
      prewarm: null, processed: new Set(),
      injected: false, prewarmPromise: null,
      injecting: false,
    })
  }
  return sessions.get(sid)
}

/**
 * 模拟一次 assemble 的注入决策
 * @returns {{action: string, full: boolean|null}}
 */
function assembleDecision(sid, opts = {}) {
  const st = sessState(sid)

  // 守卫（E-348 修：加了 injecting）
  if (st.injected || st.injecting) {
    return { action: 'skip', full: null }
  }

  // E-348 修：先记首轮，再置位
  const isFirstTurn = !st.injected
  st.injected = true
  st.injecting = true

  const full = isFirstTurn
  if (!opts.holdLock) {
    st.injecting = false
  }
  return { action: 'inject', full }
}

console.log('='.repeat(66))
console.log('inject-once 回归测试')
console.log('='.repeat(66))

// ---------- ① 首轮给全文 ----------
console.log('\n【1】首轮判断（E-348 bug①）')
{
  sessionsMap().clear()
  const r1 = assembleDecision('s1')
  check('首轮执行注入', r1.action === 'inject', r1.action)
  check('首轮 full=true', r1.full === true, String(r1.full))

  const r2 = assembleDecision('s1')
  check('第二轮跳过注入', r2.action === 'skip', r2.action)
}

// ---------- ② 旧实现会错（对照）----------
console.log('\n【2】对照：旧实现的首轮判断永远 false')
{
  // 旧代码：st.injected = true; ... 用 !st.injected
  const st = { injected: false }
  st.injected = true
  const oldFull = !st.injected
  check('旧实现 full 恒为 false（bug 复现）', oldFull === false,
    String(oldFull))
}

// ---------- ③ 并发防重 ----------
console.log('\n【3】并发防重（E-348 bug②）')
{
  sessionsMap().clear()
  // 第一次注入，持锁不放（模拟检索耗时 6 秒）
  const r1 = assembleDecision('s2', { holdLock: true })
  check('首次注入持锁', r1.action === 'inject', r1.action)
  check('锁已设置', sessState('s2').injecting === true)

  // 并发第二个 assemble
  const r2 = assembleDecision('s2')
  check('并发第二次被挡', r2.action === 'skip', r2.action)

  // 第三个也被挡
  const r3 = assembleDecision('s2')
  check('并发第三次被挡', r3.action === 'skip', r3.action)

  // 释放锁后仍跳过（因为 injected=true）
  sessState('s2').injecting = false
  const r4 = assembleDecision('s2')
  check('释放锁后仍跳过', r4.action === 'skip', r4.action)
}

// ---------- ④ 异常时锁要能释放 ----------
console.log('\n【4】异常释放锁')
{
  sessionsMap().clear()
  const st = sessState('s3')
  st.injected = true
  st.injecting = true
  // 模拟 catch 分支释放
  st.injecting = false
  // 重新会话（模拟 disposed 后重建）
  sessionsMap().delete('s3')
  const r = assembleDecision('s3')
  check('disposed 后可重新注入', r.action === 'inject', r.action)
  check('新生命周期 full=true', r.full === true, String(r.full))
}

// ---------- ⑤ 不同 sid 互不干扰 ----------
console.log('\n【5】不同 sid 隔离')
{
  sessionsMap().clear()
  const a = assembleDecision('sidA')
  const b = assembleDecision('sidB')
  check('sidA 注入', a.action === 'inject')
  check('sidB 也注入（互不影响）', b.action === 'inject')
  check('sidA full=true', a.full === true)
  check('sidB full=true', b.full === true)
}

// ---------- ⑥ reload 保留状态 ----------
console.log('\n【6】reload 保留 injected（E-307）')
{
  sessionsMap().clear()
  assembleDecision('s4')
  // 模拟 reload：模块级变量重置，但 globalThis 保留
  const st2 = sessState('s4')
  check('reload 后 injected 仍为 true', st2.injected === true)
  const r = assembleDecision('s4')
  check('reload 后不重复注入', r.action === 'skip', r.action)
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
