"""E-327 验证 recentUserMessages 的返回顺序

E-326 发现：recentCount=8 但 latest 是旧消息。
说明数组顺序可能反了。

在 E-325 的单元测试里，r1 = [用户消息 A(seq1), 用户消息 B(seq5)]
A 在前 B 在后（从旧到新）—— 这看起来是对的。

但真实场景可能不同。需要模拟真实日志结构。
"""
import sys
import os
import json
import io
import subprocess
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))

print("=" * 76)
print("E-327 验证返回顺序")
print("=" * 76)

# ============ ① 从真实日志提取一个真实的 log 样本 ============
print("\n① 检查真实日志里 user/message 的 seq 顺序")

log_path = os.path.join(_HERE, "cosmos_mem", "plugin-debug.jsonl")
if os.path.exists(log_path):
    with io.open(log_path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()[-3000:]

    # 从 scan-debug 的 samples 里看 seq
    seqs = []
    for line in lines:
        try:
            e = json.loads(line)
            if e.get("kind") == "scan-debug":
                for s in (e.get("samples") or []):
                    keys = s.get("keys") or []
                    if "seq" in keys:
                        # 从 dataShape 里找 seq
                        shape = s.get("dataShape", "")
                        # 无法直接拿到 seq，跳过
                        pass
        except Exception:
            pass

    print("   （scan-debug 样本里没有直接记录 seq）")

# ============ ② 用更真实的模拟测试 ============
print("\n② 真实结构模拟测试")

test_script = r'''
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

// 模拟 8 条历史用户消息（模仿真实场景：老消息在前，新消息在后）
const log = []
let seq = 1
const msgs = [
  '你能看到我刚才在笔记本里面加入的一个新笔记吗',
  '继续修复，并且你现在又停止了',
  '继续',
  '所以你分析出来为什么停止了呢',
  '你能找到能让你自己重启的插件吗',
  '继续继续',
  '继续自主迭代第一次',
  '继续自主迭代第二次',
]
for (const m of msgs) {
  log.push({ seq: seq++, type: 'user/message',
    data: { source: { kind: 'user' },
            content: [{ type: 'text', text: m }] } })
  log.push({ seq: seq++, type: 'assistant/message', data: {} })
}

console.log('构造的 log 长度:', log.length)
console.log('用户消息数:', msgs.length)

const r = recentUserMessages(log, 8, 'test')
console.log('\n提取结果（顺序）:')
r.forEach((x, i) => console.log(`  [${i}] seq=${x.seq} "${x.text}"`))

// 检查顺序
const isAscending = r.every((x, i) => i === 0 || x.seq > r[i-1].seq)
console.log('\n顺序是从旧到新:', isAscending ? '✅' : '❌')

const latest = r.length ? r[r.length - 1].text : ''
console.log('latest 取值:', latest)
console.log('最新消息应该是:', msgs[msgs.length - 1])
console.log('latest 正确:', latest === msgs[msgs.length - 1] ? '✅' : '❌')

process.exit(isAscending && latest === msgs[msgs.length - 1] ? 0 : 1)
'''

tmp = os.path.join(_HERE, "work", "_test_order.mjs")
os.makedirs(os.path.dirname(tmp), exist_ok=True)
with open(tmp, "w", encoding="utf-8") as f:
    f.write(test_script)

r = subprocess.run(["node", tmp], capture_output=True, text=True,
                   timeout=60, encoding="utf-8", errors="replace")
print(r.stdout)
if r.stderr:
    print("STDERR:", r.stderr[:300])

# ============ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

print(f"""
  单元测试: {'✅ 通过' if r.returncode == 0 else '❌ 失败'}

  如果顺序正确但真实 latest 还是旧的，说明：
    · 真实日志里最后 8 条 user/message 都是老消息
    · 新的用户消息还没进入 log（或 source 不是 'user'）
""")

out = {"experiment": "E-327", "order_test_ok": r.returncode == 0}
p = os.path.join(_HERE, "results", "e327_order.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e327_order.json")
