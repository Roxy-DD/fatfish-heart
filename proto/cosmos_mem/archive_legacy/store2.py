"""
store v0.2 —— 应用 fixes.py 的三处修复，并加入 importance 字段
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field

from fixes import (extract_person_fixed, split_query_terms,
                   estimate_affect_v2, _LOC_STRIP, _TIME_STRIP)

SCHEMA_VERSION = "cosmos-mem/0.2"

TIME_PAT = re.compile(
    r"(\d{1,2}月\d{1,2}[日号]?|\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?|"
    r"今天|昨天|前天|明天|后天|上周|本周|这周|下周|上个月|这个月|下个月|"
    r"去年|今年|明年|早上|上午|中午|下午|晚上|凌晨|半夜|刚才|之前|后来)")
LOC_PAT = re.compile(
    r"(在公司|在家里|在家|在办公室|在会议室|在学校|在医院|在车上|在路上|"
    r"在咖啡厅|在图书馆|在机场|在车站|在饭店|在卧室|在客厅|在阳台)")
TOPIC_HINT = {
    "工作": ["项目", "需求", "上线", "评审", "汇报", "加班", "会议", "方案", "指标", "老板"],
    "技术": ["代码", "接口", "参数", "部署", "bug", "报错", "服务器", "数据库", "模型", "密码"],
    "生活": ["吃饭", "睡觉", "家务", "买菜", "看病", "房租", "水电", "快递", "妈妈"],
    "金钱": ["工资", "报销", "付款", "借钱", "预算", "账单", "转账"],
    "关系": ["吵架", "道歉", "分手", "生日", "约会", "聊天", "生气"],
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def extract_elements(text: str) -> dict:
    t = text or ""
    times = sorted(set(TIME_PAT.findall(t)))
    locs = sorted(set(LOC_PAT.findall(t)))
    persons = extract_person_fixed(t)
    topics = [tp for tp, kws in TOPIC_HINT.items() if any(k in t for k in kws)]
    return {"person": persons, "time": times,
            "location": locs, "topic": sorted(topics)}


@dataclass
class Episode:
    ep_id: str = ""
    created_at: float = 0.0
    person: list = field(default_factory=list)
    time: list = field(default_factory=list)
    location: list = field(default_factory=list)
    topic: list = field(default_factory=list)
    raw_text: str = ""
    gist: str = ""
    atmosphere: str = ""
    valence: float = 0.0
    arousal: float = 0.0
    importance: float = 0.0
    affect_source: str = "none"
    confidence: float = 1.0
    invalid_at: float | None = None
    superseded_by: str | None = None
    revision: int = 1
    text_hash: str = ""


COLS = ["ep_id", "created_at", "person", "time", "location", "topic",
        "raw_text", "gist", "atmosphere", "valence", "arousal", "importance",
        "affect_source", "confidence", "invalid_at", "superseded_by",
        "revision", "text_hash"]


class MemoryStore:
    def __init__(self, path: str = "cosmos_mem.db"):
        self.path = path
        self.con = sqlite3.connect(path)
        self.con.execute("PRAGMA journal_mode=WAL")
        self._init()

    def _init(self):
        c = self.con
        c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, val TEXT)")
        c.execute("""CREATE TABLE IF NOT EXISTS episodes (
            ep_id TEXT PRIMARY KEY, created_at REAL,
            person TEXT, time TEXT, location TEXT, topic TEXT,
            raw_text TEXT NOT NULL, gist TEXT, atmosphere TEXT,
            valence REAL, arousal REAL, importance REAL, affect_source TEXT,
            confidence REAL, invalid_at REAL, superseded_by TEXT,
            revision INTEGER, text_hash TEXT, schema_version TEXT)""")
        c.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS ep_fts
            USING fts5(raw_text, gist, content='episodes', content_rowid='rowid')""")
        for f in ("person", "time", "location", "topic"):
            c.execute(f"CREATE INDEX IF NOT EXISTS ix_{f} ON episodes({f})")
        c.execute("CREATE INDEX IF NOT EXISTS ix_valid ON episodes(invalid_at)")
        c.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)",
                  (SCHEMA_VERSION,))
        c.commit()

    def add(self, text: str, atmosphere: str = "", gist: str = "",
            explicit: bool = False) -> Episode:
        el = extract_elements(text)
        af = estimate_affect_v2(text, explicit=explicit)
        ep = Episode(
            ep_id=uuid.uuid4().hex[:16], created_at=time.time(),
            person=el["person"], time=el["time"],
            location=el["location"], topic=el["topic"],
            raw_text=text, gist=gist or text[:40], atmosphere=atmosphere,
            valence=float(af.get("valence", 0.0)),
            arousal=float(af.get("arousal", 0.0)),
            importance=float(af.get("importance", 0.0)),
            affect_source=str(af.get("source", "none")),
            text_hash=hashlib.md5(_norm(text).encode()).hexdigest())
        vals = [ep.ep_id, ep.created_at,
                json.dumps(ep.person, ensure_ascii=False),
                json.dumps(ep.time, ensure_ascii=False),
                json.dumps(ep.location, ensure_ascii=False),
                json.dumps(ep.topic, ensure_ascii=False),
                ep.raw_text, ep.gist, ep.atmosphere,
                ep.valence, ep.arousal, ep.importance, ep.affect_source,
                ep.confidence, ep.invalid_at, ep.superseded_by,
                ep.revision, ep.text_hash, SCHEMA_VERSION]
        self.con.execute(
            f"INSERT INTO episodes VALUES ({','.join('?'*len(vals))})", vals)
        self.con.execute("""INSERT INTO ep_fts(rowid, raw_text, gist)
            SELECT rowid, raw_text, gist FROM episodes WHERE ep_id=?""", (ep.ep_id,))
        self.con.commit()
        return ep

    def _ep(self, r) -> Episode:
        d = dict(zip(COLS, r))
        for k in ("person", "time", "location", "topic"):
            d[k] = json.loads(d[k]) if d[k] else []
        return Episode(**d)

    def get(self, ep_id: str) -> Episode | None:
        r = self.con.execute(
            f"SELECT {','.join(COLS)} FROM episodes WHERE ep_id=?", (ep_id,)).fetchone()
        return self._ep(r) if r else None

    def count(self) -> int:
        return self.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]

    def revise(self, ep_id, new_text=None, confidence=None) -> bool:
        if not self.get(ep_id):
            return False
        if new_text is not None:
            el = extract_elements(new_text)
            af = estimate_affect_v2(new_text)
            self.con.execute("""UPDATE episodes SET raw_text=?, person=?, time=?,
                location=?, topic=?, valence=?, arousal=?, importance=?,
                affect_source=?, revision=revision+1, text_hash=?
                WHERE ep_id=?""", (
                new_text,
                json.dumps(el["person"], ensure_ascii=False),
                json.dumps(el["time"], ensure_ascii=False),
                json.dumps(el["location"], ensure_ascii=False),
                json.dumps(el["topic"], ensure_ascii=False),
                af["valence"], af["arousal"], af["importance"], af["source"],
                hashlib.md5(_norm(new_text).encode()).hexdigest(), ep_id))
        if confidence is not None:
            self.con.execute("UPDATE episodes SET confidence=?, revision=revision+1 "
                             "WHERE ep_id=?", (confidence, ep_id))
        self.con.commit()
        return True

    def invalidate(self, ep_id, superseded_by=None) -> bool:
        self.con.execute(
            "UPDATE episodes SET invalid_at=?, superseded_by=?, revision=revision+1 "
            "WHERE ep_id=?", (time.time(), superseded_by, ep_id))
        self.con.commit()
        return True

    def supersede(self, old_id, new_text, **kw):
        new_ep = self.add(new_text, **kw)
        self.invalidate(old_id, superseded_by=new_ep.ep_id)
        return True, new_ep

    def search(self, query: str, top: int = 5, include_invalid: bool = False):
        w = "" if include_invalid else "AND e.invalid_at IS NULL"
        scores: dict[str, float] = {}

        # 1) FTS5 多词 OR（修复 1）
        terms = split_query_terms(query)
        if terms:
            expr = " OR ".join(f'"{t}"' for t in terms)
            try:
                rows = self.con.execute(f"""
                    SELECT e.ep_id, bm25(ep_fts) AS s
                    FROM ep_fts JOIN episodes e ON e.rowid = ep_fts.rowid
                    WHERE ep_fts MATCH ? {w} ORDER BY s LIMIT ?""",
                    (expr, top * 6)).fetchall()
                for eid, s in rows:
                    scores[eid] = scores.get(eid, 0.0) + 1.0 / (1.0 + abs(s))
            except sqlite3.OperationalError:
                pass
            # 2) 子串兜底（FTS 分词不覆盖的中文词）
            for t in terms[:8]:
                for (eid,) in self.con.execute(f"""
                        SELECT ep_id FROM episodes e
                        WHERE raw_text LIKE ? {w} LIMIT ?""",
                        (f"%{t}%", top * 2)).fetchall():
                    scores[eid] = scores.get(eid, 0.0) + 0.4

        # 3) 四要素结构化匹配
        el = extract_elements(query)
        for fname, vals in (("person", el["person"]), ("time", el["time"]),
                            ("location", el["location"]), ("topic", el["topic"])):
            for v in vals:
                for (eid,) in self.con.execute(f"""
                        SELECT ep_id FROM episodes e
                        WHERE {fname} LIKE ? {w} LIMIT ?""",
                        (f"%{v}%", top * 2)).fetchall():
                    scores[eid] = scores.get(eid, 0.0) + 0.8

        # 4) 情绪共振 + 重要性加成
        qa = estimate_affect_v2(query)
        rows = self.con.execute(f"""
            SELECT ep_id, valence, arousal, importance FROM episodes e
            WHERE 1=1 {w}""").fetchall()
        for eid, v, a, imp in rows:
            add = 0.0
            if qa["source"] != "none" and qa["arousal"] > 0.1 and v * qa["valence"] > 0:
                add += 0.6 * (a or 0.0)
            # 重要记忆有基础可见度（不是压缩掉，而是更容易被想起）
            add += 0.25 * (imp or 0.0)
            if add:
                scores[eid] = scores.get(eid, 0.0) + add

        out = []
        for eid in sorted(scores, key=lambda k: -scores[k])[:top]:
            ep = self.get(eid)
            if ep:
                out.append((ep, round(scores[eid], 3)))
        return out

    def stats(self):
        n = self.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        nv = self.con.execute("SELECT COUNT(*) FROM episodes WHERE invalid_at IS NULL").fetchone()[0]
        hi = self.con.execute("SELECT COUNT(*) FROM episodes WHERE arousal>=0.5").fetchone()[0]
        im = self.con.execute("SELECT COUNT(*) FROM episodes WHERE importance>=0.5").fetchone()[0]
        return {"total": n, "valid": nv, "invalid": n - nv,
                "high_arousal": hi, "important": im,
                "size_mb": round(os.path.getsize(self.path) / 1024 / 1024, 2)
                if os.path.exists(self.path) else 0}
