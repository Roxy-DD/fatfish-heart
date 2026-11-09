"""E-325 验证：scanCursor 修复后能否提取到新用户消息

E-324 发现的问题：
  scanCursor 记的是"末尾事件的 seq"，下次从末尾-2 扫，
  只能看到最后 2 条事件，看不到新用户消息。
  → recentCount 长期为 0 → 检索一直用旧 query

修复后验证：
  ① 用真实日志模拟 recentUserMessages
  ② 检查能否提取到最新的用户消息
  ③ 检查 cursor 是否只记录被采纳的消息 seq
"""
import sys
import os
import json
import io
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))

print("=" * 76)
print("E-325 验证 scanCursor 修复")
print("=" * 76)

# ============ ① 用 JS 直接测函数逻辑 ============
print("\n① 用 Node 直接测 recentUserMessages")

# 构造一个模拟 log
test_script = r'''
// 从 index.js 里提取 recentUserMessages 的逻辑做单元测试
const scanCursor = new Map()

function recentUserMessages(log, n, sid) {
  if (!Array.isArray(log) || n <= 0) return []
  let start = 0
  const last = sid ? scanCursor.get(sid) : undefined
  const out = []
  for (let i = log.length - 1; i >= start && out.length < n; i--) {
    const e = log[i]
    if (!e || e.type !== 'user/message') continue
    if (typeof last === 'number' && typeof e.seq === 'number' &&
        e.seq <= last) continue
    const d = e.data
    if (!d) continue
    const src = d.source && d.source.kind
    if (src && src !== 'user') continue
    let text = ''
    if (typeof d.content === 'string') text = d.content
    else if (Array.isArray(d.content)) {
      text = d.content
        .filter((c) => c && c.type === 'text' && typeof c.text === 'string')
        .map((c) => c.text).join(' ')
    }
    text = (text || '').trim()
    if (text.startsWith('Time sampled while preparing')) continue
    if (text.startsWith('Current runtime context')) continue
    if (text.startsWith('background job')) continue
    if (text) out.push({ seq: e.seq, text })
  }
  if (sid && out.length > 0) {
    const maxSeq = Math.max(...out.map((x) =>
      typeof x.seq === 'number' ? x.seq : 0))
    if (maxSeq > 0) {
      const prev = scanCursor.get(sid)
      if (typeof prev !== 'number' || maxSeq > prev) {
        scanCursor.set(sid, maxSeq)
      }
    }
  }
  return out.reverse()
}

// 构造模拟 log：穿插系统消息和用户消息
const log = [
  { seq: 1, type: 'user/message', data: { source: { kind: 'user' },
    content: [{ type: 'text', text: '用户消息 A' }] } },
  { seq: 2, type: 'assistant/message', data: {} },
  { seq: 3, type: 'user/message',
    data: { source: { kind: 'time-context' },
            content: [{ type: 'text', text: 'Time sampled while preparing turn 1' }] } },
  { seq: 4, type: 'user/message',
    data: { source: { kind: 'cosmos-mem-lifecycle' },
            content: [{ type: 'text', text: '### 继续自主迭代' }] } },
  { seq: 5, type: 'user/message', data: { source: { kind: 'user' },
    content: [{ type: 'text', text: '用户消息 B' }] } },
]

console.log('=== 第一次调用 ===')
let r1 = recentUserMessages(log, 8, 'test')
console.log('提取到:', JSON.stringify(r1))
console.log('cursor:', scanCursor.get('test'))

console.log('\n=== 第二次调用（无新消息）===')
let r2 = recentUserMessages(log, 8, 'test')
console.log('提取到:', JSON.stringify(r2))
console.log('（应为空，因为都已处理过）')

console.log('\n=== 加入新消息后再调用 ===')
log.push({ seq: 6, type: 'user/message', data: { source: { kind: 'user' },
  content: [{ type: 'text', text: '用户消息 C（新）' }] } })
let r3 = recentUserMessages(log, 8, 'test')
console.log('提取到:', JSON.stringify(r3))
console.log('（应该只有 C）')

// 断言
const ok1 = r1.length === 2 && r1[0].text === '用户消息 A' && r1[1].text === '用户消息 B'
const ok2 = r2.length === 0
const ok3 = r3.length === 1 && r3[0].text === '用户消息 C（新）'

console.log('\n=== 结果 ===')
console.log((ok1 ? '✅' : '❌') + ' 首次提取到 A 和 B（排除系统和 lifecycle 消息）')
console.log((ok2 ? '✅' : '❌') + ' 重复调用不重复提取')
console.log((ok3 ? '✅' : '❌') + ' 新消息能被提取')

process.exit(ok1 && ok2 && ok3 ? 0 : 1)
'''

import subprocess
tmp = os.path.join(_HERE, "work", "_test_cursor.mjs")
os.makedirs(os.path.dirname(tmp), exist_ok=True)
with open(tmp, "w", encoding="utf-8") as f:
    f.write(test_script)

r = subprocess.run(["node", tmp], capture_output=True, text=True,
                   timeout=60, encoding="utf-8", errors="replace")
print(r.stdout)
if r.stderr:
    print("STDERR:", r.stderr[:500])

# ============ ② 检查真实日志的当前状态 ============
print("\n② 真实日志的 recentCount 趋势")

log_path = os.path.join(_HERE, "cosmos_mem", "plugin-debug.jsonl")
if os.path.exists(log_path):
    with io.open(log_path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()[-2000:]

    scans = []
    for line in lines:
        try:
            e = json.loads(line)
            if e.get("kind") == "assemble-scan":
                scans.append(e)
        except Exception:
            pass

    print(f"   最近 assemble-scan: {len(scans)} 条")
    nonzero = [s for s in scans[-30:] if s.get("recentCount", 0) > 0]
    print(f"   最近 30 次里 recentCount>0 的: {len(nonzero)}")

    if nonzero:
        for s in nonzero[-5:]:
            print(f"     {s.get('t','')[:19]}  "
                  f"recentCount={s.get('recentCount')}  "
                  f"latest='{str(s.get('latest',''))[:35]}'")

# ============ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

ok = r.returncode == 0
print(f"""
  单元测试: {'✅ 通过' if ok else '❌ 失败'}

  {'✅ scanCursor 逻辑已修复' if ok else '❌ 仍有问题'}

  修复要点：
    · cursor 记"最后一条被采纳的用户消息的 seq"
    · 下次跳过 <= cursor 的消息
    · 新消息 seq 更大 → 一定能看到
    · 只在实际提取到消息时才更新 cursor
""")

out = {
    "experiment": "E-325",
    "unit_test_ok": ok,
}
p = os.path.join(_HERE, "results", "e325_cursor_fix.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e325_cursor_fix.json")
