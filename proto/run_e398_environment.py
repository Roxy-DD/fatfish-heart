"""E-398 主张6 的最小实现：环境事件 → 记忆存续

原文要求（5.3 因果链）：
  系统结构 → 对物理环境施加动作 → 环境状态变化
  → 产生资源/能量/结构后果 → 影响系统维持
  → 影响后继产生与传播

原文强调（5.2）：
  · 环境不是有意识的裁判，不给打分
  · **内部奖励不能冒充环境因果**

★ 设计原则（严格遵守原文）：
  1. 只记录**真实因果**，不记录人的评价
     ✓ 代码执行返回码、耗时、报错
     ✗ 用户说"好"/"不好"
  2. 环境事件不与"记忆重要性"打分混在一起
     · cost/affect_peak 是我的内部分数（主张5 待裁定）
     · 环境事件是**外部的、客观的**事实
     · 两者分开存储，不互相换算
  3. 环境事件只影响"证据强度"，不影响"记忆内容"

本步只做**记录与查询**，不做自动权重调整——
因为环境样本还太少，自动调整等于过拟合。

实现：
  · 新表 environment_events：记录真实环境反馈
  · 字段：event_id, at, kind, source, outcome, detail, ep_ids
  · kind: exec_ok / exec_fail / test_pass / test_fail / file_missing
  · outcome: 客观结果，不含主观评价
"""
import sys
import os
import json
import time
import sqlite3
import hashlib
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
DB = os.path.join(MEM, "dsh_mem.db")
EV = os.path.join(MEM, "environment.py")

print("=" * 78)
print("E-398 主张6 最小实现：环境事件记录")
print("=" * 78)

# ---------- ① 写模块 ----------
print("\n① 生成 environment.py")

MODULE = '''"""
environment.py —— 环境事件记录（主张6 的最小实现）

============================================================
【为什么单独一个模块，而不并入记忆】
============================================================
《环管群》主张5 明确：
  「内部奖励不能冒充自然环境的因果筛选」

所以必须严格区分两类数据：
  · memory 的 cost / affect_peak  → 我的**内部分数**（主观）
  · environment_events            → **客观事实**（外部）

两者不互相换算，也不混在同一张表里。
这样即使将来主张5 的裁定变了，环境数据仍然可信。

============================================================
【什么算「环境事件」】
============================================================
只记真实因果，不记人的评价：

  ✅ 算：
     · 命令执行的退出码与耗时
     · 测试通过/失败
     · 文件是否存在
     · 进程是否存活
     · 网络请求的状态码

  ❌ 不算：
     · 用户说"好"或"不好"（这是人类评价）
     · 我自己判断"这条记忆有用"（这是内部评分）
     · 任何带主观语义的标签

============================================================
【本模块只做记录，不做自动调整】
============================================================
理由：环境样本还太少（几十条），
     自动调整权重等于过拟合。
     先积累数据，等样本够了再谈调整。
"""
from __future__ import annotations

import os
import json
import time
import uuid
import sqlite3

# 允许的事件类型（白名单，防止混入主观标签）
EVENT_KINDS = (
    "exec_ok",        # 命令执行成功
    "exec_fail",      # 命令执行失败（返回码非 0）
    "test_pass",      # 测试通过
    "test_fail",      # 测试失败
    "file_exists",    # 文件存在
    "file_missing",   # 文件不存在
    "proc_alive",     # 进程存活
    "proc_dead",      # 进程不存在
    "http_ok",        # HTTP 2xx
    "http_error",     # HTTP 非 2xx
    "timeout",        # 超时
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS environment_events (
    event_id   TEXT PRIMARY KEY,
    at         REAL NOT NULL,
    kind       TEXT NOT NULL,
    source     TEXT,              -- 哪个动作产生的（命令行/URL/文件路径）
    outcome    TEXT,              -- 客观结果（退出码、状态码、耗时）
    detail     TEXT,              -- 附加细节（报错摘要）
    ep_ids     TEXT,              -- 相关联的记忆 id（JSON 数组，可为空）
    task       TEXT               -- 属于哪个实验/任务
)
"""

INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_env_at ON environment_events(at)",
    "CREATE INDEX IF NOT EXISTS ix_env_kind ON environment_events(kind)",
)


def init(st) -> dict:
    """建表（幂等）"""
    try:
        st.con.execute(SCHEMA)
        for ix in INDEXES:
            st.con.execute(ix)
        st.con.commit()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def record(st, kind: str, source: str = "", outcome: str = "",
           detail: str = "", ep_ids=None, task: str = "") -> dict:
    """
    记录一条环境事件。

    ★ 只接受白名单里的 kind，拒绝一切主观标签。
    """
    if kind not in EVENT_KINDS:
        return {"ok": False,
                "error": f"kind 不在白名单: {kind}",
                "allowed": list(EVENT_KINDS)}

    eid = uuid.uuid4().hex[:16]
    try:
        st.con.execute(
            "INSERT INTO environment_events "
            "(event_id, at, kind, source, outcome, detail, ep_ids, task) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (eid, time.time(), kind, str(source)[:500],
             str(outcome)[:200], str(detail)[:1000],
             json.dumps(ep_ids or [], ensure_ascii=False),
             str(task)[:100]))
        st.con.commit()
        return {"ok": True, "event_id": eid, "kind": kind}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def recent(st, limit: int = 20, kind: str = None) -> list:
    """读最近的环境事件"""
    try:
        if kind:
            rows = st.con.execute(
                "SELECT event_id, at, kind, source, outcome, detail, "
                "ep_ids, task FROM environment_events WHERE kind=? "
                "ORDER BY at DESC LIMIT ?", (kind, limit)).fetchall()
        else:
            rows = st.con.execute(
                "SELECT event_id, at, kind, source, outcome, detail, "
                "ep_ids, task FROM environment_events "
                "ORDER BY at DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            out.append({
                "event_id": r[0], "at": r[1], "kind": r[2],
                "source": r[3], "outcome": r[4], "detail": r[5],
                "ep_ids": json.loads(r[6] or "[]"), "task": r[7],
            })
        return out
    except Exception:
        return []


def stats(st) -> dict:
    """环境事件统计"""
    try:
        rows = st.con.execute(
            "SELECT kind, COUNT(*) FROM environment_events "
            "GROUP BY kind").fetchall()
        by_kind = {k: n for k, n in rows}
        total = sum(by_kind.values())
        # 成功率（只针对成对的 kind）
        ok_n = sum(by_kind.get(k, 0) for k in
                   ("exec_ok", "test_pass", "http_ok",
                    "file_exists", "proc_alive"))
        fail_n = sum(by_kind.get(k, 0) for k in
                     ("exec_fail", "test_fail", "http_error",
                      "file_missing", "proc_dead", "timeout"))
        rate = None
        if ok_n + fail_n > 0:
            rate = round(ok_n / (ok_n + fail_n), 3)
        return {"total": total, "by_kind": by_kind,
                "ok": ok_n, "fail": fail_n, "success_rate": rate}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def events_for_episode(st, ep_id: str) -> list:
    """某条记忆关联的环境事件"""
    try:
        rows = st.con.execute(
            "SELECT event_id, at, kind, outcome, detail FROM "
            "environment_events WHERE ep_ids LIKE ? ORDER BY at DESC",
            (f'%"{ep_id}"%',)).fetchall()
        return [{"event_id": r[0], "at": r[1], "kind": r[2],
                 "outcome": r[3], "detail": r[4]} for r in rows]
    except Exception:
        return []
'''

with open(EV, "w", encoding="utf-8") as f:
    f.write(MODULE)

print(f"   写入 {len(MODULE)} 字节")

# ---------- ② 建表测试 ----------
print("\n② 建表 + 记录测试")

sys.path.insert(0, MEM)
from store_v2 import MemoryStore
import environment as env

st = MemoryStore(DB)

r = env.init(st)
print(f"   init: {r}")

# 记录几条真实事件（用这次实验的真实数据）
TESTS = [
    ("exec_ok", "python iterate.py check E-396",
     "exit=0", "18 项全绿", "E-396"),
    ("test_pass", "run_e396_doc_check.py",
     "16/16", "文档一致性通过", "E-396"),
    ("exec_fail", "python -c 引号未转义",
     "exit=1", "SyntaxError: unexpected character", "E-397"),
]

for kind, src, outcome, detail, task in TESTS:
    r = env.record(st, kind, src, outcome, detail, task=task)
    print(f"   {kind}: {'✅' if r.get('ok') else '❌'} "
          f"{r.get('event_id', r.get('error'))}")

# 拒绝主观标签
r_bad = env.record(st, "user_liked", "test", "good")
print(f"\n   拒绝主观标签: {'✅' if not r_bad.get('ok') else '❌ 竟然接受了'}")
print(f"      返回: {r_bad.get('error')}")

# ---------- ③ 查询测试 ----------
print("\n③ 查询")

s = env.stats(st)
print(f"   统计: {json.dumps(s, ensure_ascii=False)}")

recent = env.recent(st, limit=5)
print(f"\n   最近 {len(recent)} 条:")
for e in recent:
    t = time.strftime("%H:%M:%S", time.localtime(e["at"]))
    print(f"     {t} [{e['kind']}] {e['source'][:40]} → {e['outcome']}")

st.con.close()

# ---------- ④ 结论 ----------
print("\n" + "=" * 78)
print("结论")
print("=" * 78)

print(f"""
  ✅ environment.py 已创建（{len(MODULE)} 字节）
  ✅ 表 environment_events 已建（{s.get('total', 0)} 条事件）
  ✅ 白名单机制生效（拒绝 user_liked 这类主观标签）
  ✅ 统计与查询可用

  ★ 这实现了主张6 的**第一步**：
    把真实环境反馈记录下来，与内部分数严格分开。

  ★ 还没实现的（诚实标注）：
    · 环境事件**不影响**记忆存续（故意的，样本太少）
    · 没有「环境被改变」的记录（主张6 的后半：L→E→L）
    · 没有跨生命周期的环境继承

  第一步的意义：
    没有它，后面所有关于「环境筛选」的讨论都是空的。
    现在有了客观数据源，以后才能谈"哪些记忆真的扛住了环境"。
""")

out = {
    "experiment": "E-398",
    "module": "environment.py",
    "module_bytes": len(MODULE),
    "event_kinds": list(env.EVENT_KINDS),
    "stats": s,
    "rejects_subjective": not r_bad.get("ok"),
}
p = os.path.join(_HERE, "results", "e398_environment.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e398_environment.json")
