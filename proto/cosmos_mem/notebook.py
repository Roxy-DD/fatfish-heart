"""
notebook.py v2 —— 三层知识沉淀：随笔 → 笔记 → 手册

用户要求（2026-11-07）：
  "记忆系统应该记录应该记住的内容以及长期记忆。
   像编程语言等技术性和体系性的内容，新学的人也不可能一直记着。
   所以需要定期把这些可复用的记忆整理出来，记录成笔记。
   这就是记笔记的能力，也就是写书、写手册的能力。"

  "这个笔记你要记得，它是那种，比如你刚刚那些容易踩坑的、但是是技术相关的内容。
   就像我们不可能去背诵书，也不可能记住所有的自然规律，
   但是我们会记住一些重要或者常用的东西。
   而那些不常用的、少见但重要的，或者少见又不重要的东西，
   我们经常会把它体系性地记录在笔记本里。
   有的时候，你就可以在闲暇时候把这些笔记整理成体系性的书籍、参考手册等等。
   所以，先是像随笔一样的零散笔记，然后是体系化的书籍、手册等等。"

三层结构（对应人类知识管理的真实形态）：

  L1 随笔 scrap    零散灵感、踩坑记录、一句提醒
                   特点：碎片、不成体系、随时记
                   例："FTS5 的 JOIN 会回表，用两段式查询快 50 倍"

  L2 笔记 note     按主题整理的条目
                   特点：有条理、能独立看懂、可复用
                   例："SQLite FTS5 性能优化"（含原理+做法+实测数据）

  L3 手册 manual   体系化的书/参考手册
                   特点：成体系、有目录、能当教材
                   例："cosmos-mem 记忆系统设计手册"（多章节）

演化路径：
  scrap（随时记）→ 攒够同类 → note（整理成主题）→ 攒够同域 → manual（体系化成书）

判断该放哪一层的依据（用户给的标准）：
  · 常用 → 记忆（episodes），不写笔记
  · 少见但重要 → 笔记（要写得详细）
  · 少见又不重要 → 也记，但简短、优先级低

红线：
  · 独立表，不动 episodes/vectors → R1 不受影响
  · 只打 invalid_at 标记，不物理删除 → R2 不遗忘
  · 记录 source_ep_ids / source_note_ids → R4 可溯源
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid

SCHEMA_VERSION = "notebook/2.0"

# 三层类型
TIERS = ("scrap", "note", "manual")

TIER_NAMES = {
    "scrap": "随笔",
    "note": "笔记",
    "manual": "手册",
}


def _init(st) -> None:
    c = st.con
    # ---------- 笔记表（三层共用，用 tier 区分）----------
    c.execute("""CREATE TABLE IF NOT EXISTS notes (
        note_id        TEXT PRIMARY KEY,
        tier           TEXT NOT NULL DEFAULT 'note',
        title          TEXT NOT NULL,
        body           TEXT NOT NULL,
        topic          TEXT,
        tags           TEXT,
        source_ep_ids  TEXT,
        source_note_ids TEXT,
        parent_id      TEXT,
        order_idx      INTEGER DEFAULT 0,
        level          TEXT,
        revision       INTEGER DEFAULT 1,
        created_at     REAL,
        updated_at     REAL,
        invalid_at     REAL,
        superseded_by  TEXT,
        text_hash      TEXT
    )""")
    # 兼容旧表：补列（已有库升级时不丢数据）
    existing = {r[1] for r in c.execute("PRAGMA table_info(notes)").fetchall()}
    for col, ddl in (
        ("tier", "TEXT NOT NULL DEFAULT 'note'"),
        ("source_note_ids", "TEXT"),
        ("parent_id", "TEXT"),
        ("order_idx", "INTEGER DEFAULT 0"),
    ):
        if col not in existing:
            try:
                c.execute(f"ALTER TABLE notes ADD COLUMN {col} {ddl}")
            except Exception:
                pass

    c.execute("""CREATE TABLE IF NOT EXISTS todos (
        todo_id    TEXT PRIMARY KEY,
        title      TEXT NOT NULL,
        detail     TEXT,
        status     TEXT DEFAULT 'pending',
        priority   INTEGER DEFAULT 0,
        kind       TEXT DEFAULT 'task',
        ref_ep_ids TEXT,
        ref_note_ids TEXT,
        created_at REAL,
        updated_at REAL,
        done_at    REAL,
        invalid_at REAL
    )""")
    existing_t = {r[1] for r in c.execute("PRAGMA table_info(todos)").fetchall()}
    if "ref_note_ids" not in existing_t:
        try:
            c.execute("ALTER TABLE todos ADD COLUMN ref_note_ids TEXT")
        except Exception:
            pass

    c.execute("CREATE INDEX IF NOT EXISTS ix_notes_tier ON notes(tier)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_notes_topic ON notes(topic)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_notes_parent ON notes(parent_id)")
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


def _safe_json(s, default):
    if not s:
        return default
    try:
        v = json.loads(s)
        return v if isinstance(v, type(default)) else default
    except Exception:
        return default


# ============================================================
# 笔记：三层共用
# ============================================================

def note_add(st, title: str, body: str, tier: str = "note",
             topic: str = "", tags: list | None = None,
             source_ep_ids: list | None = None,
             source_note_ids: list | None = None,
             parent_id: str = "", order_idx: int = 0,
             level: str = "") -> dict:
    """
    新建一条知识条目。

    tier:
      scrap   随笔（零散、随时记）
      note    笔记（按主题整理）
      manual  手册/书籍（体系化，可含多章节）

    parent_id: 手册的章节可以指向手册本身
    source_ep_ids / source_note_ids: 可溯源（这条知识从哪来）
    """
    _init(st)
    if not title or not body:
        raise ValueError("title 与 body 均不能为空")
    if tier not in TIERS:
        tier = "note"

    nid = _jid("nt")
    now = _now()
    st.con.execute(
        "INSERT INTO notes (note_id, tier, title, body, topic, tags, "
        "source_ep_ids, source_note_ids, parent_id, order_idx, level, "
        "revision, created_at, updated_at, text_hash) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)",
        (nid, tier, title, body, topic or "",
         json.dumps(tags or [], ensure_ascii=False),
         json.dumps(source_ep_ids or [], ensure_ascii=False),
         json.dumps(source_note_ids or [], ensure_ascii=False),
         parent_id or "", int(order_idx), level or "",
         now, now, _hash(body)))
    st.con.commit()
    return get_note(st, nid)


def get_note(st, note_id: str) -> dict | None:
    _init(st)
    row = st.con.execute(
        "SELECT note_id, tier, title, body, topic, tags, source_ep_ids, "
        "source_note_ids, parent_id, order_idx, level, revision, "
        "created_at, updated_at, invalid_at, superseded_by "
        "FROM notes WHERE note_id=?", (note_id,)).fetchone()
    if not row:
        return None
    return {
        "note_id": row[0], "tier": row[1] or "note",
        "tier_name": TIER_NAMES.get(row[1] or "note", "笔记"),
        "title": row[2], "body": row[3],
        "topic": row[4] or "",
        "tags": _safe_json(row[5], []),
        "source_ep_ids": _safe_json(row[6], []),
        "source_note_ids": _safe_json(row[7], []),
        "parent_id": row[8] or "",
        "order_idx": row[9] or 0,
        "level": row[10] or "",
        "revision": row[11],
        "created_at": row[12], "updated_at": row[13],
        "invalid_at": row[14], "superseded_by": row[15],
        "chars": len(row[3] or ""),
    }


def note_revise(st, note_id: str, title: str | None = None,
                body: str | None = None, topic: str | None = None,
                tags: list | None = None, tier: str | None = None) -> dict | None:
    """修订（R2/R4：旧内容不丢，revision+1）"""
    _init(st)
    if not get_note(st, note_id):
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
    if tier is not None and tier in TIERS:
        sets.append("tier=?")
        args.append(tier)
    args.append(note_id)
    st.con.execute(f"UPDATE notes SET {', '.join(sets)} WHERE note_id=?", args)
    st.con.commit()
    return get_note(st, note_id)


def note_promote(st, note_id: str, to_tier: str) -> dict | None:
    """
    升层：scrap → note → manual。
    这是三层之间的演化，revision+1 保留历史。
    """
    _init(st)
    cur = get_note(st, note_id)
    if not cur or to_tier not in TIERS:
        return None
    order = {"scrap": 0, "note": 1, "manual": 2}
    if order.get(to_tier, 0) <= order.get(cur["tier"], 0):
        return cur   # 不降级
    return note_revise(st, note_id, tier=to_tier)


def note_invalidate(st, note_id: str, superseded_by: str = "") -> bool:
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


def note_list(st, tier: str = "", topic: str = "",
              include_invalid: bool = False, limit: int = 200) -> list[dict]:
    _init(st)
    sql = ("SELECT note_id FROM notes WHERE 1=1" +
           ("" if include_invalid else " AND invalid_at IS NULL"))
    args = []
    if tier:
        sql += " AND tier=?"
        args.append(tier)
    if topic:
        sql += " AND topic=?"
        args.append(topic)
    sql += " ORDER BY order_idx ASC, updated_at DESC LIMIT ?"
    args.append(limit)
    return [n for n in (get_note(st, r[0])
                        for r in st.con.execute(sql, args).fetchall()) if n]


def note_search(st, query: str, tier: str = "", limit: int = 30) -> list[dict]:
    """笔记检索（笔记量小，LIKE 够用，不引入向量以降低迁移风险）"""
    _init(st)
    if not query:
        return note_list(st, tier=tier, limit=limit)
    like = f"%{query}%"
    sql = ("SELECT note_id FROM notes WHERE invalid_at IS NULL "
           "AND (title LIKE ? OR body LIKE ? OR topic LIKE ? OR tags LIKE ?)")
    args = [like, like, like, like]
    if tier:
        sql += " AND tier=?"
        args.append(tier)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    args.append(limit)
    return [n for n in (get_note(st, r[0])
                        for r in st.con.execute(sql, args).fetchall()) if n]


def note_children(st, manual_id: str) -> list[dict]:
    """手册的章节列表（按 order_idx 排序）"""
    _init(st)
    rows = st.con.execute(
        "SELECT note_id FROM notes WHERE parent_id=? AND invalid_at IS NULL "
        "ORDER BY order_idx ASC", (manual_id,)).fetchall()
    return [n for n in (get_note(st, r[0]) for r in rows) if n]


def note_stats(st) -> dict:
    _init(st)
    rows = st.con.execute(
        "SELECT tier, COUNT(*) FROM notes WHERE invalid_at IS NULL "
        "GROUP BY tier").fetchall()
    by_tier = {t: c for t, c in rows}
    topics = st.con.execute(
        "SELECT topic, COUNT(*) FROM notes WHERE invalid_at IS NULL "
        "AND topic != '' GROUP BY topic").fetchall()
    total = st.con.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    valid = sum(by_tier.values())
    return {
        "total": total, "valid": valid, "invalid": total - valid,
        "by_tier": by_tier,
        "scrap": by_tier.get("scrap", 0),
        "note": by_tier.get("note", 0),
        "manual": by_tier.get("manual", 0),
        "by_topic": {t: c for t, c in topics},
    }


# ============================================================
# 待办
# ============================================================

TODO_STATUS = ("pending", "doing", "done", "dropped")
TODO_KINDS = ("task", "note_hint", "review")


def todo_add(st, title: str, detail: str = "", kind: str = "task",
             priority: int = 0, ref_ep_ids: list | None = None,
             ref_note_ids: list | None = None) -> dict:
    _init(st)
    if not title:
        raise ValueError("title 不能为空")
    if kind not in TODO_KINDS:
        kind = "task"
    tid = _jid("td")
    now = _now()
    st.con.execute(
        "INSERT INTO todos (todo_id, title, detail, status, priority, kind, "
        "ref_ep_ids, ref_note_ids, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (tid, title, detail or "", "pending", int(priority), kind,
         json.dumps(ref_ep_ids or [], ensure_ascii=False),
         json.dumps(ref_note_ids or [], ensure_ascii=False), now, now))
    st.con.commit()
    return get_todo(st, tid)


def get_todo(st, todo_id: str) -> dict | None:
    _init(st)
    row = st.con.execute(
        "SELECT todo_id, title, detail, status, priority, kind, ref_ep_ids, "
        "ref_note_ids, created_at, updated_at, done_at, invalid_at "
        "FROM todos WHERE todo_id=?", (todo_id,)).fetchone()
    if not row:
        return None
    return {
        "todo_id": row[0], "title": row[1], "detail": row[2] or "",
        "status": row[3], "priority": row[4], "kind": row[5],
        "ref_ep_ids": _safe_json(row[6], []),
        "ref_note_ids": _safe_json(row[7], []),
        "created_at": row[8], "updated_at": row[9],
        "done_at": row[10], "invalid_at": row[11],
    }


def todo_update(st, todo_id: str, **fields) -> dict | None:
    _init(st)
    if not get_todo(st, todo_id):
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
    if "status" in fields and fields["status"] in TODO_STATUS:
        sets.append("status=?")
        args.append(fields["status"])
        if fields["status"] == "done":
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
# 演化建议：记忆 → 随笔 → 笔记 → 手册
# ============================================================

def suggest_note_topics(st, min_count: int = 5) -> list[dict]:
    """扫描记忆，找出同主题累积过多的，建议写成笔记"""
    _init(st)
    rows = st.con.execute(
        "SELECT ep_id, raw_text, triggers FROM episodes "
        "WHERE invalid_at IS NULL").fetchall()

    word_mem = {}
    for eid, txt, trigs in rows:
        tl = _safe_json(trigs, [])
        for w in tl:
            w = str(w).strip()
            if len(w) < 2:
                continue
            word_mem.setdefault(w, []).append(eid)

    covered = {r[0] for r in st.con.execute(
        "SELECT topic FROM notes WHERE invalid_at IS NULL AND topic != ''").fetchall()}

    out = []
    for w, eids in word_mem.items():
        if len(eids) < min_count or w in covered:
            continue
        out.append({"topic": w, "count": len(eids), "ep_ids": eids,
                    "suggest_tier": "note"})
    out.sort(key=lambda x: -x["count"])
    return out[:20]


def suggest_manual_topics(st, min_notes: int = 5) -> list[dict]:
    """
    扫描笔记，找出同主题笔记已足够多的，建议整理成手册。

    这是第二段演化：note → manual
    """
    _init(st)
    rows = st.con.execute(
        "SELECT topic, COUNT(*) FROM notes "
        "WHERE invalid_at IS NULL AND tier='note' AND topic != '' "
        "GROUP BY topic").fetchall()

    existing_manuals = {r[0] for r in st.con.execute(
        "SELECT topic FROM notes WHERE invalid_at IS NULL "
        "AND tier='manual' AND topic != ''").fetchall()}

    out = []
    for topic, cnt in rows:
        if cnt < min_notes or topic in existing_manuals:
            continue
        note_ids = [r[0] for r in st.con.execute(
            "SELECT note_id FROM notes WHERE invalid_at IS NULL "
            "AND tier='note' AND topic=? ORDER BY created_at", (topic,)).fetchall()]
        out.append({"topic": topic, "count": cnt,
                    "note_ids": note_ids, "suggest_tier": "manual"})
    out.sort(key=lambda x: -x["count"])
    return out


def suggest_scrap_promote(st, min_scraps: int = 3) -> list[dict]:
    """
    扫描随笔，找出同关键词已有多条的，建议升为笔记。

    第一段演化：scrap → note
    """
    _init(st)
    rows = st.con.execute(
        "SELECT note_id, title, body, topic FROM notes "
        "WHERE invalid_at IS NULL AND tier='scrap'").fetchall()

    groups = {}
    for nid, title, body, topic in rows:
        key = topic or ""
        if not key:
            # 没 topic 的用标题前 4 字聚类
            key = (title or "")[:4]
        if not key:
            continue
        groups.setdefault(key, []).append(nid)

    out = []
    for topic, nids in groups.items():
        if len(nids) < min_scraps:
            continue
        out.append({"topic": topic, "count": len(nids),
                    "note_ids": nids, "suggest_tier": "note"})
    out.sort(key=lambda x: -x["count"])
    return out


__all__ = [
    "note_add", "get_note", "note_revise", "note_promote", "note_invalidate",
    "note_list", "note_search", "note_children", "note_stats",
    "todo_add", "get_todo", "todo_update", "todo_done",
    "todo_list", "todo_invalidate", "todo_stats",
    "suggest_note_topics", "suggest_manual_topics", "suggest_scrap_promote",
    "TIERS", "TIER_NAMES", "TODO_STATUS", "TODO_KINDS",
]
