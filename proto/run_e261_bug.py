"""E-261 定位 suggest_from_triage 的逻辑 bug

E-260 发现：critical=14, minor=41，都远超 min=3，
             但 suggest_from_triage 返回 0 条。
             这是逻辑 bug，必须定位。
"""
import sys
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))

from store_v2 import MemoryStore
import triage as _tg
import inspect

DB = os.path.join(_HERE, "cosmos_mem", "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 74)
print("E-261 定位 suggest_from_triage bug")
print("=" * 74)

# ---------- ① 看源码 ----------
print("\n① 源码")
src = inspect.getsource(_tg.suggest_from_triage)
print(src)

# ---------- ② 逐步调试 ----------
print("\n② 逐步调试")
t = _tg.triage_all(st)
print(f"   triage_all 返回的 counts: {t['counts']}")
print(f"   t['critical'] 类型: {type(t['critical'])}, 长度: {len(t['critical'])}")
print(f"   t['minor'] 类型: {type(t['minor'])}, 长度: {len(t['minor'])}")

# 手动模拟循环
print("\n   手动模拟循环：")
note_hint_min = 3
for quad, tier, urgency in (
    ("critical", "note", "high"),
    ("minor", "scrap", "low"),
):
    items = t[quad]
    print(f"     {quad}: len={len(items)}, min={note_hint_min}, "
          f"通过={len(items) >= note_hint_min}")

# ---------- ③ 实际调用 ----------
print("\n③ 实际调用 suggest_from_triage")
r = _tg.suggest_from_triage(st, note_hint_min=3)
print(f"   返回 {len(r)} 条")
if r:
    for x in r:
        print(f"     {x}")

# ---------- ④ 检查 None 的可能 ----------
print("\n④ 检查是否被异常吞掉")
try:
    # 直接复制内部逻辑跑一遍
    t2 = _tg.triage_all(st, _tg.FREQ_HIGH, _tg.COST_HIGH)
    out = []
    for quad, tier, urgency in (
        ("critical", "note", "high"),
        ("minor", "scrap", "low"),
    ):
        items = t2[quad]
        if len(items) < 3:
            continue
        out.append({
            "quadrant": quad,
            "suggest_tier": tier,
            "urgency": urgency,
            "count": len(items),
            "ep_ids": [x["ep_id"] for x in items[:20]],
            "samples": [x["text"][:60] for x in items[:3]],
            "reason": _tg.STRATEGIES[quad],
        })
    print(f"   手工复制逻辑得到: {len(out)} 条")
    for x in out:
        print(f"     {x['quadrant']}: {x['count']} 条")

    # 对比
    print(f"\n   实际函数返回: {len(r)} 条")
    print(f"   手工逻辑返回: {len(out)} 条")
    if len(out) > 0 and len(r) == 0:
        print("   ⚠ 函数返回值与手工逻辑不一致 → 确认是 bug")
except Exception as e:
    print(f"   异常: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()

st.con.close()
