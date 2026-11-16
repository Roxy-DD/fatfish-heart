"""验证清理后的生产库：保护生效 + 无测试残留 + 关键记忆在场"""
import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proto", "cosmos_mem"))
from store_v2 import MemoryStore
import hook as _hook

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proto", "cosmos_mem", "dsh_mem.db")
st = MemoryStore(DB)

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 72)
print("清理后生产库验证")
print("=" * 72)

stats = st.stats()
print(f"\n统计: {json.dumps(stats, ensure_ascii=False)}")

# 1. 测试数据已清除
print("\n【1】测试数据已清除")
rows = st.con.execute(
    "SELECT raw_text FROM episodes WHERE invalid_at IS NULL").fetchall()
all_txt = " ".join(r[0] for r in rows)
for bad in ["8848", "5678", "卡罗拉", "沪A", "MacBook", "Cursor", "出大事"]:
    check(f"无 '{bad}'", bad not in all_txt)

# 2. 保护生效
print("\n【2】保护生效")
hs = _hook.stats(st)
check("L0 有 5 条", hs["by_level"].get("L0", 0) == 5, f"{hs['by_level'].get('L0',0)}")
check("L1 有 16 条", hs["by_level"].get("L1", 0) == 16, f"{hs['by_level'].get('L1',0)}")
check("L2 有 12 条", hs["by_level"].get("L2", 0) == 12, f"{hs['by_level'].get('L2',0)}")

# 3. L0 无法失效
print("\n【3】L0 不可变验证")
l0 = [p for p in _hook.protected_of(st) if p["level"] == "L0"]
if l0:
    eid = l0[0]["ep_id"]
    ok = st.invalidate(eid)
    ep = st.get(eid)
    check("L0 invalidate 被拒", ok is False and ep.invalid_at is None)
    ok2 = st.revise(eid, new_text="试试改动红线")
    check("L0 revise 被拒", ok2 is False)

# 4. L1 强制在场
print("\n【4】L1 强制在场")
res = st.search("完全不相关的问题xyz123", top=3)
got = {ep.ep_id for ep, _, fl in res if fl == 2}
l01 = _hook.present_ids(st)
check("L0/L1 全部在场", l01.issubset(got),
      f"{len(got)}/{len(l01)}")

# 5. 保留的关键内容
print("\n【5】关键内容仍在")
must_have = [
    ("红线", "红线R1" in all_txt and "红线R2" in all_txt),
    ("以实验数据为准", "实验数据" in all_txt),
    ("自主决定", "自主" in all_txt),
    ("情景记忆定义", "情景" in all_txt),
    ("代价重要性", "代价" in all_txt or "cost" in all_txt.lower()),
    ("睡眠机制", "睡眠" in all_txt),
]
for name, ok in must_have:
    check(f"保留了「{name}」", ok)

# 6. 重复已合并
print("\n【6】重复项已合并")
dup_check = "遇到问题时先自己尝试解决"
cnt = all_txt.count(dup_check)
valid_dup = sum(1 for r in rows if dup_check in r[0])
check("该表述只剩 1 条有效", valid_dup <= 1, f"{valid_dup} 条")

# 7. 失效记录仍可查（R2）
print("\n【7】R2 不遗忘：失效记录仍在库")
inv = st.con.execute(
    "SELECT COUNT(*) FROM episodes WHERE invalid_at IS NOT NULL").fetchone()[0]
check("失效记录保留", inv > 0, f"{inv} 条")
sup = st.con.execute(
    "SELECT COUNT(*) FROM episodes WHERE superseded_by IS NOT NULL "
    "AND superseded_by != ''").fetchone()[0]
check("superseded_by 有记录", sup > 0, f"{sup} 条")

st.con.close()

print("\n" + "=" * 72)
n_ok = sum(1 for r in results if r["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for r in results:
    if not r["ok"]:
        print(f"  ❌ {r['name']}  {r['detail']}")
print("=" * 72)

sys.exit(0 if n_ok == len(results) else 1)
