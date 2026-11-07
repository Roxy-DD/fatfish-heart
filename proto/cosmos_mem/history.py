"""
history.py —— 内容历史版本（R4 可塑性的完整实现）

E-244 诊断发现的问题：
  revise() 直接 UPDATE 覆盖 raw_text/body，旧文本**永久丢失**。
  这违反 R4："旧值永不物理删除，可溯源"。

  revision 字段只是递增计数，**没有存下任何历史内容**。

设计：
  独立的 *_history 表，每次修订前把旧内容快照进去。

  episodes_history: 记录记忆的每次修订前的版本
  notes_history:    记录笔记的每次修订前的版本

为什么不用"每次修订新建一条 episode"：
  · 会让 episodes 表膨胀，影响检索性能
  · episodes 语义是"一条情景"，修订版不是新情景

为什么用独立历史表：
  · 不影响 episodes 的 schema（R1 红线只比对 episodes/vectors）
  · 历史表只在需要时查，不影响热路径性能
  · 纯追加，不改动会被修订的那条

红线：
  · R2 不遗忘：历史表只增不删
  · R4 可塑性：可追溯任意版本
  · R1 迁移：不动 episodes/vectors，红线不受影响
"""
from __future__ import annotations

import json
import time


def _init(st) -> None:
    c = st.con
    c.execute("""CREATE TABLE IF NOT EXISTS episodes_history (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ep_id       TEXT NOT NULL,
        revision    INTEGER,
        raw_text    TEXT,
        atmosphere  TEXT,
        cost        REAL,
        affect_peak REAL,
        valence     REAL,
        person      TEXT,
        time        TEXT,
        location    TEXT,
        triggers    TEXT,
        text_hash   TEXT,
        archived_at REAL,
        reason      TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS notes_history (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        note_id    TEXT NOT NULL,
        revision   INTEGER,
        title      TEXT,
        body       TEXT,
        topic      TEXT,
        tags       TEXT,
        tier       TEXT,
        text_hash  TEXT,
        archived_at REAL,
        reason     TEXT
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_ephist_ep ON episodes_history(ep_id)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_nthist_note ON notes_history(note_id)")
    c.commit()


# ============================================================
# 记忆历史
# ============================================================

def archive_episode(st, ep_id: str, reason: str = "") -> bool:
    """
    把 episode 的**当前内容**快照进历史表。

    调用时机：在修改之前调用（这样存下的是"修改前的版本"）。
    """
    try:
        _init(st)
        row = st.con.execute(
            "SELECT ep_id, revision, raw_text, atmosphere, cost, affect_peak, "
            "valence, person, time, location, triggers, text_hash "
            "FROM episodes WHERE ep_id=?", (ep_id,)).fetchone()
        if not row:
            return False
        st.con.execute(
            "INSERT INTO episodes_history (ep_id, revision, raw_text, "
            "atmosphere, cost, affect_peak, valence, person, time, location, "
            "triggers, text_hash, archived_at, reason) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (row[0], row[1], row[2], row[3], row[4], row[5], row[6],
             row[7], row[8], row[9], row[10], row[11], time.time(), reason or ""))
        st.con.commit()
        return True
    except Exception:
        return False


def episode_history(st, ep_id: str) -> list[dict]:
    """查询某条记忆的完整历史（从旧到新）"""
    _init(st)
    rows = st.con.execute(
        "SELECT revision, raw_text, cost, affect_peak, valence, "
        "archived_at, reason FROM episodes_history "
        "WHERE ep_id=? ORDER BY id ASC", (ep_id,)).fetchall()
    cur = st.con.execute(
        "SELECT revision, raw_text, cost, affect_peak, valence "
        "FROM episodes WHERE ep_id=?", (ep_id,)).fetchone()

    out = []
    for r in rows:
        out.append({
            "revision": r[0], "raw_text": r[1],
            "cost": r[2], "affect": r[3], "valence": r[4],
            "archived_at": r[5], "reason": r[6] or "",
            "is_current": False,
        })
    if cur:
        out.append({
            "revision": cur[0], "raw_text": cur[1],
            "cost": cur[2], "affect": cur[3], "valence": cur[4],
            "archived_at": None, "reason": "当前版本",
            "is_current": True,
        })
    return out


def episode_history_stats(st) -> dict:
    _init(st)
    n = st.con.execute("SELECT COUNT(*) FROM episodes_history").fetchone()[0]
    eps = st.con.execute(
        "SELECT COUNT(DISTINCT ep_id) FROM episodes_history").fetchone()[0]
    return {"history_records": n, "memories_with_history": eps}


# ============================================================
# 笔记历史
# ============================================================

def archive_note(st, note_id: str, reason: str = "") -> bool:
    try:
        _init(st)
        row = st.con.execute(
            "SELECT note_id, revision, title, body, topic, tags, tier, text_hash "
            "FROM notes WHERE note_id=?", (note_id,)).fetchone()
        if not row:
            return False
        st.con.execute(
            "INSERT INTO notes_history (note_id, revision, title, body, "
            "topic, tags, tier, text_hash, archived_at, reason) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (row[0], row[1], row[2], row[3], row[4], row[5], row[6],
             row[7], time.time(), reason or ""))
        st.con.commit()
        return True
    except Exception:
        return False


def note_history(st, note_id: str) -> list[dict]:
    _init(st)
    rows = st.con.execute(
        "SELECT revision, title, body, topic, tier, archived_at, reason "
        "FROM notes_history WHERE note_id=? ORDER BY id ASC",
        (note_id,)).fetchall()
    cur = st.con.execute(
        "SELECT revision, title, body, topic, tier FROM notes WHERE note_id=?",
        (note_id,)).fetchone()

    out = []
    for r in rows:
        out.append({
            "revision": r[0], "title": r[1], "body": r[2],
            "topic": r[3], "tier": r[4],
            "archived_at": r[5], "reason": r[6] or "",
            "is_current": False,
        })
    if cur:
        out.append({
            "revision": cur[0], "title": cur[1], "body": cur[2],
            "topic": cur[3], "tier": cur[4],
            "archived_at": None, "reason": "当前版本",
            "is_current": True,
        })
    return out


def note_history_stats(st) -> dict:
    _init(st)
    n = st.con.execute("SELECT COUNT(*) FROM notes_history").fetchone()[0]
    ids = st.con.execute(
        "SELECT COUNT(DISTINCT note_id) FROM notes_history").fetchone()[0]
    return {"history_records": n, "notes_with_history": ids}


def stats(st) -> dict:
    return {
        "episodes": episode_history_stats(st),
        "notes": note_history_stats(st),
    }


__all__ = [
    "archive_episode", "episode_history", "episode_history_stats",
    "archive_note", "note_history", "note_history_stats", "stats",
]
