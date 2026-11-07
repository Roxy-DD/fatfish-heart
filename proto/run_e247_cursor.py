"""E-247 诊断：sleep 游标真的有效吗？

E-246 观察到的现象：
  第一次 prepare: window=recent, 20 候选对
  睡眠后 cursor 推进
  第二次 prepare: window=recent, fresh=0, 还是 20 候选对

问题：游标推进了，但兜底窗口永远扫最近 50 条，
      所以每次都会重复扫同一批老记忆，游标形同虚设。

验证：
  ① fresh 为空时，是否该走兜底？
  ② 兜底窗口是否应该排除"已经扫过"的？
  ③ 是否有"同一对候选被反复拿出来"的情况？
"""
import sys
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

from store_v2 import MemoryStore
import consolidate as _con
from store_v1 import Embedder

DB = os.path.join(_MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 74)
print("E-247 诊断：sleep 游标有效性")
print("=" * 74)

cursor = _con.get_cursor(st)
print(f"\n① 当前游标: {cursor:.0f}")

# 游标之后新增了多少
import time
n_after = st.con.execute(
    "SELECT COUNT(*) FROM episodes WHERE invalid_at IS NULL AND created_at > ?",
    (cursor,)).fetchone()[0]
n_total = st.con.execute(
    "SELECT COUNT(*) FROM episodes WHERE invalid_at IS NULL").fetchone()[0]
print(f"   游标后新增: {n_after} 条")
print(f"   有效总数:   {n_total} 条")

# ---------- ② 连续两次 prepare，看候选对是否重复 ----------
print(f"\n② 连续两次 prepare，候选对是否重复")
p1 = _con.prepare_sleep_pack(st, Embedder.get())
pairs1 = set()
for p in p1.pairs:
    key = tuple(sorted([p.a_id, p.b_id]))
    pairs1.add(key)
print(f"   第1次: window={p1.window}, pairs={len(p1.pairs)}")

p2 = _con.prepare_sleep_pack(st, Embedder.get())
pairs2 = set()
for p in p2.pairs:
    key = tuple(sorted([p.a_id, p.b_id]))
    pairs2.add(key)
print(f"   第2次: window={p2.window}, pairs={len(p2.pairs)}")

overlap = pairs1 & pairs2
print(f"   重复的候选对: {len(overlap)}/{len(pairs1)}")
if len(overlap) == len(pairs1):
    print(f"   ⚠ 完全重复 —— 每次 sleep 都在看同一批对")

# ---------- ③ 游标是否被使用 ----------
print(f"\n③ 游标在 prepare 里的实际作用")
src = open(os.path.join(_MEM, "consolidate.py"), encoding="utf-8").read()
# 找 cursor 相关逻辑
import re
for m in re.finditer(r".*cursor.*", src):
    line = m.group(0).strip()
    if line.startswith("#") or not line:
        continue
    if any(k in line for k in ["cursor", "fresh"]):
        print(f"   {line[:90]}")

# ---------- ④ 结论 ----------
print(f"\n{'='*74}")
print("诊断结论")
print("=" * 74)

if len(overlap) == len(pairs1) and len(pairs1) > 0:
    print(f"""
   ⚠ 问题确认：候选对完全重复。

   根因（读代码得出）：
     prepare_sleep_pack 的逻辑是：
       if cursor > 0: 用 fresh 窗口
       if fresh 不足 2 条: 退回 recent 窗口（最近 50 条）

     而实际使用中，游标推进后新增记忆通常不足 2 条，
     于是每次都退回 recent 窗口 —— 即"总是扫最近 50 条"。

     游标在"增量不触发"时**完全不生效**。

   后果：
     · 同一批高相似对会被反复拿出、反复判定
     · 如果上次判定"不合并"，这次还会再问一遍（浪费模型调用）
     · 如果上次合并了，那对已消失，但其他老对还在

   修法建议：
     记录"哪些候选对已经被判定过"，下次跳过。
     这是纯机械的记录，不需要模型参与。
""")
else:
    print(f"\n   候选对不完全重复（{len(overlap)}/{len(pairs1)}），游标部分有效。")

st.con.close()

out = {
    "experiment": "E-247",
    "cursor": cursor,
    "n_after_cursor": n_after,
    "n_total": n_total,
    "pairs_first": len(pairs1),
    "pairs_second": len(pairs2),
    "overlap": len(overlap),
    "fully_duplicated": len(overlap) == len(pairs1) and len(pairs1) > 0,
}
p = os.path.join(_HERE, "results", "e247_cursor.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e247_cursor.json")
