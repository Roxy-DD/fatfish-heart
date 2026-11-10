"""E-413 关键验证：环境事件现在能支撑什么判断？

主张6 的目的不是"记录数据"，而是「让结构在环境中接受因果检验」。
判据应该是：**这些数据能不能回答"哪条记忆/哪个改动真的有效"？**

如果答案是"不能"，那这个模块就只是日志，不是筛选。

本实验用现有 10 条真实事件，尝试回答三个具体问题：
  ① 最近一周我的操作成功率是多少？
  ② 哪类错误反复出现？
  ③ 能否把某条记忆与"它带来的真实结果"关联起来？

第 ③ 个最关键 —— 如果做不到，主张6 的核心就没实现。
"""
import sys
import os
import json
import time
import warnings
from collections import Counter, defaultdict

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

from store_v2 import MemoryStore
import environment as env

DB = os.path.join(MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 78)
print("E-413 环境事件能支撑什么判断")
print("=" * 78)

# ---------- ① 成功率 ----------
print("\n① 问题1：操作成功率是多少")

s = env.stats(st)
print(f"   总事件: {s.get('total')}")
print(f"   成功 {s.get('ok')} / 失败 {s.get('fail')}")
print(f"   成功率: {s.get('success_rate')}")

# 但要看样本量是否足够
n = s.get("total", 0)
print(f"\n   样本量评估: {n} 条")
if n < 30:
    print(f"   ⚠ 样本太少（<30），成功率的置信区间很宽")
    print(f"      按二项分布，{n} 条样本的 95% CI 约 ±"
          f"{1.96 * (0.5 / max(1, n ** 0.5)) * 100:.0f}pp")
else:
    print(f"   样本量可接受")

# ---------- ② 错误类型分布 ----------
print("\n② 问题2：哪类错误反复出现")

events = env.recent(st, limit=100)
fails = [e for e in events if e["kind"] in ("exec_fail", "test_fail")]
print(f"   失败事件: {len(fails)} 条")

if fails:
    print("\n   失败详情:")
    for e in fails:
        t = time.strftime("%m-%d %H:%M", time.localtime(e["at"]))
        print(f"     {t} {e['kind']} | {str(e['source'])[:35]}")
        print(f"        报错: {str(e['detail'])[:70]}")
else:
    print("   （无失败事件）")

# ---------- ③ 关键：能否关联记忆与结果 ----------
print("\n③ 问题3（最关键）：能否把记忆与真实结果关联")

# 检查有多少环境事件带了 ep_ids
with_ep = [e for e in events if e.get("ep_ids")]
print(f"   带 ep_ids 的事件: {len(with_ep)}/{len(events)}")

if not with_ep:
    print("""
   ❌ **完全不能关联**

   所有环境事件都没有 ep_ids —— 也就是说：
     · 知道"某次执行成功了"
     · 但不知道"哪条记忆导致/参与了这个成功"

   这意味着主张6 的核心还没实现：
     环境筛选要求能判断"某个结构是否真的起作用"，
     而现在只能判断"某次操作是否成功"，
     两者之间没有连接。
""")
else:
    print("   可按 ep_id 反查关联事件")

# ---------- ④ 补充检查：记忆被检索 vs 环境结果 ----------
print("\n④ 补充：记忆的检索次数 vs 环境结果")

# channel 表里的访问次数
ch_rows = st.con.execute(
    "SELECT key, val FROM meta WHERE key LIKE 'channel:%'").fetchall()
print(f"   有访问记录的记忆: {len(ch_rows)} 条")

if ch_rows:
    # 取访问最多的几条
    items = []
    for k, v in ch_rows:
        try:
            d = json.loads(v)
            total = sum(int(x or 0) for x in d.values())
            items.append((k.replace("channel:", ""), total))
        except Exception:
            pass
    items.sort(key=lambda x: -x[1])

    print(f"\n   访问最多的 5 条记忆:")
    for eid, n_visit in items[:5]:
        # 查这条记忆关联的环境事件
        evs = env.events_for_episode(st, eid)
        row = st.con.execute(
            "SELECT substr(raw_text, 1, 40) FROM episodes "
            "WHERE ep_id=?", (eid,)).fetchone()
        txt = row[0] if row else "?"
        print(f"     [{eid[:8]}] 访问 {n_visit} 次, "
              f"关联环境事件 {len(evs)} 条")
        print(f"        {txt}")

# ---------- 结论 ----------
print("\n" + "=" * 78)
print("结论")
print("=" * 78)

print(f"""
  问题1 成功率: ✅ 能回答（{s.get('success_rate')}）
     但样本 {n} 条太少，置信区间宽。

  问题2 错误分布: ✅ 能回答
     {len(fails)} 条失败记录，带具体报错。

  问题3 记忆↔结果关联: {'❌ 不能' if not with_ep else '✅ 能'}
     {len(with_ep)}/{len(events)} 条事件带 ep_ids。

  ★ 诚实判断：

    当前环境事件记录的是「操作是否成功」，
    而不是「哪条记忆是否有效」。

    这两件事差得很远：
      · 前者是操作日志
      · 后者才是主张6 要的"环境筛选"

   ★ 要补上的缺口：
    记录环境事件时，应该带上**当时用了哪些记忆**。
    比如跑一个实验时，如果用了某条记忆的结论，
    就把那条记忆的 id 记进 ep_ids。

    但这需要**人工标注**（我知道自己用了哪条记忆），
    不能自动推断 —— 自动推断等于让模型自己说"这条有用"，
    那又回到主张5 禁止的"内部奖励冒充环境因果"。
""")

st.con.close()

out = {
    "experiment": "E-413",
    "n_events": n,
    "success_rate": s.get("success_rate"),
    "n_fails": len(fails),
    "events_with_ep_ids": len(with_ep),
    "can_link_memory_to_outcome": len(with_ep) > 0,
    "verdict": ("只能回答操作成功率，不能回答记忆有效性"
                if not with_ep else "可关联"),
}
p = os.path.join(_HERE, "results", "e413_what_can_answer.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e413_what_can_answer.json")
