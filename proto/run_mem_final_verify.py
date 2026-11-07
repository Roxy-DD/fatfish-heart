"""最终验证：13 条底层记忆 + 保护生效 + 检索可用"""
import sys
import json

sys.path.insert(0, r"C:\Users\xuee\Desktop\肥鱼之心\proto\cosmos_mem")
from store_v2 import MemoryStore
import hook as _hook

DB = r"C:\Users\xuee\Desktop\肥鱼之心\proto\cosmos_mem\dsh_mem.db"
st = MemoryStore(DB)
results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 70)
print("最终验证：13 条底层记忆")
print("=" * 70)

stats = st.stats()
check("有效记忆 = 13 条", stats["valid"] == 13, f"{stats['valid']}")

# 保护
hs = _hook.stats(st)
check("L0 = 1", hs["by_level"].get("L0", 0) == 1)
check("L1 = 5", hs["by_level"].get("L1", 0) == 5)
check("L2 = 4", hs["by_level"].get("L2", 0) == 4)

# 强制在场
present = _hook.present_ids(st)
check("L0+L1 共 6 条强制在场", len(present) == 6, f"{len(present)}")

# 检索
res = st.search("完全不相关xyz", top=3)
got = {ep.ep_id for ep, _, fl in res if fl == 2}
check("L0/L1 检索时全部在场", present.issubset(got), f"{len(got)}/{len(present)}")

# 保护生效
l0 = [p for p in _hook.protected_of(st) if p["level"] == "L0"][0]["ep_id"]
check("L0 拒绝 invalidate", st.invalidate(l0) is False)
check("L0 拒绝 revise", st.revise(l0, new_text="改红线") is False)

# R2：失效记录保留
check("失效记录保留（R2）", stats["invalid"] > 100, f"{stats['invalid']} 条")

# 内容完整性
rows = st.con.execute(
    "SELECT raw_text FROM episodes WHERE invalid_at IS NULL").fetchall()
alltxt = " ".join(r[0] for r in rows)
for k in ["红线", "实验数据", "情景", "代价", "睡眠", "自主", "USER画像".replace("USER", "用户")]:
    check(f"包含「{k}」", k in alltxt)

st.con.close()

print("\n" + "=" * 70)
n_ok = sum(1 for r in results if r["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for r in results:
    if not r["ok"]:
        print(f"  ❌ {r['name']}  {r['detail']}")
print("=" * 70)
sys.exit(0 if n_ok == len(results) else 1)
