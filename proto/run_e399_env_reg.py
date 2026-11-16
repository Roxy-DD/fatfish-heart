"""E-399 环境事件模块的回归测试

E-398 建立了 environment.py（主张6 最小实现）。
本测试固化它的关键约束：

  ① 白名单：只接真实因果，拒绝一切主观标签
  ② 与记忆分数严格分离（不写进 episodes 的 cost/affect）
  ③ 建表幂等
  ④ 统计与查询可用
  ⑤ 能关联到具体记忆
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
import cli_mcp

DB = os.path.join(MEM, "dsh_mem.db")
st = MemoryStore(DB)

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("E-399 环境事件模块回归")
print("=" * 74)

# ---------- ① 建表幂等 ----------
print("\n【1】建表幂等")

r1 = env.init(st)
r2 = env.init(st)
check("init 可重复调用", r1.get("ok") and r2.get("ok"))

# ---------- ② 白名单 ----------
print("\n【2】白名单（拒绝主观标签）")

SUBJECTIVE = [
    "user_liked", "user_said_good", "i_think_useful",
    "feels_right", "importance_high", "score_8",
]
rejected = 0
for k in SUBJECTIVE:
    r = env.record(st, k, "test")
    if not r.get("ok"):
        rejected += 1
check(f"拒绝全部 {len(SUBJECTIVE)} 个主观标签",
      rejected == len(SUBJECTIVE), f"{rejected}/{len(SUBJECTIVE)}")

# 且错误信息里列出了白名单
r_bad = env.record(st, "user_liked")
has_allowed = "allowed" in r_bad
check("拒绝时给出白名单", has_allowed)

# 白名单内都接受
accepted = 0
TEST_KINDS = ["exec_ok", "exec_fail", "test_pass", "test_fail",
              "file_exists", "file_missing", "http_ok", "http_error"]
for k in TEST_KINDS:
    r = env.record(st, k, "__e399_test__", "test")
    if r.get("ok"):
        accepted += 1
check(f"白名单内 {len(TEST_KINDS)} 类都接受",
      accepted == len(TEST_KINDS), f"{accepted}/{len(TEST_KINDS)}")

# ---------- ③ 与记忆分数分离 ----------
print("\n【3】与记忆分数严格分离")

# 记录一条环境事件后，episodes 的 cost/affect 不应变化
row = st.con.execute(
    "SELECT ep_id, cost, affect_peak FROM episodes "
    "WHERE invalid_at IS NULL LIMIT 1").fetchone()

if row:
    eid, c0, a0 = row
    env.record(st, "exec_ok", "__e399_test__", "exit=0", ep_ids=[eid])
    row2 = st.con.execute(
        "SELECT cost, affect_peak FROM episodes WHERE ep_id=?",
        (eid,)).fetchone()
    check("环境事件不改变 cost", row2[0] == c0, f"{c0} → {row2[0]}")
    check("环境事件不改变 affect_peak", row2[1] == a0, f"{a0} → {row2[1]}")

    # 但能关联查询
    evs = env.events_for_episode(st, eid)
    check("能查到该记忆关联的环境事件", len(evs) > 0, f"{len(evs)} 条")

# ---------- ④ 统计 ----------
print("\n【4】统计")

s = env.stats(st)
check("stats 有 total", "total" in s, str(s.get("total")))
check("stats 有 success_rate",
      "success_rate" in s, str(s.get("success_rate")))
print(f"   当前: total={s.get('total')}, "
      f"ok={s.get('ok')}, fail={s.get('fail')}, "
      f"rate={s.get('success_rate')}")

# ---------- ⑤ CLI 路由 ----------
print("\n【5】cmd_environment 路由")

actions = ["record", "recent", "stats", "for_episode"]
for a in actions:
    payload = {"action": a}
    if a == "record":
        payload.update({"kind": "exec_ok", "source": "__e399_test__"})
    elif a == "for_episode":
        payload["ep_id"] = "nonexistent"
    r = cli_mcp.cmd_environment(st, payload)
    check(f"action={a} 可用", r.get("ok") is True,
          str(r.get("error") or "ok"))

# 未知 action
r = cli_mcp.cmd_environment(st, {"action": "delete_all"})
check("未知 action 被拒绝", not r.get("ok"))

# ---------- ⑥ 清理测试数据 ----------
print("\n【6】清理测试数据")
before = st.con.execute(
    "SELECT COUNT(*) FROM environment_events").fetchone()[0]
st.con.execute(
    "DELETE FROM environment_events WHERE source LIKE '__e399_test__%'"
    " OR source='test'")
st.con.commit()
after = st.con.execute(
    "SELECT COUNT(*) FROM environment_events").fetchone()[0]
print(f"   清理前 {before} → 清理后 {after}")

# ---------- 汇总 ----------
print("\n" + "=" * 74)
n_ok = sum(1 for x in results if x["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for x in results:
    if not x["ok"]:
        print(f"  ❌ {x['name']}  {x['detail']}")
print("=" * 74)

print(f"""
  ★ 这个模块守住了一条边界：
    《环管群》5.2「内部奖励不能冒充自然环境的因果筛选」

    白名单机制强制区分：
      ✅ 客观因果（退出码、状态码、文件存在）
      ❌ 主观标签（用户喜欢、我觉得有用）

  ★ 还没做的（诚实）：
    · 环境事件不影响记忆存续（故意的，样本太少）
    · 没有「环境被改变」的记录（主张6 后半）
""")

out = {
    "experiment": "E-399",
    "n_pass": n_ok,
    "n_total": len(results),
    "event_kinds": len(env.EVENT_KINDS),
    "stats": s,
}
p = os.path.join(_HERE, "results", "e399_env_regression.json")
if os.environ.get("FFH_NO_WRITE") == "1":
    print("（FFH_NO_WRITE=1，跳过写盘）")
else:
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e399_env_regression.json")

st.con.close()
sys.exit(0 if n_ok == len(results) else 1)
