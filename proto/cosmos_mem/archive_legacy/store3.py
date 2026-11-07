"""
store v0.3 —— 接 BGE 语义检索 + 链式展开

补 v0.2 两个最大缺口（docs/51 P0）：
  P0-1 接 BGE 语义检索：同义改写也要能查到
  P0-2 链式展开：命中一条后，沿"情景相似"或"情绪共振"继续展开

链式展开对应用户原话：
  "看到这个场景，夕阳西落，突然发现有一天我也在哪看到过这样的夕阳，
   然后又跟着那个夕阳，回忆起了小时候我在哪看的夕阳，小时候的事情。"

机制：
  seed    = 查询命中的第一跳
  hop 1   = 与 seed 情景相似（向量近）**或** 情绪共振（valence/arousal 同向且强度近）
  hop 2   = 与 hop1 情景相似
  限制：最多 2 跳；每跳最多 2 条；不重复；可关闭
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from dataclasses import dataclass, field

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from fixes import (extract_person_fixed, split_query_terms,
                   estimate_affect_v2)

SCHEMA_VERSION = "cosmos-mem/0.3"
MODEL_DIR = r"C:\Users\xuee\Desktop\肥鱼之心\models\bge-small-zh"

TIME_PAT = re.compile(
    r"(\d{1,2}月\d{1,2}[日号]?|\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?|"
    r"今天|昨天|前天|明天|后天|上周|本周|这周|下周|上个月|这个月|下个月|"
    r"去年|今年|明年|早上|上午|中午|下午|傍晚|晚上|凌晨|半夜|刚才|之前|后来|"
    r"小时候|童年|以前|当年)")
LOC_PAT = re.compile(
    r"(在公司|在家里|在家|在办公室|在会议室|在学校|在医院|在车上|在路上|"
    r"在咖啡厅|在图书馆|在机场|在车站|在饭店|在卧室|在客厅|在阳台)")
TOPIC_HINT = {
    "工作": ["项目", "需求", "上线", "评审", "汇报", "加班", "会议", "方案", "指标", "老板"],
    "技术": ["代码", "接口", "参数", "部署", "bug", "报错", "服务器", "数据库", "模型", "密码"],
    "生活": ["吃饭", "睡觉", "家务", "买菜", "看病", "房租", "水电", "快递", "妈妈", "家里"],
    "金钱": ["工资", "报销", "付款", "借钱", "预算", "账单", "转账"],
    "关系": ["吵架", "道歉", "分手", "生日", "约会", "聊天", "生气"],
    "场景": ["夕阳", "天气", "天空", "雨", "雪", "风", "街", "路", "树", "光"],
}


def _norm(s):
    return re.sub(r"\s+", "", s or "")


def extract_elements(text):
    t = text or ""
    return {"person": extract_person_fixed(t),
            "time": sorted(set(TIME_PAT.findall(t))),
            "location": sorted(set(LOC_PAT.findall(t))),
            "topic": sorted([tp for tp, kws in TOPIC_HINT.items()
                             if any(k in t for k in kws)])}


class Embedder:
    """BGE 语义编码（懒加载）"""
    _inst = None

    def __init__(self):
        self._tok = None
        self._mdl = None

    @classmethod
    def get(cls):
        if cls._inst is None:
            cls._inst = cls()
        return cls._inst

    def _load(self):
        if self._mdl is not None:
            return
        import torch
        from transformers import AutoTokenizer, AutoModel
        torch.set_num_threads(4)
        self._tok = AutoTokenizer.from_pretrained(MODEL_DIR)
        self._mdl = AutoModel.from_pretrained(MODEL_DIR).eval()

    def encode(self, texts):
        import numpy as np
        if isinstance(texts, str):
            texts = [texts]
        self._load()
        import torch
        with torch.no_grad():
            enc = self._tok(texts, padding=True, truncation=True,
                            max_length=128, return_tensors="pt")
            o = self._mdl(**enc)
        v = o.last_hidden_state[:, 0].numpy().astype("float32")
        n = np.linalg.norm(v, axis=1, keepdims=True)
        n[n == 0] = 1
        return v / n


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
    def __init__(self, path="cosmos_mem.db", use_semantic=True):
        self.path = path
        self.use_semantic = use_semantic
        self.con = sqlite3.connect(path)
        self.con.execute("PRAGMA journal_mode=WAL")
        self._vec = None       # 内存向量矩阵（None=未加载）
        self._vec_ids = None   # 对应 ep_id
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
        c.execute("""CREATE TABLE IF NOT EXISTS vectors (
            ep_id TEXT PRIMARY KEY, dim INTEGER, vec BLOB)""")
        c.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS ep_fts
            USING fts5(raw_text, gist, atmosphere, content='episodes',
                       content_rowid='rowid')""")
        for f in ("person", "time", "location", "topic"):
            c.execute(f"CREATE INDEX IF NOT EXISTS ix_{f} ON episodes({f})")
        c.execute("CREATE INDEX IF NOT EXISTS ix_valid ON episodes(invalid_at)")
        c.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)",
                  (SCHEMA_VERSION,))
        c.commit()

    # ---------- 写入 ----------
    def add(self, text, atmosphere="", gist="", explicit=False, encode=True):
        el = extract_elements(text)
        af = estimate_affect_v2(text, explicit=explicit)
        ep = Episode(ep_id=uuid.uuid4().hex[:16], created_at=time.time(),
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
        self.con.execute(f"INSERT INTO episodes VALUES ({','.join('?'*len(vals))})", vals)
        self.con.execute("""INSERT INTO ep_fts(rowid, raw_text, gist, atmosphere)
            SELECT rowid, raw_text, gist, atmosphere FROM episodes WHERE ep_id=?""",
            (ep.ep_id,))
        if encode and self.use_semantic:
            try:
                v = Embedder.get().encode(text)[0]
                self.con.execute("INSERT OR REPLACE INTO vectors VALUES (?,?,?)",
                                 (ep.ep_id, int(len(v)), v.tobytes()))
                self._vec = None    # 失效缓存
            except Exception as e:
                print(f"[warn] 编码失败: {e}", file=sys.stderr)
        self.con.commit()
        return ep

    def _ep(self, r):
        d = dict(zip(COLS, r))
        for k in ("person", "time", "location", "topic"):
            d[k] = json.loads(d[k]) if d[k] else []
        return Episode(**d)

    def get(self, ep_id):
        r = self.con.execute(
            f"SELECT {','.join(COLS)} FROM episodes WHERE ep_id=?", (ep_id,)).fetchone()
        return self._ep(r) if r else None

    def count(self):
        return self.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]

    # ---------- 可塑性 ----------
    def revise(self, ep_id, new_text=None, confidence=None):
        if not self.get(ep_id):
            return False
        if new_text is not None:
            el = extract_elements(new_text)
            af = estimate_affect_v2(new_text)
            self.con.execute("""UPDATE episodes SET raw_text=?, person=?, time=?,
                location=?, topic=?, valence=?, arousal=?, importance=?,
                affect_source=?, revision=revision+1, text_hash=? WHERE ep_id=?""",
                (new_text, json.dumps(el["person"], ensure_ascii=False),
                 json.dumps(el["time"], ensure_ascii=False),
                 json.dumps(el["location"], ensure_ascii=False),
                 json.dumps(el["topic"], ensure_ascii=False),
                 af["valence"], af["arousal"], af["importance"], af["source"],
                 hashlib.md5(_norm(new_text).encode()).hexdigest(), ep_id))
            if self.use_semantic:
                try:
                    import numpy as np
                    v = Embedder.get().encode(new_text)[0]
                    self.con.execute("INSERT OR REPLACE INTO vectors VALUES (?,?,?)",
                                     (ep_id, int(len(v)), v.tobytes()))
                    self._vec = None
                except Exception:
                    pass
        if confidence is not None:
            self.con.execute("UPDATE episodes SET confidence=?, revision=revision+1 "
                             "WHERE ep_id=?", (confidence, ep_id))
        self.con.commit()
        return True

    def invalidate(self, ep_id, superseded_by=None):
        self.con.execute("UPDATE episodes SET invalid_at=?, superseded_by=?, "
                         "revision=revision+1 WHERE ep_id=?",
                         (time.time(), superseded_by, ep_id))
        self.con.commit()
        return True

    def supersede(self, old_id, new_text, **kw):
        ne = self.add(new_text, **kw)
        self.invalidate(old_id, superseded_by=ne.ep_id)
        return True, ne

    # ---------- 向量 ----------
    def _load_vectors(self):
        """把向量载入内存（可丢弃、可重建）"""
        import numpy as np
        if self._vec is not None:
            return
        rows = self.con.execute("""
            SELECT v.ep_id, v.vec FROM vectors v
            JOIN episodes e ON e.ep_id=v.ep_id
            WHERE e.invalid_at IS NULL""").fetchall()
        if not rows:
            self._vec, self._vec_ids = np.zeros((0, 512), "float32"), []
            return
        ids = [r[0] for r in rows]
        arr = np.vstack([np.frombuffer(r[1], dtype="float32") for r in rows])
        self._vec, self._vec_ids = arr, ids

    def rebuild_vectors(self):
        """从原文重算所有向量（索引可重建）"""
        import numpy as np
        rows = self.con.execute("SELECT ep_id, raw_text FROM episodes").fetchall()
        if not rows:
            return 0
        texts = [r[1] for r in rows]
        V = Embedder.get().encode(texts)
        for (eid, _), v in zip(rows, V):
            self.con.execute("INSERT OR REPLACE INTO vectors VALUES (?,?,?)",
                             (eid, int(len(v)), v.tobytes()))
        self.con.commit()
        self._vec = None
        return len(rows)

    # ---------- 检索 ----------
    def search(self, query, top=5, include_invalid=False,
               expand=False, hops=2):
        """
        返回 [(Episode, score, hop)]
        expand=True 时做链式展开（对应"夕阳→小时候的夕阳"）
        """
        seeds = self._search_flat(query, top, include_invalid)
        out = [(ep, sc, 0) for ep, sc in seeds]
        if not expand or not seeds:
            return out

        seen = {ep.ep_id for ep, _, _ in out}
        frontier = [(seeds[0][0], 0)]     # 只从最强的一条展开
        for hop in range(1, hops + 1):
            nxt = []
            for ep, _ in frontier:
                for cand, sc in self._expand_from(ep, include_invalid, k=2):
                    if cand.ep_id in seen:
                        continue
                    seen.add(cand.ep_id)
                    out.append((cand, sc, hop))
                    nxt.append((cand, hop))
            frontier = nxt
            if not frontier:
                break
        return out

    def _search_flat(self, query, top, include_invalid):
        w = "" if include_invalid else "AND e.invalid_at IS NULL"
        scores = {}

        # 语义（BGE）
        if self.use_semantic:
            try:
                self._load_vectors()
                if self._vec is not None and len(self._vec) > 0:
                    import numpy as np
                    qv = Embedder.get().encode(query)[0]
                    sims = self._vec @ qv
                    k = min(top * 4, len(sims))
                    idx = np.argpartition(-sims, k - 1)[:k]
                    for i in idx:
                        scores[self._vec_ids[i]] = scores.get(
                            self._vec_ids[i], 0.0) + float(sims[i]) * 2.0
            except Exception as e:
                print(f"[warn] 语义检索失败: {e}", file=sys.stderr)

        # 关键词（FTS5 多词 OR + 子串兜底）
        terms = split_query_terms(query)
        if terms:
            expr = " OR ".join(f'"{t}"' for t in terms)
            try:
                rows = self.con.execute(f"""
                    SELECT e.ep_id, bm25(ep_fts) AS s
                    FROM ep_fts JOIN episodes e ON e.rowid=ep_fts.rowid
                    WHERE ep_fts MATCH ? {w} ORDER BY s LIMIT ?""",
                    (expr, top * 6)).fetchall()
                for eid, s in rows:
                    scores[eid] = scores.get(eid, 0.0) + 1.0 / (1.0 + abs(s))
            except sqlite3.OperationalError:
                pass
            for t in terms[:8]:
                for (eid,) in self.con.execute(f"""
                        SELECT ep_id FROM episodes e
                        WHERE (raw_text LIKE ? OR atmosphere LIKE ?) {w} LIMIT ?""",
                        (f"%{t}%", f"%{t}%", top * 2)).fetchall():
                    scores[eid] = scores.get(eid, 0.0) + 0.4

        # 四要素
        el = extract_elements(query)
        for fname, vals in (("person", el["person"]), ("time", el["time"]),
                            ("location", el["location"]), ("topic", el["topic"])):
            for v in vals:
                for (eid,) in self.con.execute(f"""
                        SELECT ep_id FROM episodes e WHERE {fname} LIKE ? {w} LIMIT ?""",
                        (f"%{v}%", top * 2)).fetchall():
                    scores[eid] = scores.get(eid, 0.0) + 0.8

        # 情绪共振 + 重要性
        qa = estimate_affect_v2(query)
        for eid, v, a, imp in self.con.execute(f"""
                SELECT ep_id, valence, arousal, importance FROM episodes e
                WHERE 1=1 {w}""").fetchall():
            add = 0.0
            if qa["source"] != "none" and qa["arousal"] > 0.1 and v * qa["valence"] > 0:
                add += 0.6 * (a or 0.0)
            add += 0.25 * (imp or 0.0)
            if add:
                scores[eid] = scores.get(eid, 0.0) + add

        out = []
        for eid in sorted(scores, key=lambda k: -scores[k])[:top]:
            ep = self.get(eid)
            if ep:
                out.append((ep, round(scores[eid], 3)))
        return out

    def _expand_from(self, seed: Episode, include_invalid, k=2):
        """
        从一条情景展开：情景相似 或 情绪共振
        对应"看到夕阳 → 想起小时候的夕阳"
        """
        w = "" if include_invalid else "AND e.invalid_at IS NULL"
        cands = {}

        # (a) 情景相似：向量近（排除自己）
        if self.use_semantic:
            try:
                self._load_vectors()
                if self._vec is not None and len(self._vec) > 0:
                    import numpy as np
                    sv = Embedder.get().encode(seed.raw_text)[0]
                    sims = self._vec @ sv
                    for i in np.argsort(-sims)[:k * 3]:
                        eid = self._vec_ids[i]
                        if eid != seed.ep_id:
                            cands[eid] = cands.get(eid, 0) + float(sims[i])
            except Exception:
                pass

        # (b) 情绪共振：valence 同号且 arousal 接近（差值 < 0.3）
        if abs(seed.valence) > 0.1 or seed.arousal > 0.1:
            for eid, v, a in self.con.execute(f"""
                    SELECT ep_id, valence, arousal FROM episodes e
                    WHERE ep_id != ? {w}""", (seed.ep_id,)).fetchall():
                if v is None or a is None:
                    continue
                same_sign = (v * seed.valence > 0)
                close = abs((a or 0) - (seed.arousal or 0)) < 0.3
                if same_sign and close:
                    cands[eid] = cands.get(eid, 0) + 0.5 + 0.5 * seed.arousal

        # (c) 共同话题/场景词（"夕阳"这种）
        for tp in seed.topic:
            for (eid,) in self.con.execute(f"""
                    SELECT ep_id FROM episodes e
                    WHERE topic LIKE ? AND ep_id != ? {w} LIMIT ?""",
                    (f"%{tp}%", seed.ep_id, k * 2)).fetchall():
                cands[eid] = cands.get(eid, 0) + 0.6

        out = []
        for eid in sorted(cands, key=lambda x: -cands[x])[:k]:
            ep = self.get(eid)
            if ep:
                out.append((ep, round(cands[eid], 3)))
        return out

    def stats(self):
        n = self.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        nv = self.con.execute("SELECT COUNT(*) FROM episodes WHERE invalid_at IS NULL").fetchone()[0]
        nvec = self.con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
        hi = self.con.execute("SELECT COUNT(*) FROM episodes WHERE arousal>=0.5").fetchone()[0]
        im = self.con.execute("SELECT COUNT(*) FROM episodes WHERE importance>=0.5").fetchone()[0]
        return {"total": n, "valid": nv, "invalid": n - nv, "vectors": nvec,
                "high_arousal": hi, "important": im,
                "size_mb": round(os.path.getsize(self.path) / 1024 / 1024, 2)
                if os.path.exists(self.path) else 0}
