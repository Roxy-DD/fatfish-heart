"""E-329 关键发现：log 被重置导致消息丢失

E-328 发现：
  logLen: [27211, 27219, 2886, 27224, 27230]
                 ^^^^ 突然变成 2886

这说明 session log 被**重置**了（可能因为压缩/checkpoint）。

后果：
  · scanCursor 记的是旧 log 的 seq（比如 27000+）
  · 新 log 的 seq 从 1 开始
  · 条件 seq <= last 让所有新消息都被跳过！
  → recentCount 一直是 0

这是一个**真实且严重**的 bug：
  session 压缩/重置后，cursor 失效，永久看不到新消息。

验证并修复。
"""
import sys
import os
import json
import io
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

log_path = os.path.join(MEM, "plugin-debug.jsonl")

print("=" * 76)
print("E-329 验证 log 重置导致 cursor 失效")
print("=" * 76)

# ============ ① 找 logLen 突变点 ============
print("\n① logLen 时间序列（找突变）")

scans = []
if os.path.exists(log_path):
    with io.open(log_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f.readlines()[-5000:]:
            try:
                e = json.loads(line)
                if e.get("kind") == "assemble-scan":
                    scans.append(e)
            except Exception:
                pass

print(f"   assemble-scan 记录: {len(scans)}")

# 找突变
resets = []
prev_len = None
for s in scans:
    L = s.get("logLen")
    if prev_len is not None and isinstance(L, int) and isinstance(prev_len, int):
        if L < prev_len * 0.5 and prev_len > 1000:
            resets.append({
                "t": s.get("t", "")[:19],
                "from": prev_len,
                "to": L,
                "recentCount": s.get("recentCount"),
            })
    prev_len = L

print(f"\n   检测到 log 重置: {len(resets)} 次")
for r in resets:
    print(f"     {r['t']}  {r['from']} → {r['to']}  "
          f"recentCount={r['recentCount']}")

# ============ ② 验证：重置后 recentCount 是否一直为 0 ============
print("\n② 重置后的 recentCount 趋势")

if resets:
    last_reset_t = resets[-1]["t"]
    after = [s for s in scans if s.get("t", "")[:19] >= last_reset_t]
    print(f"   最后一次重置后: {len(after)} 条记录")
    counts = [s.get("recentCount") for s in after[-20:]]
    print(f"   recentCount: {counts}")

    n_zero = sum(1 for c in counts if c == 0)
    print(f"\n   为 0 的次数: {n_zero}/{len(counts)}")

    if n_zero == len(counts) and len(counts) > 3:
        print("   ❌ 确认：log 重置后 recentCount 一直是 0")
        print("      → cursor 记录了旧 log 的大 seq，新 log 的小 seq 全被跳过")
        confirmed = True
    else:
        print("   🟡 不完全是 0，可能有其他因素")
        confirmed = False
else:
    print("   未检测到重置")
    confirmed = False

# ============ ③ 分析修复方案 ============
print("\n③ 修复方案分析")

print("""
   问题代码：
     if (typeof last === 'number' && typeof e.seq === 'number' &&
         e.seq <= last) continue

   当 log 重置后：
     · last = 27000（旧 log 的 seq）
     · 新 log 的 e.seq = 1, 2, 3...
     · 1 <= 27000 → 全部跳过

   修复方案：
     A. 检测 log 重置：如果当前 log 最后一个 seq < last，说明重置了，清空 cursor
     B. 不用 seq 做 cursor，改用"消息内容的 hash"
     C. 用 log.length 变化 + seq 双重判断

   选 A（最简单可靠）：
     每次扫描前检查：log 末尾的 seq 是否 < cursor
     如果小于，说明 log 被重置 → 清空 cursor 重新扫
""")

# ============ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

print(f"""
  log 重置次数: {len(resets)}
  重置后 recentCount 全为 0: {confirmed}

  {'✅ 确认根因：log 重置导致 cursor 失效' if confirmed else '🟡 需要进一步验证'}
""")

out = {
    "experiment": "E-329",
    "n_resets": len(resets),
    "resets": resets,
    "confirmed": confirmed,
}
p = os.path.join(_HERE, "results", "e329_log_reset.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e329_log_reset.json")
