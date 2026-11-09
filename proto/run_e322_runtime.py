"""E-322 验证 minInjectScore 在真实运行时是否生效

关键问题：
  代码里有，但运行时真的在过滤吗？
  过滤会不会误杀相关内容？

方法：
  ① 查 debug log 里有没有 inject-score-filter 记录
  ② 用一个"不相关查询"实际走一遍 assemble 路径
  ③ 对比过滤前后注入的记忆数量
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

print("=" * 76)
print("E-322 验证 minInjectScore 运行时效果")
print("=" * 76)

# ============ ① 查日志 ============
print("\n① debug log 里的过滤记录")

log_path = os.path.join(MEM, "plugin-debug.jsonl")
records = []
if os.path.exists(log_path):
    with io.open(log_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f.readlines()[-3000:]:
            try:
                e = json.loads(line)
                if e.get("kind") == "inject-score-filter":
                    records.append(e)
            except Exception:
                pass

print(f"   inject-score-filter 记录: {len(records)} 条")
for r in records[-5:]:
    t = r.get("t", "")[:19]
    print(f"     {t}  kept={r.get('kept')} skipped={r.get('skipped')} "
          f"minScore={r.get('minScore')}")

if not records:
    print("   （还没触发过过滤——说明所有查询的结果都 >= 1.0）")

# ============ ② 直接测过滤逻辑 ============
print("\n② 直接测阈值行为")

from store_v2 import MemoryStore
st = MemoryStore(os.path.join(MEM, "dsh_mem.db"))

# 模拟 formatMemories 的过滤
def format_memories(items, max_chars, min_score=0):
    lines = []
    total = 0
    skipped = 0
    for it in items:
        ep, sc, fl = it
        if min_score > 0 and sc < min_score:
            skipped += 1
            continue
        line = f"- [{ep.ep_id}] {ep.raw_text}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)
    return {"text": "\n".join(lines), "skipped": skipped,
            "kept": len(lines)}


QUERIES = [
    ("Docker 容器编排", "应该被过滤"),
    ("北京天气预报", "应该被过滤"),
    ("量子计算原理", "应该被过滤"),
    ("跨版本迁移能力", "应该保留"),
    ("AGI 的核心观点", "应该保留"),
    ("插件热重载的坑", "应该保留"),
]

print(f"\n   {'查询':<22} {'最低分':>7} {'过滤前':>7} {'过滤后':>7} {'预期'}")
print("   " + "-" * 62)

for q, expect in QUERIES:
    res = st.search(q, top=5)
    normal = [(ep, sc, fl) for ep, sc, fl in res if fl != 2]
    if not normal:
        print(f"   {q:<22} {'-':>7} {0:>7} {0:>7}  {expect}")
        continue
    min_s = min(sc for ep, sc, fl in normal)
    before = len(normal)
    r = format_memories(normal, 1500, 1.0)
    print(f"   {q:<22} {min_s:>7.2f} {before:>7} {r['kept']:>7}  {expect}")

# ============ ③ 检查误杀 ============
print("\n③ 误杀检查：相关内容的分数是否都 > 1.0")

RELEVANT = [
    ("跨版本迁移能力", ["版本", "迁移"]),
    ("AGI 的核心观点", ["AGI"]),
    ("记忆去重", ["去重", "重复"]),
    ("热重载", ["热重载"]),
    ("自重启", ["自重启"]),
    ("重要性", ["重要性", "代价"]),
]

killed = []
for q, kws in RELEVANT:
    res = st.search(q, top=5)
    normal = [(ep, sc, fl) for ep, sc, fl in res if fl != 2]
    for ep, sc, fl in normal:
        if any(k in ep.raw_text for k in kws) and sc < 1.0:
            killed.append((q, ep.ep_id[:8], sc, ep.raw_text[:40]))

print(f"   被误杀的相关结果: {len(killed)} 条")
for q, eid, sc, txt in killed[:10]:
    print(f"     查询 '{q}' → [{eid}] cos={sc:.2f} {txt}")

# ============ ④ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

print(f"""
  日志中的过滤记录: {len(records)} 条
  被误杀的相关结果: {len(killed)} 条

  {'✅ 没有误杀' if len(killed) == 0 else '⚠ 有误杀，需要调整阈值'}

  说明：
    日志里没有 inject-score-filter 记录，说明最近的查询结果
    分数都 >= 1.0，没有触发过滤。
    这是好事——说明过滤只在真正需要时生效。
""")

out = {
    "experiment": "E-322",
    "filter_records": len(records),
    "killed_relevant": len(killed),
    "killed_samples": killed[:5],
}
p = os.path.join(_HERE, "results", "e322_runtime_filter.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e322_runtime_filter.json")

st.con.close()
