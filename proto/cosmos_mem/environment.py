"""
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


def recent(st, limit: int = 20, kind: str = None,
           include_invalid: bool = False) -> list:
    """
    读最近的环境事件。

    E-407：默认过滤 invalid_at（标记为测试数据的记录）。
    这与记忆表的约定一致 —— 标记不删除，但默认不显示。
    """
    try:
        w = "" if include_invalid else "AND invalid_at IS NULL"
        if kind:
            rows = st.con.execute(
                "SELECT event_id, at, kind, source, outcome, detail, "
                "ep_ids, task FROM environment_events WHERE kind=? "
                f"{w} ORDER BY at DESC LIMIT ?", (kind, limit)).fetchall()
        else:
            rows = st.con.execute(
                "SELECT event_id, at, kind, source, outcome, detail, "
                "ep_ids, task FROM environment_events "
                f"WHERE 1=1 {w} ORDER BY at DESC LIMIT ?",
                (limit,)).fetchall()
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
    """环境事件统计（E-407：默认只看有效的）"""
    try:
        rows = st.con.execute(
            "SELECT kind, COUNT(*) FROM environment_events "
            "WHERE invalid_at IS NULL GROUP BY kind").fetchall()
        by_kind = {k: n for k, n in rows}
        total = sum(by_kind.values())
        # 已完成标记的（用于审计）
        invalid_n = st.con.execute(
            "SELECT COUNT(*) FROM environment_events "
            "WHERE invalid_at IS NOT NULL").fetchone()[0]
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
                "ok": ok_n, "fail": fail_n, "success_rate": rate,
                "invalid": invalid_n}
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
