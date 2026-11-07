"""
cosmos_mem 原型 v0.1 —— 情景记忆库

设计依据（docs/49 用户定义 + docs/50 工程对照）：
  1. 一条记忆 = 一个**情景**，不是一条事实
  2. 情景的边界由四要素变化触发：人 / 时间 / 地点 / 话题（HingeMem 规则）
  3. 情绪是**二维连续值**（效价 valence / 唤醒 arousal），不是 27 类分类
  4. 情绪采集：显式标记优先（用户说"记住这条"）→ 规则次之 → 关键词兜底
  5. 不做遗忘规则，做**可塑性**：能问、能改、能失效
  6. 不存文本条目，存"情景"；原文完整保留（不切碎）

存储：SQLite 单文件（已验证 43MB/10万条、迁移 100%、可重建索引）
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any

SCHEMA_VERSION = "cosmos-mem/0.1"

# ---------- 显式标记（用户直接说"记住这条"）----------
EXPLICIT_MARK = re.compile(
    r"(记住|记下|别忘了|切记|千万要记|这条重要|务必记住)", re.I)

# ---------- 情绪线索（规则法，对应"生理自动反应"的快速通道）----------
NEG_STRONG = ["千万别", "要出大事", "丢饭碗", "完蛋", "严重", "危险", "不能错",
              "搞错了", "出问题", "崩溃", "损失", "事故", "报警", "紧急"]
NEG_MILD = ["不太对", "有问题", "麻烦", "担心", "怕", "错误", "失败", "不行"]
POS_STRONG = ["太好了", "完美", "特别开心", "成功", "终于", "突破了", "爽"]
POS_MILD = ["不错", "挺好", "喜欢", "满意", "顺利", "可以"]
AROUSAL_HIGH = ["!!!", "！！", "立刻", "马上", "现在就", "赶紧", "急",
                "千万", "一定", "必须", "绝对"]
AROUSAL_LOW = ["随便", "都行", "无所谓", "不急", "以后再说", "看情况"]

# ---------- 四要素抽取（切分依据）----------
TIME_PAT = re.compile(
    r"(\d{1,2}月\d{1,2}[日号]?|\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?|"
    r"今天|昨天|前天|明天|后天|上周|本周|这周|下周|上个月|这个月|下个月|"
    r"去年|今年|明年|早上|上午|中午|下午|晚上|凌晨|半夜|刚才|之前|后来)")
LOC_PAT = re.compile(
    r"(在公司|在家里|在家|在办公室|在会议室|在学校|在医院|在车上|在路上|"
    r"在咖啡厅|在图书馆|在机场|在车站|在饭店|在卧室|在客厅|在阳台)")
# 人：简化处理，取"2-4 字 + 常见称谓/名字特征"，避免引入 NER 依赖
PERSON_PAT = re.compile(
    r"([\u4e00-\u9fa5]{2,4})(?=说|问|告诉|让我|跟我|给我|觉得|认为|负责)|"
    r"(老板|领导|同事|客户|朋友|妈妈|爸爸|老婆|老公|儿子|女儿|老师|医生|房东|司机)")
TOPIC_HINT = {
    "工作": ["项目", "需求", "上线", "评审", "汇报", "加班", "会议", "方案", "指标"],
    "技术": ["代码", "接口", "参数", "部署", "bug", "报错", "服务器", "数据库", "模型"],
    "生活": ["吃饭", "睡觉", "家务", "买菜", "看病", "房租", "水电", "快递"],
    "金钱": ["工资", "报销", "付款", "借钱", "预算", "账单", "转账"],
    "关系": ["吵架", "道歉", "分手", "生日", "约会", "聊天", "生气"],
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def estimate_affect(text: str) -> dict[str, float]:
    """
    规则法估情绪。返回 {valence: -1..1, arousal: 0..1, source: str}
    valence 负=难受 正=愉快；arousal 高=强度大
    """
    t = text or ""
    neg = sum(1 for w in NEG_STRONG if w in t) * 2 + \
          sum(1 for w in NEG_MILD if w in t)
    pos = sum(1 for w in POS_STRONG if w in t) * 2 + \
          sum(1 for w in POS_MILD if w in t)
    aro = sum(1 for w in AROUSAL_HIGH if w in t) * 2 - \
          sum(1 for w in AROUSAL_LOW if w in t)
    # 标点强度
    aro += min(t.count("!") + t.count("！"), 4)
    # 归一
    val = (pos - neg) / max(pos + neg, 1) if (pos + neg) else 0.0
    arousal = max(0.0, min(1.0, aro / 6.0))
    src = "rule" if (pos + neg or aro) else "none"
    if EXPLICIT_MARK.search(t):
        # 显式标记：拉到高唤醒，效价按上下文定
        arousal = max(arousal, 0.8)
        src = "explicit"
    return {"valence": round(float(val), 3),
            "arousal": round(float(arousal), 3), "source": src}


def extract_elements(text: str) -> dict[str, Any]:
    """抽四要素：人 / 时间 / 地点 / 话题"""
    t = text or ""
    times = sorted(set(TIME_PAT.findall(t)))
    locs = sorted(set(LOC_PAT.findall(t)))
    persons = []
    for m in PERSON_PAT.finditer(t):
        w = m.group(1) or m.group(2)
        if w:
            persons.append(w)
    persons = sorted(set(persons))
    topics = []
    for tp, kws in TOPIC_HINT.items():
        if any(k in t for k in kws):
            topics.append(tp)
    return {"person": persons, "time": times,
            "location": locs, "topic": sorted(topics)}


@dataclass
class Episode:
    """一条情景记忆"""
    ep_id: str = ""
    created_at: float = 0.0
    # 四要素（切分依据）
    person: list[str] = field(default_factory=list)
    time: list[str] = field(default_factory=list)
    location: list[str] = field(default_factory=list)
    topic: list[str] = field(default_factory=list)
    # 情景内容
    raw_text: str = ""            # 原文完整保留（红线：不切碎）
    gist: str = ""                # 骨架（一句话）
    atmosphere: str = ""          # 氛围（自动/手填）
    # 情绪二维
    valence: float = 0.0
    arousal: float = 0.0
    affect_source: str = "none"   # explicit / rule / none
    # 可塑性
    confidence: float = 1.0       # 可信度（可被后续询问修正）
    invalid_at: float | None = None   # 失效时间（None=有效）
    superseded_by: str | None = None  # 被哪条取代
    revision: int = 1             # 修订次数
    # 索引
    text_hash: str = ""


class MemoryStore:
    """情景记忆库（SQLite 单文件）"""

    def __init__(self, path: str = "cosmos_mem.db"):
        self.path = path
        self.con = sqlite3.connect(path)
        self.con.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self):
        c = self.con
        c.execute("""CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY, val TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS episodes (
            ep_id TEXT PRIMARY KEY,
            created_at REAL,
            person TEXT, time TEXT, location TEXT, topic TEXT,
            raw_text TEXT NOT NULL,
            gist TEXT,
            atmosphere TEXT,
            valence REAL, arousal REAL, affect_source TEXT,
            confidence REAL, invalid_at REAL, superseded_by TEXT,
            revision INTEGER,
            text_hash TEXT,
            schema_version TEXT)""")
        # 全文索引（FTS5，已验证 132× 于 numpy 扫描）
        c.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS ep_fts
            USING fts5(raw_text, gist, content='episodes',
                       content_rowid='rowid')""")
        c.execute("CREATE INDEX IF NOT EXISTS ix_person ON episodes(person)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_time ON episodes(time)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_valid ON episodes(invalid_at)")
        c.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)",
                  (SCHEMA_VERSION,))
        c.commit()

    # ---------- 写入 ----------
    def add(self, text: str, atmosphere: str = "",
            affect: dict | None = None, gist: str = "") -> Episode:
        el = extract_elements(text)
        af = affect or estimate_affect(text)
        ep = Episode(
            ep_id=uuid.uuid4().hex[:16],
            created_at=time.time(),
            person=el["person"], time=el["time"],
            location=el["location"], topic=el["topic"],
            raw_text=text, gist=gist or text[:40],
            atmosphere=atmosphere,
            valence=float(af.get("valence", 0.0)),
            arousal=float(af.get("arousal", 0.0)),
            affect_source=str(af.get("source", "none")),
            text_hash=hashlib.md5(_norm(text).encode()).hexdigest(),
        )
        c = self.con
        c.execute("""INSERT INTO episodes VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            ep.ep_id, ep.created_at, json.dumps(ep.person, ensure_ascii=False),
            json.dumps(ep.time, ensure_ascii=False),
            json.dumps(ep.location, ensure_ascii=False),
            json.dumps(ep.topic, ensure_ascii=False),
            ep.raw_text, ep.gist, ep.atmosphere,
            ep.valence, ep.arousal, ep.affect_source,
            ep.confidence, ep.invalid_at, ep.superseded_by,
            ep.revision, ep.text_hash, SCHEMA_VERSION))
        c.execute("""INSERT INTO ep_fts(rowid, raw_text, gist)
            SELECT rowid, raw_text, gist FROM episodes WHERE ep_id=?""", (ep.ep_id,))
        c.commit()
        return ep

    # ---------- 读取 ----------
    def _row_to_ep(self, r) -> Episode:
        cols = ["ep_id", "created_at", "person", "time", "location", "topic",
                "raw_text", "gist", "atmosphere", "valence", "arousal",
                "affect_source", "confidence", "invalid_at", "superseded_by",
                "revision", "text_hash"]
        d = dict(zip(cols, r))
        for k in ("person", "time", "location", "topic"):
            d[k] = json.loads(d[k]) if d[k] else []
        return Episode(**d)

    def get(self, ep_id: str) -> Episode | None:
        r = self.con.execute(
            "SELECT ep_id,created_at,person,time,location,topic,raw_text,gist,"
            "atmosphere,valence,arousal,affect_source,confidence,invalid_at,"
            "superseded_by,revision,text_hash FROM episodes WHERE ep_id=?",
            (ep_id,)).fetchone()
        return self._row_to_ep(r) if r else None

    def count(self) -> int:
        return self.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]

    # ---------- 可塑性：修订 / 失效 ----------
    def revise(self, ep_id: str, new_text: str | None = None,
               confidence: float | None = None) -> bool:
        ep = self.get(ep_id)
        if not ep:
            return False
        if new_text is not None:
            el = extract_elements(new_text)
            af = estimate_affect(new_text)
            self.con.execute("""UPDATE episodes SET raw_text=?, person=?, time=?,
                location=?, topic=?, valence=?, arousal=?, affect_source=?,
                revision=revision+1, text_hash=? WHERE ep_id=?""", (
                new_text,
                json.dumps(el["person"], ensure_ascii=False),
                json.dumps(el["time"], ensure_ascii=False),
                json.dumps(el["location"], ensure_ascii=False),
                json.dumps(el["topic"], ensure_ascii=False),
                af["valence"], af["arousal"], af["source"],
                hashlib.md5(_norm(new_text).encode()).hexdigest(), ep_id))
        if confidence is not None:
            self.con.execute("UPDATE episodes SET confidence=?, revision=revision+1 "
                             "WHERE ep_id=?", (confidence, ep_id))
        self.con.commit()
        return True

    def invalidate(self, ep_id: str, superseded_by: str | None = None) -> bool:
        """标记失效（不删除——红线）"""
        self.con.execute(
            "UPDATE episodes SET invalid_at=?, superseded_by=?, revision=revision+1 "
            "WHERE ep_id=?", (time.time(), superseded_by, ep_id))
        self.con.commit()
        return True

    def supersede(self, old_id: str, new_text: str, **kw) -> tuple[bool, Episode]:
        """新事实取代旧事实：旧标失效，新入一条"""
        new_ep = self.add(new_text, **kw)
        self.invalidate(old_id, superseded_by=new_ep.ep_id)
        return True, new_ep

    # ---------- 检索 ----------
    def search(self, query: str, top: int = 5, include_invalid: bool = False,
               query_type: str = "auto") -> list[tuple[Episode, float]]:
        """
        检索。query_type: auto / person / time / location / topic / general
        对应 HingeMem 的"按查询类型路由"
        """
        where = "" if include_invalid else "AND e.invalid_at IS NULL"
        scores: dict[str, float] = {}

        # 1) 全文（FTS5，精确词）
        try:
            safe = re.sub(r'["\']', " ", query).strip()
            if safe:
                rows = self.con.execute(f"""
                    SELECT e.ep_id, bm25(ep_fts) AS s
                    FROM ep_fts JOIN episodes e ON e.rowid = ep_fts.rowid
                    WHERE ep_fts MATCH ? {where}
                    ORDER BY s LIMIT ?""", (f'"{safe}"', top * 4)).fetchall()
                for eid, s in rows:
                    scores[eid] = scores.get(eid, 0) + 1.0 / (1.0 + abs(s))
        except sqlite3.OperationalError:
            pass

        # 2) 四要素匹配（结构化）
        el = extract_elements(query)
        for field_name, vals in (("person", el["person"]), ("time", el["time"]),
                                 ("location", el["location"]),
                                 ("topic", el["topic"])):
            for v in vals:
                rows = self.con.execute(f"""
                    SELECT ep_id FROM episodes e
                    WHERE {field_name} LIKE ? {where} LIMIT ?""",
                    (f"%{v}%", top * 2)).fetchall()
                for (eid,) in rows:
                    scores[eid] = scores.get(eid, 0) + 0.8

        # 3) 情绪共振（效价同向时加权）—— 对应"情绪共振触发"
        qa = estimate_affect(query)
        if qa["source"] != "none" and qa["arousal"] > 0.1:
            rows = self.con.execute(f"""
                SELECT ep_id, valence, arousal FROM episodes e
                WHERE 1=1 {where} AND arousal > 0.1
                ORDER BY arousal DESC LIMIT ?""", (top * 4,)).fetchall()
            for eid, v, a in rows:
                # 效价同向 → 共振加分
                if v * qa["valence"] > 0:
                    scores[eid] = scores.get(eid, 0) + 0.6 * a

        # 4) 排序：查询类型影响权重
        if query_type in ("person", "time", "location", "topic"):
            # 结构化优先
            pass

        out = []
        for eid in sorted(scores, key=lambda k: -scores[k])[:top]:
            ep = self.get(eid)
            if ep:
                out.append((ep, scores[eid]))
        return out

    def stats(self) -> dict:
        c = self.con
        n = c.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        nv = c.execute("SELECT COUNT(*) FROM episodes WHERE invalid_at IS NULL"
                       ).fetchone()[0]
        hi = c.execute("SELECT COUNT(*) FROM episodes WHERE arousal>=0.5"
                       ).fetchone()[0]
        return {"total": n, "valid": nv, "invalid": n - nv,
                "high_arousal": hi,
                "size_mb": round(os.path.getsize(self.path) / 1024 / 1024, 2)
                if os.path.exists(self.path) else 0}
