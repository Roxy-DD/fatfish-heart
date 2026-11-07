"""
notebook.py —— 笔记与待办（记忆系统的另一半）

用户要求（2026-11-07）：
  "记忆系统应该记录那些应该记住的内容以及长期记忆。
   像编程语言等技术性和体系性的内容，就算新学的人也不可能一直记着。
   所以需要定期把这些可复用的记忆整理出来，记录成笔记。
   这就是记笔记的能力，也就是写书、写手册的能力。"
  "这个 todo list 系统也是给模型自己使用的，模型可以自主完全控制。"

核心区分：
  记忆（episodes）  = 我经历过什么（情景，有情绪底色，会过时）
  笔记（notes）     = 我总结出了什么（知识，可复用，长期有效）
  待办（todos）     = 我接下来要做什么（任务队列）

红线：
  · 独立表，不动 episodes/vectors → R1 不受影响
  · 只打 invalid_at 标记，不物理删除 → R2 不遗忘
  · 笔记记 source_ep_ids，可溯源 → R4 可塑性
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid

# ============================================================
# 建表
# ============================================================

SCHEMA_VERSION = "notebook/1.0"


def _init(st) -> None:
    c = st.con
    c.execute("""CREATE TABLE IF NOT EXISTS notes (
        note_id       TEXT PRIMARY KEY,
        title         TEXT NOT NULL,
        body          TEXT NOT NULL,
        topic         TEXT,
        tags          TEXT,
        source_ep_ids TEXT,
        level         TEXT,
        revision      INTEGER DEFAULT 1,
        created_at    REAL,
        updated_at    REAL,
        invalid_at    REAL,
        superseded_by TEXT,
        text_hash     TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS todos (
        todo_id    TEXT PRIMARY KEY,
        title      TEXT NOT NULL,
        detail     TEXT,
        status     TEXT DEFAULT 'pending',
        priority   INTEGER DEFAULT 0,
        kind       TEXT DEFAULT 'task',
        ref_ep_ids TEXT,
        created_at REAL,
        updated_at REAL,
        done_at    REAL,
        invalid_at REAL
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_notes_topic ON notes(topic)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_notes_valid ON notes(invalid_at)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_todos_status ON todos(status)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_todos_valid ON todos(invalid_at)")
    c.commit()


def _now() -> float:
    return time.time()


def _hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _jid(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:14]


# ============================================================
# 笔记 CRUD
# ============================================================

def note_add(st, title: str, body: str, topic: str = "",
             tags: list | None = None, source_ep_ids: list | None = None,
             level: str = "") -> dict:
    """
    新建一条笔记。

    source_ep_ids: 由哪些记忆整理而来（可溯源，R4）
    level:        可选保护层级（L0~L2 会被 hook 机制保护）
    """
    _init(st)
    if not title or not body:
        raise ValueError("title 与 body 均不能为空")

    nid = _jid("nt")
    now = _now()
    st.con.execute(
        "INSERT INTO notes (note_id, title, body, topic, tags, source_ep_ids, "
        "level, revision, created_at, updated_at, text_hash) "
        "VALUES (?,?,?,?,?,?,?,1,?,?,?)",
        (nid, title, body, topic or "",
         json.dumps(tags or [], ensure_ascii=False),
         json.dumps(source_ep_ids or [], ensure_ascii=False),
         level or "", now, now, _hash(body)))
    st.con.commit()
    return get_note(st, nid)


def get_note(st, note_id: str) -> dict | None:
    _init(st)
    row = st.con.execute(
        "SELECT note_id, title, body, topic, tags, source_ep_ids, level, "
        "revision, created_at, updated_at, invalid_at, superseded_by "
        "FROM notes WHERE note_id=?", (note_id,)).fetchone()
    if not row:
        return None
    return {
        "note_id": row[0], "title": row[1], "body": row[2],
        "topic": row[3] or "", "_tags": row[4],
        "tags": _safe_json(row[4], []),
        "source_ep_ids": _safe_json(row[5], []),
        "level": row[6] or "",
        "revision": row[7], "created_at": row[8], "updated_at": row[9],
        "invalid_at": row[10], "superseded_by": row[11],
        "chars": len(row[2] or ""),
    }


def _safe_json(s, default):
    if not s:
        return default
    try:
        v = json.loads(s)
        return v if isinstance(v, type(default)) else default
    except Exception:
        return default


def note_revise(st, note_id: str, title: str | None = None,
                body: str | None = None, topic: str | None = None,
                tags: list | None = None) -> dict | None:
    """修订笔记（R2/R4：旧内容不丢，revision+1，可追溯）"""
    _init(st)
    cur = get_note(st, note_id)
    if not cur:
        return None
    sets = ["revision=revision+1", "updated_at=?"]
    args = [_now()]
    if title is not None:
        sets.append("title=?")
        args.append(title)
    if body is not None:
        sets.append("body=?")
        args.append(body)
        sets.append("text_hash=?")
        args.append(_hash(body))
    if topic is not None:
        sets.append("topic=?")
        args.append(topic)
    if tags is not None:
        sets.append("tags=?")
        args.append(json.dumps(tags, ensure_ascii=False))
    args.append(note_id)
    st.con.execute(f"UPDATE notes SET {', '.join(sets)} WHERE note_id=?", args)
    st.con.commit()
    return get_note(st, note_id)


def note_invalidate(st, note_id: str, superseded_by: str = "") -> bool:
    """标记笔记失效（不物理删除）"""
    _init(st)
    row = st.con.execute(
        "SELECT note_id FROM notes WHERE note_id=?", (note_id,)).fetchone()
    if not row:
        return False
    st.con.execute(
        "UPDATE notes SET invalid_at=?, superseded_by=? WHERE note_id=?",
        (_now(), superseded_by or "", note_id))
    st.con.commit()
    return True


def note_list(st, topic: str = "", include_invalid: bool = False,
              limit: int = 100) -> list[dict]:
    _init(st)
    sql = ("SELECT note_id FROM notes WHERE 1=1" +
           ("" if include_invalid else " AND invalid_at IS NULL"))
    args = []
    if topic:
        sql += " AND topic=?"
        args.append(topic)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    args.append(limit)
    return [n for n in (get_note(st, r[0])
                        for r in st.con.execute(sql, args).fetchall()) if n]


def note_search(st, query: str, limit: int = 20) -> list[dict]:
    """
    笔记检索（简单的 LIKE 匹配，笔记量小不需要向量）。

    不做向量检索的理由：笔记是精炼过的知识，数量少（几十到几百），
    关键词匹配够用；引入向量反而增加复杂度和迁移风险。
    """
    _init(st)
    if not query:
        return note_list(st, limit=limit)
    like = f"%{query}%"
    rows = st.con.execute(
        "SELECT note_id FROM notes WHERE invalid_at IS NULL "
        "AND (title LIKE ? OR body LIKE ? OR topic LIKE ? OR tags LIKE ?) "
        "ORDER BY updated_at DESC LIMIT ?",
        (like, like, like, like, limit)).fetchall()
    return [n for n in (get_note(st, r[0]) for r in rows) if n]


def note_stats(st) -> dict:
    _init(st)
    total = st.con.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    valid = st.con.execute(
        "SELECT COUNT(*) FROM notes WHERE invalid_at IS NULL").fetchone()[0]
    topics = st.con.execute(
        "SELECT topic, COUNT(*) FROM notes WHERE invalid_at IS NULL "
        "AND topic != '' GROUP BY topic").fetchall()
    return {"total": total, "valid": valid, "invalid": total - valid,
            "by_topic": {t: c for t, c in topics}}


# ============================================================
# TODO CRUD
# ============================================================

TODO_STATUS = ("pending", "doing", "done", "dropped")
TODO_KINDS = ("task", "note_hint", "review")


def todo_add(st, title: str, detail: str = "", kind: str = "task",
             priority: int = 0, ref_ep_ids: list | None = None) -> dict:
    """
    新增待办。

    kind:
      task      普通任务
      note_hint "这块该整理成笔记了"（连接记忆与笔记的桥梁）
      review    定期回顾
    """
    _init(st)
    if not title:
        raise ValueError("title 不能为空")
    if kind not in TODO_KINDS:
        kind = "task"
    tid = _jid("td")
    now = _now()
    st.con.execute(
        "INSERT INTO todos (todo_id, title, detail, status, priority, kind, "
        "ref_ep_ids, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (tid, title, detail or "", "pending", int(priority), kind,
         json.dumps(ref_ep_ids or [], ensure_ascii=False), now, now))
    st.con.commit()
    return get_todo(st, tid)


def get_todo(st, todo_id: str) -> dict | None:
    _init(st)
    row = st.con.execute(
        "SELECT todo_id, title, detail, status, priority, kind, ref_ep_ids, "
        "created_at, updated_at, done_at, invalid_at FROM todos "
        "WHERE todo_id=?", (todo_id,)).fetchone()
    if not row:
        return None
    return {
        "todo_id": row[0], "title": row[1], "detail": row[2] or "",
        "status": row[3], "priority": row[4], "kind": row[5],
        "ref_ep_ids": _safe_json(row[6], []),
        "created_at": row[7], "updated_at": row[8],
        "done_at": row[9], "invalid_at": row[10],
    }


def todo_update(st, todo_id: str, **fields) -> dict | None:
    """更新待办字段（status/title/detail/priority）"""
    _init(st)
    cur = get_todo(st, todo_id)
    if not cur:
        return None
    sets = ["updated_at=?"]
    args = [_now()]

    if "title" in fields and fields["title"]:
        sets.append("title=?")
        args.append(fields["title"])
    if "detail" in fields:
        sets.append("detail=?")
        args.append(fields["detail"] or "")
    if "priority" in fields:
        sets.append("priority=?")
        args.append(int(fields["priority"]))
    if "status" in fields:
        s = fields["status"]
        if s in TODO_STATUS:
            sets.append("status=?")
            args.append(s)
            if s == "done":
                sets.append("done_at=?")
                args.append(_now())

    args.append(todo_id)
    st.con.execute(f"UPDATE todos SET {', '.join(sets)} WHERE todo_id=?", args)
    st.con.commit()
    return get_todo(st, todo_id)


def todo_done(st, todo_id: str) -> dict | None:
    return todo_update(st, todo_id, status="done")


def todo_list(st, status: str = "", kind: str = "",
              include_done: bool = False, limit: int = 200) -> list[dict]:
    _init(st)
    sql = "SELECT todo_id FROM todos WHERE invalid_at IS NULL"
    args = []
    if status:
        sql += " AND status=?"
        args.append(status)
    elif not include_done:
        sql += " AND status IN ('pending','doing')"
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    sql += " ORDER BY priority DESC, created_at ASC LIMIT ?"
    args.append(limit)
    return [t for t in (get_todo(st, r[0])
                        for r in st.con.execute(sql, args).fetchall()) if t]


def todo_invalidate(st, todo_id: str) -> bool:
    _init(st)
    row = st.con.execute(
        "SELECT todo_id FROM todos WHERE todo_id=?", (todo_id,)).fetchone()
    if not row:
        return False
    st.con.execute("UPDATE todos SET invalid_at=? WHERE todo_id=?",
                   (_now(), todo_id))
    st.con.commit()
    return True


def todo_stats(st) -> dict:
    _init(st)
    rows = st.con.execute(
        "SELECT status, COUNT(*) FROM todos WHERE invalid_at IS NULL "
        "GROUP BY status").fetchall()
    by_status = {s: c for s, c in rows}
    by_kind = dict(st.con.execute(
        "SELECT kind, COUNT(*) FROM todos WHERE invalid_at IS NULL "
        "GROUP BY kind").fetchall())
    return {
        "total": sum(by_status.values()),
        "pending": by_status.get("pending", 0),
        "doing": by_status.get("doing", 0),
        "done": by_status.get("done", 0),
        "dropped": by_status.get("dropped", 0),
        "by_status": by_status,
        "by_kind": by_kind,
    }


# ============================================================
# 记忆 → 笔记 的桥梁：发现"该整理了"
# ============================================================

def suggest_note_topics(st, min_count: int = 5) -> list[dict]:
    """
    扫描记忆，找出"同一主题下记忆已累积很多"的，建议整理成笔记。

    这是 note_hint 的来源：不是靠猜，而是看数据。
    判据（纯统计，无判断成分）：
      · 按 triggers 词频聚合
      · 某个词出现在 >= min_count 条有效记忆里
      · 且尚未有笔记覆盖该主题

    判断"要不要真的整理"仍交给模型。
    """
    _init(st)
    from collections import Counter

    rows = st.con.execute(
        "SELECT ep_id, raw_text, triggers FROM episodes "
        "WHERE invalid_at IS NULL").fetchall()

    # 统计 trigger 词频
    word_mem = {}
    for eid, txt, trigs in rows:
        try:
            tl = json.loads(trigs) if trigs else []
        except Exception:
            tl = []
        for w in (tl if isinstance(tl, list) else []):
            w = str(w).strip()
            if len(w) < 2:
                continue
            word_mem.setdefault(w, []).append(eid)

    # 已有笔记覆盖的主题
    covered = {r[0] for r in st.con.execute(
        "SELECT topic FROM notes WHERE invalid_at IS NULL AND topic != ''").fetchall()}

    out = []
    for w, eids in word_mem.items():
        if len(eids) < min_count:
            continue
        if w in covered:
            continue
        out.append({"topic": w, "count": len(eids), "ep_ids": eids})

    out.sort(key=lambda x: -x["count"])
    return out[:20]


__all__ = [
    "note_add", "get_note", "note_revise", "note_invalidate",
    "note_list", "note_search", "note_stats",
    "todo_add", "get_todo", "todo_update", "todo_done",
    "todo_list", "todo_invalidate", "todo_stats",
    "suggest_note_topics", "TODO_STATUS", "TODO_KINDS",
]
