"""E-348 最后一个矛盾：91.3% 的轮次 recentCount=0，为什么还能注入？

E-347 数据：
  assemble-scan 里 91.3% 的 recentCount=0
  但 inject 发生了 28 次

代码里明明有 `if (!latest) return result`。

唯一合理解释：
  assemble 会被**调用很多次**（每个 step 一次，600 次），
  但真正「注入」只发生 28 次。
  注入发生的那些轮次里，recentCount 是 >0 的。
  我 E-347 的 30 秒窗口对齐错了。

验证方法（不再靠时间对齐）：
  直接找 inject 触发**前后同一毫秒级**的 assemble 记录，
  或者反过来：列出所有 recentCount>0 的轮次，
  看它们是否一一对应 28 次 inject。
"""
import sys
import os
import json
import time
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
LOG = os.path.join(MEM, "plugin-debug.jsonl")

print("=" * 78)
print("E-348 recentCount=0 与 inject 的关系")
print("=" * 78)

events = []
if os.path.exists(LOG):
    with open(LOG, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                events.append(json.loads(line))
            except Exception:
                pass

scans = [e for e in events if e.get("kind") == "assemble-scan"]
assembles = [e for e in events if e.get("kind") == "assemble"]

print(f"\n① 事件统计")
print(f"   assemble-scan: {len(scans)}")
print(f"   assemble（真正注入）: {len(assembles)}")

# ---------- ② assemble 记录本身就带 query ----------
print("\n② assemble 记录自带的 query（这是注入时的真实 query）")
for a in assembles[-15:]:
    t = str(a.get("t", ""))[:19]
    q = str(a.get("query", ""))[:55]
    hits = a.get("hits")
    ms = a.get("msSearch")
    print(f"   {t}  hits={hits:<2} {ms:>6}ms  '{q}'")

# ---------- ③ 关键：这些 query 是不是本轮的 ----------
print("\n③ 这些 query 是否对应当轮用户消息")
qs = [str(a.get("query", "")) for a in assembles]
uniq = []
for q in qs:
    if q not in uniq:
        uniq.append(q)
print(f"   不同 query: {len(uniq)} 个（共 {len(qs)} 次注入）")
for q in uniq[-12:]:
    print(f"     '{q[:60]}'")

# ---------- ④ 是否同一 query 被反复使用 ----------
print("\n④ 同一 query 的重复使用")
from collections import Counter
cnt = Counter(qs)
for q, n in cnt.most_common(10):
    if n > 1:
        print(f"   {n:>3} 次  '{q[:60]}'")

dup_total = sum(n for q, n in cnt.items() if n > 1)
print(f"\n   重复使用的注入: {dup_total}/{len(qs)}")

# ---------- ⑤ 判定 ----------
print("\n⑤ 判定")

if len(uniq) == len(qs):
    print("""
   ✅ 每个 query 都不重复 → 每次注入用的都是当轮的真实消息
      没有"用旧消息检索"的问题
""")
    verdict = "ok"
else:
    print(f"""
   ⚠ 有 {dup_total}/{len(qs)} 次注入复用了同一个 query

   这说明：多次注入用了同一条用户消息。
   可能原因：
     · 同一轮里 assemble 被调用多次（每个 step 一次）
     · 但 st.injected 应该阻止第二次注入

   如果确实是同一轮内多次注入 → 那才是 bug。
""")
    verdict = "dup"

# ---------- ⑥ 检查同一轮多次注入 ----------
print("\n⑥ 同一轮内是否多次注入（按时间聚集）")

def to_ts(t):
    try:
        return time.mktime(time.strptime(t[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None

ats = [(to_ts(a.get("t")), a) for a in assembles if to_ts(a.get("t"))]
ats.sort()

clusters = []
cur = [ats[0]] if ats else []
for i in range(1, len(ats)):
    if ats[i][0] - ats[i-1][0] < 60:   # 60 秒内算同一轮
        cur.append(ats[i])
    else:
        clusters.append(cur)
        cur = [ats[i]]
if cur:
    clusters.append(cur)

print(f"   按 60 秒聚类: {len(clusters)} 轮")
multi = [c for c in clusters if len(c) > 1]
print(f"   同一轮内多次注入: {len(multi)} 轮")

for c in multi[:5]:
    t0 = time.strftime("%H:%M:%S", time.localtime(c[0][0]))
    t1 = time.strftime("%H:%M:%S", time.localtime(c[-1][0]))
    q = str(c[0][1].get("query", ""))[:40]
    print(f"     {t0}~{t1}  {len(c)} 次  '{q}'")

out = {
    "experiment": "E-348",
    "n_inject": len(assembles),
    "n_unique_query": len(uniq),
    "dup_total": dup_total,
    "n_multi_clusters": len(multi),
    "verdict": verdict,
}
p = os.path.join(_HERE, "results", "e348_query_dup.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e348_query_dup.json")
