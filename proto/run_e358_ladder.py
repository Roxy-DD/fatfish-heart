"""E-358 单元实验：新逻辑是否真的输出 continue-anyway

E-357 观察到：
  · 修复后 15:19:36 有一次 steer（纯文本回合被追）
  · 但 no-progress 的 6 条全是修复前的，没有 action 字段

原因：修复后还没发生「连续第二次无进展」，
所以 continue-anyway 分支还没被走到。

不等待真实触发，直接做单元实验验证新分支。
"""
import sys
import os
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))

test = r'''
// 复刻 lifecycle.js 的新逻辑，验证阶梯行为
const DEFAULT_CONFIG = {
  enabled: true, maxConsecutive: 20,
  requireProgress: true, maxNoProgress: 3,
}

const logs = []
const log = (kind, data) => logs.push({ kind, ...data })

function makeState() {
  return {
    consecutive: 0, lastToolCalls: -1,
    lastSteerTurn: -1, totalSteers: 0,
    noProgressStreak: 0,
  }
}

/**
 * 模拟一次 turn-stopping
 * @returns 'steer' | 'no-progress-drop'
 */
function turnStopping(s, conf, tc, turn) {
  if (s.lastSteerTurn === turn) return 'reentry'
  if (s.consecutive >= conf.maxConsecutive) return 'max-reached'

  const hasProgress = !(s.lastToolCalls >= 0 && tc <= s.lastToolCalls)

  if (conf.requireProgress && !hasProgress) {
    s.noProgressStreak += 1
    const limit = conf.maxNoProgress
    if (s.noProgressStreak >= limit) {
      log('lifecycle-no-progress',
          { turn, toolCalls: tc, last: s.lastToolCalls,
            streak: s.noProgressStreak, limit, action: 'give-up' })
      return 'no-progress-give-up'
    }
    log('lifecycle-no-progress',
        { turn, toolCalls: tc, last: s.lastToolCalls,
          streak: s.noProgressStreak, limit, action: 'continue-anyway' })
  } else {
    s.noProgressStreak = 0
  }

  s.consecutive += 1
  s.totalSteers += 1
  s.lastToolCalls = tc
  s.lastSteerTurn = turn
  log('lifecycle-steer', { turn, consecutive: s.consecutive, toolCalls: tc })
  return 'steer'
}

const s = makeState()
const results = []

// 场景：连续 4 轮纯文本（tc 始终 0）
for (let turn = 1; turn <= 4; turn++) {
  const r = turnStopping(s, DEFAULT_CONFIG, 0, turn)
  results.push({ turn, result: r, streak: s.noProgressStreak })
  console.log(`turn ${turn}: ${r}  (streak=${s.noProgressStreak})`)
}

console.log('\n--- 日志 ---')
logs.forEach((l) => {
  console.log(`  ${l.kind} ${JSON.stringify(
    Object.fromEntries(Object.entries(l).filter(([k]) => k !== 'kind')))}`)
})

// 断言
const r1 = results[0].result === 'steer'
const r2 = results[1].result === 'steer'
const r3 = results[2].result === 'steer'
const r4 = results[3].result === 'no-progress-give-up'

const hasContinue = logs.some((l) => l.action === 'continue-anyway')
const hasGiveUp = logs.some((l) => l.action === 'give-up')

console.log('\n=== 结果 ===')
console.log((r1 ? '✅' : '❌') + ' turn1 纯文本 → steer（旧版这里通过）')
console.log((r2 ? '✅' : '❌') + ' turn2 纯文本 → steer（旧版这里就放弃了）')
console.log((r3 ? '✅' : '❌') + ' turn3 纯文本 → steer（streak=2 < 3）')
console.log((r4 ? '✅' : '❌') + ' turn4 纯文本 → give-up（streak=3 达上限）')
console.log((hasContinue ? '✅' : '❌') + ' 日志含 continue-anyway')
console.log((hasGiveUp ? '✅' : '❌') + ' 日志含 give-up')

process.exit(r1 && r2 && r3 && r4 && hasContinue && hasGiveUp ? 0 : 1)
'''

p = os.path.join(_HERE, "work", "_test_lc_ladder.mjs")
os.makedirs(os.path.dirname(p), exist_ok=True)
with open(p, "w", encoding="utf-8") as f:
    f.write(test)

r = subprocess.run(["node", p], capture_output=True, text=True,
                   timeout=60, encoding="utf-8", errors="replace")
print(r.stdout)
if r.stderr:
    print("STDERR:", r.stderr[:400])
print(f"\nexit={r.returncode}")
sys.exit(r.returncode)
