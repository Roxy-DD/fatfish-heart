"""E-417 回归：MESH_EP 让回归自动关联记忆

E-413 建的 --ep 只覆盖 exp_run，回归（iterate.py check）没有。
本测试固化：

  ① 设 MESH_EP 后，回归记录带 ep_ids
  ② 不设 MESH_EP 时，ep_ids 为空（不虚构）
  ③ 仍然要求显式标注（不做自动推断）

★ 为什么环境变量而不是自动推断：
  自动推断 = 模型自己说"这条有用"，违反《环管群》5.2。
  环境变量只是让标注更方便，不改变"必须显式标注"的性质。
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
import iterate

DB = os.path.join(MEM, "dsh_mem.db")
ITER = os.path.join(_HERE, "iterate.py")

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("E-417 MESH_EP 关联回归")
print("=" * 74)

src = open(ITER, encoding="utf-8").read()

# ---------- ① 代码检查 ----------
print("\n【1】代码支持")

check("iterate.py 读 MESH_EP", 'os.environ.get("MESH_EP"' in src)
check("解析为列表", 'split(",")' in src)
check("传给 env.record", "ep_ids=ep_ids" in src)
check("注释说明仍要人工标注", "人工标注" in src
      or "显式标注" in src)

# ---------- ② 端到端验证 ----------
print("\n【2】端到端：MESH_EP 生效")

st = MemoryStore(DB)

# 取两条真实记忆
rows = st.con.execute(
    "SELECT ep_id FROM episodes WHERE invalid_at IS NULL LIMIT 2"
).fetchall()
if len(rows) < 2:
    print("❌ 记忆不足")
    sys.exit(1)
EP1, EP2 = rows[0][0], rows[1][0]

# 记录当前最新 regression 事件
before = st.con.execute(
    "SELECT COUNT(*) FROM environment_events "
    "WHERE task='regression'").fetchone()[0]

# 直接调 iterate 的内部函数（避免跑全量回归太慢）
fake_results = {"a": {"pass": True}, "b": {"pass": True}}

# 场景 1：设 MESH_EP
os.environ["MESH_EP"] = f"{EP1},{EP2}"
iterate._record_regression_event(fake_results, True)
del os.environ["MESH_EP"]

# 场景 2：不设 MESH_EP
iterate._record_regression_event(fake_results, True)

after = st.con.execute(
    "SELECT COUNT(*) FROM environment_events "
    "WHERE task='regression'").fetchone()[0]
check("新增了 2 条 regression 事件", after - before == 2,
      f"{before} → {after}")

# 取这两条
evs = st.con.execute(
    "SELECT event_id, ep_ids FROM environment_events "
    "WHERE task='regression' ORDER BY at DESC LIMIT 2").fetchall()

if len(evs) == 2:
    # 最新的是无 MESH_EP 的
    latest_epids = json.loads(evs[0][1] or "[]")
    prev_epids = json.loads(evs[1][1] or "[]")

    check("不带 MESH_EP 时 ep_ids 为空（不虚构）",
          latest_epids == [], str(latest_epids))
    check("带 MESH_EP 时 ep_ids 有 2 条",
          len(prev_epids) == 2, str(prev_epids))
    check("ep_ids 内容正确",
          EP1 in prev_epids and EP2 in prev_epids, str(prev_epids))

# ---------- ③ 反查 ----------
print("\n【3】反查：记忆 → 回归结果")

evs_for_ep = env.events_for_episode(st, EP1)
check("能反查到事件", len(evs_for_ep) > 0, f"{len(evs_for_ep)} 条")

# ---------- ④ 清理 ----------
print("\n【4】清理测试数据")

st.con.execute(
    "DELETE FROM environment_events WHERE task='regression' "
    "AND event_id IN (?, ?)", (evs[0][0], evs[1][0]) if len(evs) == 2
    else ("", ""))
st.con.commit()
n = st.con.execute(
    "SELECT COUNT(*) FROM environment_events").fetchone()[0]
print(f"   剩余事件: {n} 条")

st.con.close()

# ---------- 汇总 ----------
print("\n" + "=" * 74)
n_ok = sum(1 for x in results if x["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for x in results:
    if not x["ok"]:
        print(f"  ❌ {x['name']}  {x['detail']}")
print("=" * 74)

out = {
    "experiment": "E-417",
    "n_pass": n_ok,
    "n_total": len(results),
    "test_eps": [EP1, EP2],
}
p = os.path.join(_HERE, "results", "e417_mesh_ep.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e417_mesh_ep.json")

sys.exit(0 if n_ok == len(results) else 1)
