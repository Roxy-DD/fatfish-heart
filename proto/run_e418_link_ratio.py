"""E-418 回归：关联覆盖率指标

E-413/417 建了关联能力（--ep / MESH_EP），但**没有指标衡量用了多少**。
没有指标就无法判断主张6 是否真的在推进。

本测试固化：
  ① stats 返回 linked 和 link_ratio
  ② 覆盖率计算正确（与手工统计一致）
  ③ 指标能反映真实状态
"""
import sys
import os
import json
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

from store_v2 import MemoryStore
import environment as env

DB = os.path.join(MEM, "dsh_mem.db")
st = MemoryStore(DB)

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("E-418 关联覆盖率指标回归")
print("=" * 74)

# ---------- ① 指标存在 ----------
print("\n【1】指标存在")

s = env.stats(st)
check("stats 有 linked 字段", "linked" in s, str(s.get("linked")))
check("stats 有 link_ratio 字段", "link_ratio" in s,
      str(s.get("link_ratio")))
check("link_ratio 在 [0,1] 区间",
      s.get("link_ratio") is None
      or 0 <= s.get("link_ratio") <= 1,
      str(s.get("link_ratio")))

# ---------- ② 与手工统计一致 ----------
print("\n【2】与手工统计一致")

total = s.get("total", 0)
linked_reported = s.get("linked", 0)

# 手工查
manual_linked = st.con.execute(
    "SELECT COUNT(*) FROM environment_events "
    "WHERE invalid_at IS NULL "
    "AND ep_ids IS NOT NULL AND ep_ids != '[]' "
    "AND ep_ids != ''").fetchone()[0]

check("linked 与手工统计一致",
      linked_reported == manual_linked,
      f"报告 {linked_reported} vs 手工 {manual_linked}")

manual_total = st.con.execute(
    "SELECT COUNT(*) FROM environment_events "
    "WHERE invalid_at IS NULL").fetchone()[0]
check("total 与手工统计一致",
      total == manual_total, f"{total} vs {manual_total}")

# 比率计算
expected_ratio = round(manual_linked / manual_total, 3) \
    if manual_total > 0 else None
check("link_ratio 计算正确",
      s.get("link_ratio") == expected_ratio,
      f"{s.get('link_ratio')} vs {expected_ratio}")

# ---------- ③ 指标反映真实状态 ----------
print("\n【3】指标反映真实状态")

print(f"   当前: {linked_reported}/{total} = "
      f"{(s.get('link_ratio') or 0)*100:.0f}%")

if (s.get("link_ratio") or 0) < 0.3:
    print(f"""
   ⚠ 关联覆盖率偏低（{(s.get('link_ratio') or 0)*100:.0f}% < 30%）

   含义：大部分环境事件不知道对应哪条记忆。
         → 只能回答"操作成功与否"
         → 不能回答"哪条记忆扛住了检验"

   这是主张6 尚未完成的量化证据。
""")
else:
    print(f"   覆盖率可接受")

# 不强制要求高覆盖率（样本太少，强求会导致虚假标注）
check("指标能如实反映（不虚报）",
      linked_reported <= total, f"{linked_reported} <= {total}")

# ---------- ④ 边界情况 ----------
print("\n【4】边界情况")

# 空 ep_ids 不应计入
empty_cnt = st.con.execute(
    "SELECT COUNT(*) FROM environment_events "
    "WHERE invalid_at IS NULL AND (ep_ids = '[]' OR ep_ids = '')"
).fetchone()[0]
print(f"   ep_ids 为空的事件: {empty_cnt} 条（不应计入 linked）")

check("空 ep_ids 不计入 linked",
      linked_reported + empty_cnt <= total,
      f"{linked_reported} + {empty_cnt} <= {total}")

st.con.close()

# ---------- 汇总 ----------
print("\n" + "=" * 74)
n_ok = sum(1 for x in results if x["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for x in results:
    if not x["ok"]:
        print(f"  ❌ {x['name']}  {x['detail']}")
print("=" * 74)

print(f"""
  ★ 这个指标的意义：

    link_ratio 是主张6 的**进度条**。
    它低说明我只有操作日志，没有真正的环境筛选。
    它高说明能回答"哪条记忆真的扛住了检验"。

    当前 {linked_reported}/{total} = {(s.get('link_ratio') or 0)*100:.0f}%
    —— 诚实地低。提高它需要我在每次验证时
      真的想清楚"这条结论依赖了哪条记忆"，而不是乱填。
""")

out = {
    "experiment": "E-418",
    "n_pass": n_ok,
    "n_total": len(results),
    "total": total,
    "linked": linked_reported,
    "link_ratio": s.get("link_ratio"),
}
p = os.path.join(_HERE, "results", "e418_link_ratio.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e418_link_ratio.json")

sys.exit(0 if n_ok == len(results) else 1)
