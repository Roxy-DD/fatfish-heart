"""
audit.py —— 统一审计日志（结构化，可查询）

用户要求："应该要有一个 log 日志，这样你才方便自己去检查，而不是我给你讲现象。"

设计目标：
  ① 结构化：每条日志有 kind/at/sid/ep_id/msg/extra，可 SQL 查询
  ② 覆盖记忆系统全生命周期：注入、回忆、记忆、睡眠、矛盾判定、错误
  ③ 我自己能查：一条 SQL 就能看"最近发生了啥、哪里错了"
  ④ 守红线：只 INSERT 追加，不改 episodes/vectors；独立表不影响 R1/R3

表结构（audit_log，追加式，只增不删）：
  seq         自增序号
  at          时间戳（epoch 秒）
  kind        事件类型（inject/recall/remember/sleep/conflict/error/...）
  sid         会话 id（可空）
  ep_id       相关记忆 id（可空）
  msg         一句话描述
  extra       附加 JSON（详细数据）

为什么不塞 meta 表：
  meta 是 key-value，塞日志会膨胀成超长 JSON，难查询。
为什么不塞 plugin-debug.jsonl：
  纯文本，难结构化查询，且和内核数据分离。

audit 日志是"运行期诊断数据"，不是"记忆数据"，
不纳入 R1 无损迁移比对（R1 比的是 episodes+vectors，不含日志）。
"""
from __future__ import annotations

import json
import time

# 事件类型白名单
KINDS = (
    "inject",        # 注入记忆到上下文
    "recall",        # 主动回忆（mem_search）
    "remember",      # 主动记忆（mem_add）
    "sleep",         # 睡眠整理（mem_sleep）
    "conflict",      # 矛盾判定
    "chain",         # 链式展开
    "affect",        # 情绪判断
    "config",        # 配置变更
    "error",         # 错误
    "warn",          # 警告
)


def _init(st) -> None:
    st.con.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            at REAL NOT NULL,
            kind TEXT NOT NULL,
            sid TEXT,
            ep_id TEXT,
            msg TEXT,
            extra TEXT
        )""")
    st.con.commit()


def log(st, kind: str, msg: str = "", sid: str = "",
        ep_id: str = "", extra: dict | None = None) -> None:
    """写一条审计日志（追加，不失败不抛异常）"""
    try:
        _init(st)
        st.con.execute(
            "INSERT INTO audit_log (at, kind, sid, ep_id, msg, extra) "
            "VALUES (?,?,?,?,?,?)",
            (time.time(), kind, sid or "", ep_id or "", msg or "",
             json.dumps(extra, ensure_ascii=False) if extra else None))
        st.con.commit()
    except Exception:
        pass


def query(st, kind: str | None = None, sid: str | None = None,
          limit: int = 100, offset: int = 0) -> list[dict]:
    """查询日志（按时间倒序）"""
    _init(st)
    sql = "SELECT seq, at, kind, sid, ep_id, msg, extra FROM audit_log"
    conds = []
    args = []
    if kind:
        conds.append("kind=?")
        args.append(kind)
    if sid:
        conds.append("sid=?")
        args.append(sid)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY seq DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    try:
        rows = st.con.execute(sql, args).fetchall()
    except Exception:
        return []
    out = []
    for seq, at, k, s, e, m, x in rows:
        out.append({
            "seq": seq, "at": at, "kind": k, "sid": s,
            "ep_id": e, "msg": m,
            "extra": json.loads(x) if x else None,
        })
    return out


def stats(st) -> dict:
    """各类型事件计数（诊断总览）"""
    _init(st)
    try:
        rows = st.con.execute(
            "SELECT kind, COUNT(*) FROM audit_log GROUP BY kind").fetchall()
        return {"total": sum(r[1] for r in rows),
                "by_kind": {r[0]: r[1] for r in rows}}
    except Exception:
        return {"total": 0, "by_kind": {}}
