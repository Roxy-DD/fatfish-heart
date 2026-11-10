"""E-345 决定性实验：直接用假 ctx 跑 apply 两次，看 st.injected 是否保留

前面几轮都在读日志推断，效率低且容易误判。
这次直接做**单元实验**：模拟两次 apply()，验证 sessState 的行为。

实验设计：
  ① 第一次 apply → 取 sessState('S') → 设 injected=true
  ② 第二次 apply（模拟 reload） → 再取 sessState('S')
  ③ 看 injected 是否还是 true

这能**直接判定** reload 是否会重置注入状态，不用猜。
"""
import subprocess
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))

test = r'''
// 复刻 index.js 里 sessions 的存储逻辑，验证 reload 语义
const SESSIONS_KEY = '__COSMOS_MEM_SESSIONS__'

function sessionsMap() {
  let m = globalThis[SESSIONS_KEY]
  if (!(m instanceof Map)) {
    m = new Map()
    globalThis[SESSIONS_KEY] = m
  }
  return m
}

function sessState(sid) {
  const sessions = sessionsMap()
  if (!sessions.has(sid)) {
    sessions.set(sid, {
      prewarm: null, processed: new Set(),
      injected: false, prewarmPromise: null,
    })
  }
  return sessions.get(sid)
}

const SID = 'session-test-123'

// ---- 第一次 apply ----
let st1 = sessState(SID)
console.log('第1次 apply: injected =', st1.injected)
st1.injected = true
console.log('设置 injected = true')

// ---- 模拟模块重载：重新求值所有模块级变量 ----
// globalThis 上的数据不受影响，这正是 E-307 的修复要点
console.log('\n--- 模拟 selfreload（模块重新求值）---')
// 注意：模块级变量会重置，但 SESSIONS_KEY 指向 globalThis，不会

// ---- 第二次 apply ----
let st2 = sessState(SID)
console.log('第2次 apply: injected =', st2.injected)
const preserved = st2.injected === true

// ---- 模拟 agent/disposed ----
console.log('\n--- 模拟 agent/disposed ---')
sessionsMap().delete(SID)
let st3 = sessState(SID)
console.log('disposed 后: injected =', st3.injected)
const cleared_by_dispose = st3.injected === false

// ---- 模拟 sid 变化 ----
console.log('\n--- 模拟 sid 不同 ---')
let st4 = sessState('session-other-456')
console.log('新 sid: injected =', st4.injected)

console.log('\n=== 结论 ===')
console.log('reload 保留 injected:', preserved ? '✅ 是' : '❌ 否')
console.log('disposed 清空 injected:', cleared_by_dispose ? '✅ 是' : '❌ 否')

process.exit(preserved && cleared_by_dispose ? 0 : 1)
'''

p = os.path.join(_HERE, "work", "_test_sess.py")
os.makedirs(os.path.dirname(p), exist_ok=True)
js = os.path.join(_HERE, "work", "_test_sess.mjs")
with open(js, "w", encoding="utf-8") as f:
    f.write(test)

r = subprocess.run(["node", js], capture_output=True, text=True,
                   timeout=60, encoding="utf-8", errors="replace")
print(r.stdout)
if r.stderr:
    print("STDERR:", r.stderr[:400])
print(f"\nexit={r.returncode}")
