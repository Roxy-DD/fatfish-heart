"""E-499 量化 assemble 钩子的真实开销（FISH-15 第一步）

不预设"哪里慢"，先量。
模拟真实的 log 结构（36908 条，含大量 assistant/tool 事件），
测 recentUserMessages 的实际遍历成本。

如果遍历成本高 → 优化它（增量扫描/索引）
如果成本低 → 真正的 token 开销在别处，继续找
"""
import json
import time

# 用 node 跑真实函数（js 逻辑无法用 python 复现）
JS = r"""
// 复刻 index.js 里 recentUserMessages 的核心循环
function recentUserMessages(log, n) {
  const out = []
  for (let i = log.length - 1; i >= 0 && out.length < n; i--) {
    const e = log[i]
    if (!e || e.type !== 'user/message') continue
    const d = e.data
    if (!d) continue
    const src = d.source && d.source.kind
    if (src && src !== 'user') continue
    let text = typeof d.content === 'string' ? d.content : ''
    out.push({ seq: e.seq, text })
  }
  return out
}

function makeLog(n, userEvery) {
  const log = []
  for (let i = 0; i < n; i++) {
    const isUser = (i % userEvery === 0)
    log.push({
      seq: i + 1,
      type: isUser ? 'user/message' : (i % 3 === 0 ? 'tool/result' : 'assistant/message'),
      data: isUser
        ? { content: 'user message ' + i, source: { kind: 'user' } }
        : { content: 'x'.repeat(200) },
    })
  }
  return log
}

const results = []
for (const n of [1000, 5000, 20000, 36908]) {
  const log = makeLog(n, 40)   // 每 40 条一个用户消息（真实比例稀疏）
  // 预热
  recentUserMessages(log, 8)
  const t0 = process.hrtime.bigint()
  const ITER = 100
  for (let k = 0; k < ITER; k++) recentUserMessages(log, 8)
  const t1 = process.hrtime.bigint()
  const ms = Number(t1 - t0) / 1e6 / ITER
  results.push({ logLen: n, msPerCall: Math.round(ms * 1000) / 1000 })
}
console.log(JSON.stringify(results))
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
tmp = os.path.join(HERE, "_e499_bench.mjs")
with open(tmp, "w", encoding="utf-8") as f:
    f.write(JS)

try:
    r = subprocess.run(["node", tmp], capture_output=True, text=True,
                       encoding="utf-8", timeout=300)
    if r.returncode != 0:
        print("node 失败:", r.stderr[:300])
        sys.exit(1)
    data = json.loads(r.stdout.strip().split("\n")[-1])
finally:
    if os.path.exists(tmp):
        os.remove(tmp)

print("=" * 70)
print("E-499 assemble 钩子遍历成本实测")
print("=" * 70)
print(f"\n  {'log 长度':>10} {'每次耗时':>12}")
for d in data:
    print(f"  {d['logLen']:>10} {d['msPerCall']:>10} ms")

# 计算真实影响：2449 次 assemble × 单次成本
real_log = 36908
per_call = next((d["msPerCall"] for d in data if d["logLen"] == real_log), None)
if per_call:
    total = per_call * 2449 / 1000
    print(f"\n  真实场景：log={real_log}，历史 2449 次 assemble")
    print(f"  总遍历耗时 ≈ {total:.1f} 秒")

out = {"experiment": "E-499", "measurements": data,
       "assemble_count": 2449, "real_log_len": real_log}
p = os.path.join(HERE, "results", "e499_assemble_cost.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 {p}")
