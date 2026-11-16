"""验证种子记忆：内容正确、零隐私、保护生效

设计原则：不硬编码数量，全部从 seed_memory.json 动态读取。
（硬编码会让每次加种子都要改测试——这是维护负担，也是测试脆弱性来源）
"""
import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proto", "cosmos_mem"))
from store_v2 import MemoryStore
import hook as _hook
import seed as _seed

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proto", "cosmos_mem", "dsh_mem.db")
st = MemoryStore(DB)
results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 72)
print("种子记忆验证（数量动态读取，不硬编码）")
print("=" * 72)

# ---------- 从种子文件读期望值 ----------
spec = _seed.load_seed_file()
EXPECT_L0 = [x["title"] for x in spec.get("L0", [])]
EXPECT_L1 = [x["title"] for x in spec.get("L1", [])]
EXPECT_L2 = [x["title"] for x in spec.get("L2", [])]
EXPECT_ALL = EXPECT_L0 + EXPECT_L1 + EXPECT_L2
EXPECT_PRESENT = len(EXPECT_L0) + len(EXPECT_L1)   # L0+L1 强制在场

print(f"\n种子文件定义：L0={len(EXPECT_L0)} L1={len(EXPECT_L1)} "
      f"L2={len(EXPECT_L2)}，合计 {len(EXPECT_ALL)} 条")
print(f"强制在场应为：{EXPECT_PRESENT} 条（L0+L1）")

# ---------- 1. 数量与完整性 ----------
print("\n【1】数量与完整性")
seeds = _seed.seed_stats(st)
check(f"种子有效 {len(EXPECT_ALL)} 条", seeds["valid"] == len(EXPECT_ALL),
      f"{seeds['valid']}")

titles = _seed.seed_titles(st)
missing = [t for t in EXPECT_ALL if t not in titles]
check("所有种子标题都装上了", len(missing) == 0,
      f"缺失: {missing}" if missing else "全部存在")

# ---------- 2. 层级正确 ----------
print("\n【2】保护层级")
hs = _hook.stats(st)
check(f"L0 = {len(EXPECT_L0)}", hs["by_level"].get("L0", 0) == len(EXPECT_L0),
      f"{hs['by_level'].get('L0',0)}")
check(f"L1 = {len(EXPECT_L1)}", hs["by_level"].get("L1", 0) == len(EXPECT_L1),
      f"{hs['by_level'].get('L1',0)}")
check(f"L2 = {len(EXPECT_L2)}", hs["by_level"].get("L2", 0) == len(EXPECT_L2),
      f"{hs['by_level'].get('L2',0)}")
present = _hook.present_ids(st)
check(f"L0+L1 = {EXPECT_PRESENT} 强制在场", len(present) == EXPECT_PRESENT,
      f"{len(present)}")

# ---------- 3. 隐私扫描（只扫种子）----------
print("\n【3】隐私扫描（种子必须零隐私，可随代码分发）")
seed_ids = set(titles.values())
seed_texts = [txt for (eid, txt) in st.con.execute(
    "SELECT ep_id, raw_text FROM episodes WHERE invalid_at IS NULL").fetchall()
    if eid in seed_ids]
seed_txt = " ".join(seed_texts)

PRIVATE = [
    "27 届", "27届", "校招", "INTP", "西安", "天地核心", "J10586", "J10584",
    "求职", "简历", "书信箱", "预言", "Gemini", "科幻",
    "肥鱼之心", "xuee", "RTX 3060", "胖鱼", "考研", "候选人",
    "我电脑", "我换电脑", "我的车", "爷爷", "妈妈的名字",
]
leaks = [p for p in PRIVATE if p in seed_txt]
check("种子记忆无私人内容", len(leaks) == 0,
      f"发现: {leaks}" if leaks else "零泄露")

# ---------- 4. 关键内容覆盖 ----------
print("\n【4】关键内容覆盖（用户要求的三类）")
check("① 项目本身（系统自述）", "本系统是什么" in seed_txt)
check("② 记忆钩子（红线/准则）", "红线" in seed_txt and "职责划分" in seed_txt)
check("③ 通用能力（认知纪律）", "认知纪律" in seed_txt)
check("④ 记忆自主权（模型自管记忆）", "记忆自主权" in titles)
check("⑤ 何时该问人（提问边界）", "何时该问人" in titles)

# ---------- 5. R2 不遗忘 ----------
print("\n【5】R2 不遗忘")
stats = st.stats()
check("失效记录保留", stats["invalid"] > 100, f"{stats['invalid']} 条")

# ---------- 6. 保护生效 ----------
print("\n【6】保护生效")
l0 = [p for p in _hook.protected_of(st) if p["level"] == "L0"]
check("有 L0 记忆", len(l0) == len(EXPECT_L0))
if l0:
    eid = l0[0]["ep_id"]
    check("L0 拒绝 invalidate", st.invalidate(eid) is False)
    check("L0 拒绝 revise", st.revise(eid, new_text="改红线") is False)

# L1 也不可失效/合并
l1 = [p for p in _hook.protected_of(st) if p["level"] == "L1"]
if l1:
    eid = l1[0]["ep_id"]
    check("L1 拒绝 invalidate", st.invalidate(eid) is False)

# ---------- 7. 检索强制在场 ----------
print("\n【7】L0/L1 检索时强制在场")
res = st.search("完全不相关xyz123", top=3)
got = {ep.ep_id for ep, _, fl in res if fl == 2}
check(f"{EXPECT_PRESENT} 条全部在场", len(got) == EXPECT_PRESENT,
      f"{len(got)}/{EXPECT_PRESENT}")

st.con.close()

print("\n" + "=" * 72)
n_ok = sum(1 for r in results if r["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for r in results:
    if not r["ok"]:
        print(f"  ❌ {r['name']}  {r['detail']}")
print("=" * 72)
sys.exit(0 if n_ok == len(results) else 1)
