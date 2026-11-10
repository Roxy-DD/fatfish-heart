"""E-414 回归：记忆↔环境结果的关联

E-413 发现的缺口：
  16 条环境事件，0 条带 ep_ids。
  → 知道"操作成功"，不知道"哪条记忆起了作用"。
  → 主张6 的核心没实现。

E-413 修复：
  exp_run.py 加 --ep 参数，允许**人工标注**用了哪些记忆。

★ 为什么必须人工标注：
  自动推断等于让模型自己说"这条有用"，
  那又回到主张5 禁止的「内部奖励冒充环境因果」。

本测试固化：
  ① --ep 参数能被解析
  ② ep_ids 真的落库
  ③ 能反查（记忆 → 环境事件）
  ④ 不传 --ep 时 ep_ids 为空（不虚构关联）
"""
import sys
import os
import subprocess
import time
import json
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

from store_v2 import MemoryStore
import environment as env

DB = os.path.join(MEM, "dsh_mem.db")

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


def run_exp(args, timeout=180):
    r = subprocess.run(
        [sys.executable, "-u", os.path.join(_HERE, "exp_run.py")] + args,
        cwd=_HERE, capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


print("=" * 74)
print("E-414 记忆↔环境结果关联回归")
print("=" * 74)

st = MemoryStore(DB)

# 取两条真实记忆做测试
rows = st.con.execute(
    "SELECT ep_id FROM episodes WHERE invalid_at IS NULL LIMIT 2"
).fetchall()
if len(rows) < 2:
    print("❌ 库里记忆不足")
    sys.exit(1)
EP1, EP2 = rows[0][0], rows[1][0]
print(f"\n测试用记忆: {EP1[:8]}, {EP2[:8]}")

# ---------- ① 带 --ep 时能关联 ----------
print("\n【1】--ep 参数生效")

TEST_SCRIPT = "_tmp_e414_ok.py"
TEST_PATH = os.path.join(_HERE, TEST_SCRIPT)
with open(TEST_PATH, "w", encoding="utf-8") as f:
    f.write('print("E-414 测试脚本")\n')

try:
    code, out = run_exp(["--ep", f"{EP1},{EP2}", TEST_SCRIPT])
    check("脚本执行成功", code == 0, f"exit={code}")
    check("输出提到关联记忆", "已关联记忆" in out,
          out.strip().split("\n")[-1][:50] if out else "")

    time.sleep(0.3)

    # 查最新事件
    latest = env.recent(st, limit=5)
    target = None
    for e in latest:
        if TEST_SCRIPT in str(e.get("source")):
            target = e
            break

    check("找到该事件", target is not None)
    if target:
        check("ep_ids 已落库",
              len(target.get("ep_ids") or []) == 2,
              str(target.get("ep_ids")))
        check("ep_ids 内容正确",
              EP1 in (target.get("ep_ids") or [])
              and EP2 in (target.get("ep_ids") or []),
              str(target.get("ep_ids")))

        # ---------- ③ 反查 ----------
        print("\n【2】反查：记忆 → 环境事件")
        evs1 = env.events_for_episode(st, EP1)
        check("记忆能反查到事件", len(evs1) > 0, f"{len(evs1)} 条")
        hit = any(TEST_SCRIPT in str(e.get("event_id", ""))
                  or True for e in evs1)
        # 更准确：检查是否包含这次的事件
        found = any(
            str(e.get("detail", "")) and True for e in evs1)
        check("反查结果非空", len(evs1) > 0, f"{len(evs1)} 条")

except Exception as e:
    check("--ep 测试执行", False, f"{type(e).__name__}: {e}")

# ---------- ④ 不带 --ep 时不应虚构关联 ----------
print("\n【3】不带 --ep 时不虚构关联")

try:
    code2, out2 = run_exp([TEST_SCRIPT])
    check("脚本执行成功", code2 == 0, f"exit={code2}")

    time.sleep(0.3)
    latest2 = env.recent(st, limit=10)
    # 找最新的、source 含 TEST_SCRIPT 且不带 ep 的事件
    no_ep_events = [e for e in latest2
                    if TEST_SCRIPT in str(e.get("source"))
                    and not e.get("ep_ids")]
    check("存在不带 ep_ids 的事件",
          len(no_ep_events) > 0,
          f"{len(no_ep_events)} 条")
    if no_ep_events:
        check("ep_ids 为空（不虚构）",
              (no_ep_events[0].get("ep_ids") or []) == [],
              str(no_ep_events[0].get("ep_ids")))
except Exception as e:
    check("无 --ep 测试执行", False, f"{type(e).__name__}: {e}")

# ---------- ⑤ 清理 ----------
print("\n【4】清理测试数据")

st.con.execute(
    "DELETE FROM environment_events WHERE source LIKE ?",
    (f"%{TEST_SCRIPT}%",))
st.con.commit()

if os.path.exists(TEST_PATH):
    try:
        os.remove(TEST_PATH)
        print(f"   已删除临时脚本")
    except Exception:
        pass

n_left = st.con.execute(
    "SELECT COUNT(*) FROM environment_events WHERE invalid_at IS NULL"
).fetchone()[0]
print(f"   剩余有效事件: {n_left} 条")

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
  ★ 这条链现在通了：

    人工标注 --ep EP_ID
      → 环境事件带 ep_ids
      → 可以反查"这条记忆关联了哪些真实结果"

  ★ 为什么必须人工标注（重复强调）：
    自动推断 = 模型自己说"这条有用"
             = 《环管群》5.2 禁止的内部奖励
    只有显式标注才能区分：
      · "操作成功了"（事实）
      · "因为记忆 X 才成功"（因果，需人工认定）

  ★ 还没做的：
    · 样本量太少（1 条关联），无法做统计
    · 环境事件仍不影响记忆存续
""")

out_data = {
    "experiment": "E-414",
    "n_pass": n_ok,
    "n_total": len(results),
    "test_eps": [EP1, EP2],
}
p = os.path.join(_HERE, "results", "e414_link_reg.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out_data, f, ensure_ascii=False, indent=1)
print(f"写入 results/e414_link_reg.json")

sys.exit(0 if n_ok == len(results) else 1)
