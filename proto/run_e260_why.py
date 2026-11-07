"""E-260 诊断：为什么 62 条记忆一条建议都发现不了？

E-259 用真实库扫描，三个检测器全部返回 0：
  · suggest_from_triage → 0
  · suggest_note_topics → 0
  · compile_all_topics  → 0

但库里有 62 条有效记忆。这不合理，必须查清楚是：
  a) 数据确实不满足条件（正常）
  b) 检测逻辑有 bug（真问题）
"""
import sys
import os
import json
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

from store_v2 import MemoryStore
import triage as _tg
import notebook as _nb

DB = os.path.join(_MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 76)
print("E-260 诊断：为什么检测不到建议")
print("=" * 76)

# ============ ① 分诊检测 ============
print("\n① suggest_from_triage 为什么是 0")
t = _tg.triage_all(st, limit=1000)
print(f"   分诊结果: {t['counts']}")
print(f"   阈值: {t['thresholds']}")

# suggest_from_triage 的条件：critical 或 minor 的条数 >= note_hint_min
print(f"\n   critical 条数: {t['counts'].get('critical', 0)}")
print(f"   minor 条数:    {t['counts'].get('minor', 0)}")
print(f"   默认 min: 3")
if t['counts'].get('critical', 0) < 3 and t['counts'].get('minor', 0) < 3:
    print("   → 确实不满足条件（条数不够）")
else:
    print("   → 条数够了但没建议，可能是逻辑 bug")

# ============ ② 主题累积检测 ============
print("\n② suggest_note_topics 为什么是 0")
# 看 triggers 字段
rows = st.con.execute(
    "SELECT ep_id, raw_text, triggers FROM episodes WHERE invalid_at IS NULL").fetchall()
print(f"   有效记忆: {len(rows)} 条")

# 统计 triggers
all_trigs = []
n_empty = 0
for eid, txt, trigs in rows:
    try:
        tl = json.loads(trigs) if trigs else []
    except Exception:
        tl = []
    if not tl:
        n_empty += 1
    all_trigs.extend(tl)

print(f"   triggers 为空的记忆: {n_empty}/{len(rows)}")
print(f"   triggers 总数: {len(all_trigs)}")

if all_trigs:
    cnt = Counter(all_trigs)
    print(f"   最高频的 trigger（前 10）:")
    for w, c in cnt.most_common(10):
        mark = "✅" if c >= 3 else "  "
        print(f"     {mark} {w}: {c}")
else:
    print("   ⚠ 所有记忆的 triggers 都是空的！")

# 检查 extract 逻辑
print("\n   检查 triggers 是怎么生成的：")
src = open(os.path.join(_MEM, "store_v1.py"), encoding="utf-8").read()
idx = src.find("def detect_triggers")
if idx > 0:
    seg = src[idx:idx+800]
    print("   (store_v1.detect_triggers 存在)")

# 测试 detect_triggers
from store_v1 import detect_triggers
test_texts = [
    "用户要求记忆系统支持跨版本回退",
    "登录密码是 8848",
    "项目使用 Python 3.12 和 onnxruntime",
    "今天天气不错",
]
print("\n   实测 detect_triggers：")
for txt in test_texts:
    try:
        r = detect_triggers(txt)
        print(f"     '{txt[:30]}' → {r}")
    except Exception as e:
        print(f"     '{txt[:30]}' → 错误: {e}")

# ============ ③ 随笔装订检测 ============
print("\n③ compile_all_topics 为什么是 0")
n_scrap = st.con.execute(
    "SELECT COUNT(*) FROM notes WHERE invalid_at IS NULL AND tier='scrap'").fetchone()[0]
print(f"   scrap 层笔记数: {n_scrap}")
if n_scrap == 0:
    print("   → 本来就没有随笔，检测不到是正常的")

# ============ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

print(f"""
  逐项确认：

  ① 分诊检测: critical={t['counts'].get('critical',0)} minor={t['counts'].get('minor',0)}
     → {'数据不满足（正常）' if max(t['counts'].get('critical',0), t['counts'].get('minor',0)) < 3 else '需要查'}

  ② 主题检测: triggers 非空 {len(all_trigs)} 个
     → {'triggers 数据不足（可能是记忆内容本身没有触发词）' if len(all_trigs) < 9 else '需要查'}

  ③ 装订检测: scrap 层 {n_scrap} 条
     → {'本来就没有随笔（正常）' if n_scrap == 0 else '需要查'}

  关键发现：如果 triggers 普遍为空或重复度低，
  那么"按 trigger 词频聚类"这个算法本身就选错了维度。
""")

st.con.close()

out = {
    "experiment": "E-260",
    "triage_counts": t["counts"],
    "valid_memories": len(rows),
    "triggers_empty": n_empty,
    "triggers_total": len(all_trigs),
    "top_triggers": Counter(all_trigs).most_common(10) if all_trigs else [],
    "scrap_count": n_scrap,
}
p = os.path.join(_HERE, "results", "e260_why_no_hint.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e260_why_no_hint.json")
