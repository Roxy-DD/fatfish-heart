"""
store v1.0 —— 双维度重要性 + 情景触发 + 严格红线

依据 E-61/E-62 实测：
  「cost（失去代价）」与「affect（情绪）」是**两个几乎无关的维度**
    · T触发 × C代价 = +0.060（几乎无关）
    · 硬事实：cost 7.0 / affect 1.07
    · 创伤美好：cost 3-4 / affect 2.6-2.9
  「T触发 × F频率」= −0.695（强负相关：强烈的少发生，高频的没情绪）

→ 不能再用一个 importance 分数，必须两个正交维度：
    cost        决定"必须记"     → 固定席位（永远进上下文）
    affect_peak 决定"记得牢/易被唤起" → 情景触发时优先

红线（三份文档已立）：
  R1 迁移 100%（逐条比对，偏差必须为 0）
  R2 能力不降（检索召回不低于前版）
  R3 索引可确定性重建
  R4 数据不丢（不删除，只标失效）
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
from fixes import extract_person_fixed, split_query_terms, scene_words_v2

SCHEMA_VERSION = "cosmos-mem/1.0"
MODEL_DIR = r"C:\Users\xuee\Desktop\肥鱼之心\models\bge-small-zh"

TIME_PAT = re.compile(
    r"(\d{1,2}月\d{1,2}[日号]?|\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?|"
    r"今天|昨天|前天|明天|后天|上周|本周|这周|下周|上个月|这个月|下个月|"
    r"去年|今年|明年|早上|上午|中午|下午|傍晚|晚上|凌晨|半夜|刚才|之前|后来|"
    r"小时候|童年|以前|当年)")
LOC_PAT = re.compile(
    r"(在公司|在家里|在家|在办公室|在会议室|在学校|在医院|在车上|在路上|"
    r"在咖啡厅|在图书馆|在机场|在车站|在饭店|在卧室|在客厅|在阳台)")

# ---------- cost：失去代价的规则信号 ----------
COST_SIGNALS = {
    # 生存/安全
    "密码": 0.9, "凭证": 0.9, "钥匙": 0.8, "身份证": 0.9, "账号": 0.7,
    "住址": 0.8, "地址": 0.6, "电话": 0.5, "紧急": 0.9, "医院": 0.8,
    "手术": 0.9, "药": 0.7, "过敏": 0.9,
    # 不可逆损失
    "截止": 0.8, "期限": 0.8, "逾期": 0.8, "违约": 0.9, "合同": 0.8,
    "工资": 0.7, "报销": 0.6, "转账": 0.7, "钱": 0.6,
    # 关系/身份
    "生日": 0.7, "结婚": 0.8, "答应": 0.6, "承诺": 0.7, "约定": 0.6,
    "道歉": 0.5, "妈妈": 0.6, "爸爸": 0.6, "孩子": 0.7, "老婆": 0.7, "老公": 0.7,
    # 工作关键
    "不能改": 0.8, "必须": 0.7, "一定": 0.6, "定的是": 0.7, "最终": 0.6,
    "参数": 0.5, "配置": 0.6, "根因": 0.7, "原因": 0.5,
    # E-110 补充：工作方式约定类（实测这类 cost 判 0.0，但实际很该记）
    "要求": 0.65, "约定": 0.7, "标准": 0.65, "规范": 0.6, "原则": 0.65,
    "流程": 0.6, "方式": 0.55, "习惯": 0.55, "偏好": 0.6, "喜欢": 0.5,
    "不要": 0.6, "禁止": 0.75, "只能": 0.6, "务必": 0.7, "优先": 0.6,
    "以实验数据为准": 0.85, "以数据为准": 0.85, "实验数据": 0.6,
    "先自己尝试": 0.7, "统一提问": 0.6, "统一询问": 0.6,
}

# ---------- affect_peak：触发时的情绪强度 ----------
AFFECT_STRONG = ["千万别", "出大事", "丢饭碗", "崩溃", "完蛋", "害怕", "恐惧",
                 "生气", "愤怒", "特别开心", "太好", "终于", "激动", "想哭",
                 "心疼", "难受", "后悔", "舍不得", "怀念"]
AFFECT_MILD = ["不错", "挺好", "麻烦", "担心", "有点", "遗憾", "可惜"]

# 触发场景（对应 E-62 的 trigger）：具体物象
TRIGGER_SCENE = {
    "被狗咬": ["狗", "大型犬", "遛狗"],
    "当众出丑": ["发言", "台上", "演讲", "点名"],
    "亲人去世": ["医院", "消毒水", "病房", "葬礼"],
    "失利": ["出分", "考试", "成绩"],
    "被批评": ["批评", "开会", "点名"],
    "走丢": ["走丢", "人多", "商场"],
    "夕阳": ["夕阳", "日落", "橘红", "晚霞"],
    "毕业": ["毕业", "学位服", "毕业照"],
    "第一次工资": ["工资", "请客", "发薪"],
    "约会": ["约会", "那条街"],
    "孩子学说话": ["叫爸爸", "叫妈妈", "学说话"],
    "通宵聊天": ["通宵", "深夜", "老友"],
}


def _norm(s):
    return re.sub(r"\s+", "", s or "")


def estimate_cost(text: str, explicit: bool = False) -> float:
    """cost：失去代价（0-1）"""
    t = text or ""
    best = 0.0
    for kw, w in COST_SIGNALS.items():
        if kw in t:
            best = max(best, w)
    if explicit:
        best = max(best, 0.85)
    return round(float(best), 3)


def estimate_affect_peak(text: str) -> dict:
    """affect_peak：触发时情绪强度（0-1）+ 效价方向"""
    t = text or ""
    strong = sum(1 for w in AFFECT_STRONG if w in t)
    mild = sum(1 for w in AFFECT_MILD if w in t)
    # 负向词判断（用于 valence）
    NEG = ["千万别", "出大事", "丢饭碗", "崩溃", "完蛋", "害怕", "恐惧",
           "生气", "愤怒", "难受", "后悔", "疼", "失败", "错", "危险"]
    POS = ["开心", "太好", "终于", "激动", "喜欢", "满意", "顺利", "成功",
           "温暖", "怀念", "幸福"]
    neg = sum(1 for w in NEG if w in t)
    pos = sum(1 for w in POS if w in t)

    peak = min(1.0, (strong * 0.35 + mild * 0.12 +
                     min(t.count("!") + t.count("！"), 3) * 0.08))
    val = 0.0
    if pos + neg > 0:
        val = (pos - neg) / (pos + neg)
    return {"peak": round(float(peak), 3), "valence": round(float(val), 3)}


def detect_triggers(text: str) -> list[str]:
    """识别这条记忆对应哪些触发场景"""
    t = text or ""
    out = []
    for scene, kws in TRIGGER_SCENE.items():
        if any(k in t for k in kws):
            out.append(scene)
    return out


# ============================================================
# 英文实体抽取（E-168 实测：97.2% 的 LoCoMo 问题含人名，
#   而原 extract_elements 只支持中文正则会返回空 → 结构化通道完全失效）
# ============================================================
# 英文人名：首字母大写单词（排除句首常见词，且不在停用词表里）
_EN_STOP_WORDS = set("""
a an the and or but if then than that this these those
is are was were be been being am do does did doing done
have has had having will would shall should can could may might must
i you he she it we they me him her us them my your his its our their
of in on at to from by with without for about as into over under
what which who whom whose when where why how
not no yes so such very just also too only more most much many
there here all any some each every both few other another
okay ok yeah yep nope oh wow hey hi hello thanks thank please
let lets gonna wanna gotta im ive id youd youve thats whats
""".split())

_EN_MONTHS = ("january february march april may june july august "
              "september october november december").split()
_EN_DAYS = ("monday tuesday wednesday thursday friday saturday sunday").split()

_EN_TIME_PAT = re.compile(
    r"\b(?:\d{4}|\d{1,2}[:.]\d{2}\s*(?:am|pm)?|"
    r"last\s+(?:week|month|year|night|weekend|monday|tuesday|wednesday|"
    r"thursday|friday|saturday|sunday)|"
    r"next\s+(?:week|month|year|weekend)|"
    r"yesterday|today|tomorrow|tonight|"
    r"(?:the\s+)?(?:day\s+)?before\s+\w+|after\s+\w+|"
    r"\d+\s+(?:days?|weeks?|months?|years?|hours?)\s+(?:ago|later)|"
    r"(?:" + "|".join(_EN_MONTHS) + r")"
    r"(?:\s+\d{1,2}(?:st|nd|rd|th)?)?(?:,?\s*\d{4})?|"
    r"\d{1,2}(?:st|nd|rd|th)?\s+(?:" + "|".join(_EN_MONTHS) + r")"
    r"(?:\s+\d{4})?)\b", re.I)

_EN_LOC_PAT = re.compile(
    r"\b(?:at|in|to|from|near)\s+"
    r"((?:the\s+)?[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b")


def extract_person_en(text: str) -> list[str]:
    """
    英文人名抽取：首字母大写的连续词，排除句首与停用词。

    这不是完美的 NER，但对 LoCoMo 这种"两三个固定说话人"的场景足够。
    真正的通用 NER 需要模型，属于后续优化。
    """
    t = text or ""
    out = []
    # 找到所有首字母大写的词串
    for m in re.finditer(r"\b[A-Z][a-z]{1,15}(?:\s+[A-Z][a-z]{1,15})?\b", t):
        w = m.group(0)
        # 排除停用词、月份、星期
        lw = w.lower()
        if lw in _EN_STOP_WORDS:
            continue
        if lw in [x.lower() for x in _EN_MONTHS]:
            continue
        if lw in [x.lower() for x in _EN_DAYS]:
            continue
        # 排除句首（前一个非空白字符是句号/问号/感叹号/开头）
        start = m.start()
        prefix = t[:start].rstrip()
        at_sentence_start = (not prefix) or prefix[-1] in ".!?\n"
        if at_sentence_start and " " not in w:
            # 句首单词可能是普通名词，只在多次出现时才收
            continue
        if w not in out:
            out.append(w)
    return out[:6]


def extract_elements(text):
    """
    抽取人物 / 时间 / 地点。

    E-167/E-168 修正：原实现只有中文正则，英文输入全返回空，
    导致检索的结构化通道（命中加 0.8 分）在英文语料上完全失效。
    实测 LoCoMo 有 97.2% 的问题含人名，51.3% 的失败题含人名或时间词。
    """
    t = text or ""
    person = list(extract_person_fixed(t))
    for p in extract_person_en(t):
        if p not in person:
            person.append(p)

    time_vals = set(TIME_PAT.findall(t))
    for m in _EN_TIME_PAT.finditer(t):
        v = m.group(0).strip()
        if v:
            time_vals.add(v)

    loc_vals = set(LOC_PAT.findall(t))
    for m in _EN_LOC_PAT.finditer(t):
        v = m.group(1).strip()
        if v and v.lower() not in _EN_STOP_WORDS:
            loc_vals.add(v)

    return {"person": person[:8],
            "time": sorted(time_vals)[:8],
            "location": sorted(loc_vals)[:8],
            "topic": []}


class Embedder:
    _inst = None
    def __init__(self):
        self._tok = None; self._mdl = None
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
        import numpy as np, torch
        if isinstance(texts, str):
            texts = [texts]
        self._load()
        with torch.no_grad():
            e = self._tok(texts, padding=True, truncation=True,
                          max_length=128, return_tensors="pt")
            o = self._mdl(**e)
        v = o.last_hidden_state[:, 0].numpy().astype("float32")
        n = np.linalg.norm(v, axis=1, keepdims=True); n[n == 0] = 1
        return v / n


@dataclass
class Episode:
    ep_id: str = ""
    created_at: float = 0.0
    person: list = field(default_factory=list)
    time: list = field(default_factory=list)
    location: list = field(default_factory=list)
    triggers: list = field(default_factory=list)
    raw_text: str = ""
    atmosphere: str = ""
    # ---- 双维度重要性 ----
    cost: float = 0.0          # 失去代价 → 固定席位
    affect_peak: float = 0.0   # 触发强度 → 易被唤起
    valence: float = 0.0       # 情绪方向
    # ---- 可塑性 ----
    confidence: float = 1.0
    invalid_at: float | None = None
    superseded_by: str | None = None
    revision: int = 1
    text_hash: str = ""


COLS = ["ep_id", "created_at", "person", "time", "location", "triggers",
        "raw_text", "atmosphere", "cost", "affect_peak", "valence",
        "confidence", "invalid_at", "superseded_by", "revision", "text_hash"]


class MemoryStore:
    def __init__(self, path="mem.db", use_semantic=True):
        self.path = path
        self.use_semantic = use_semantic
        self.con = sqlite3.connect(path)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.invalidate_vector_cache()
        self._init()

    def _init(self):
        c = self.con
        c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, val TEXT)")
        c.execute("""CREATE TABLE IF NOT EXISTS episodes (
            ep_id TEXT PRIMARY KEY, created_at REAL,
            person TEXT, time TEXT, location TEXT, triggers TEXT,
            raw_text TEXT NOT NULL, atmosphere TEXT,
            cost REAL, affect_peak REAL, valence REAL,
            confidence REAL, invalid_at REAL, superseded_by TEXT,
            revision INTEGER, text_hash TEXT, schema_version TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS vectors (
            ep_id TEXT PRIMARY KEY, dim INTEGER, vec BLOB)""")
        c.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS ep_fts
            USING fts5(raw_text, atmosphere, content='episodes',
                       content_rowid='rowid')""")
        for f in ("person", "time", "location"):
            c.execute(f"CREATE INDEX IF NOT EXISTS ix_{f} ON episodes({f})")
        c.execute("CREATE INDEX IF NOT EXISTS ix_valid ON episodes(invalid_at)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_cost ON episodes(cost)")
        c.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)",
                  (SCHEMA_VERSION,))
        c.commit()

    # ---------- 写入 ----------
    def add(self, text, atmosphere="", explicit=False, encode=True):
        el = extract_elements(text)
        cost = estimate_cost(text, explicit)
        af = estimate_affect_peak(text)
        tg = detect_triggers(text) or detect_triggers(atmosphere)
        ep = Episode(ep_id=uuid.uuid4().hex[:16], created_at=time.time(),
                     person=el["person"], time=el["time"],
                     location=el["location"], triggers=tg,
                     raw_text=text, atmosphere=atmosphere,
                     cost=cost, affect_peak=af["peak"], valence=af["valence"],
                     text_hash=hashlib.md5(_norm(text).encode()).hexdigest())
        vals = [ep.ep_id, ep.created_at,
                json.dumps(ep.person, ensure_ascii=False),
                json.dumps(ep.time, ensure_ascii=False),
                json.dumps(ep.location, ensure_ascii=False),
                json.dumps(ep.triggers, ensure_ascii=False),
                ep.raw_text, ep.atmosphere, ep.cost, ep.affect_peak, ep.valence,
                ep.confidence, ep.invalid_at, ep.superseded_by,
                ep.revision, ep.text_hash, SCHEMA_VERSION]
        self.con.execute(f"INSERT INTO episodes VALUES ({','.join('?'*len(vals))})", vals)
        self.con.execute("""INSERT INTO ep_fts(rowid, raw_text, atmosphere)
            SELECT rowid, raw_text, atmosphere FROM episodes WHERE ep_id=?""",
            (ep.ep_id,))
        if encode and self.use_semantic:
            try:
                v = Embedder.get().encode(text)[0]
                self.con.execute("INSERT OR REPLACE INTO vectors VALUES (?,?,?)",
                                 (ep.ep_id, int(len(v)), v.tobytes()))
                self.invalidate_vector_cache()
            except Exception as e:
                print(f"[warn] 编码失败: {e}", file=sys.stderr)
        self.con.commit()
        return ep

    def _ep(self, r):
        d = dict(zip(COLS, r))
        for k in ("person", "time", "location", "triggers"):
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
        # 分层保护：L0（不可变）连修订都不允许
        try:
            import hook as _hook
            if not _hook.can_revise(self, ep_id):
                return False
        except Exception:
            pass
        if new_text is not None:
            el = extract_elements(new_text)
            cost = estimate_cost(new_text)
            af = estimate_affect_peak(new_text)
            tg = detect_triggers(new_text)
            self.con.execute("""UPDATE episodes SET raw_text=?, person=?, time=?,
                location=?, triggers=?, cost=?, affect_peak=?, valence=?,
                revision=revision+1, text_hash=? WHERE ep_id=?""",
                (new_text, json.dumps(el["person"], ensure_ascii=False),
                 json.dumps(el["time"], ensure_ascii=False),
                 json.dumps(el["location"], ensure_ascii=False),
                 json.dumps(tg, ensure_ascii=False),
                 cost, af["peak"], af["valence"],
                 hashlib.md5(_norm(new_text).encode()).hexdigest(), ep_id))
            if self.use_semantic:
                try:
                    v = Embedder.get().encode(new_text)[0]
                    self.con.execute("INSERT OR REPLACE INTO vectors VALUES (?,?,?)",
                                     (ep_id, int(len(v)), v.tobytes()))
                    self.invalidate_vector_cache()
                except Exception:
                    pass
        if confidence is not None:
            self.con.execute("UPDATE episodes SET confidence=?, revision=revision+1 "
                             "WHERE ep_id=?", (confidence, ep_id))
        self.con.commit()
        return True

    def invalidate(self, ep_id, superseded_by=None):
        # 分层保护（docs/72，用户要求 2026-11-07）：
        # L0 不可变 / L1 常驻准则 / L2 设计基石 全部禁止失效。
        try:
            import hook as _hook
            if not _hook.can_invalidate(self, ep_id):
                return False
        except Exception:
            pass
        self.con.execute("UPDATE episodes SET invalid_at=?, superseded_by=?, "
                         "revision=revision+1 WHERE ep_id=?",
                         (time.time(), superseded_by, ep_id))
        self.con.commit()
        # 失效改变了"有效记忆集合"，必须让向量缓存失效，
        # 否则常驻进程的语义通道仍会返回已失效记录（E-133 实测的 bug）
        self.invalidate_vector_cache()
        return True

    def supersede(self, old_id, new_text, **kw):
        # 分层保护：L0/L1/L2 不可被取代
        try:
            import hook as _hook
            if not _hook.can_invalidate(self, old_id):
                return False, None
        except Exception:
            pass
        ne = self.add(new_text, **kw)
        self.invalidate(old_id, superseded_by=ne.ep_id)
        return True, ne

    # ---------- 向量 ----------
    def invalidate_vector_cache(self):
        """
        作废向量缓存。

        必须同时清 _vec 和 _vec_cache —— E-126 踩的真坑：
        原代码只写 self._vec = None，但 _load_vectors 现在优先查 _vec_cache，
        于是缓存命中后直接返回旧矩阵，新写入的记忆根本不在里面，
        导致矛盾判定的候选包看不到刚写的记忆。
        """
        self._vec = None
        self._vec_ids = []
        if hasattr(self, "_vec_cache"):
            self._vec_cache.clear()

    def _load_vectors(self, include_invalid: bool = False):
        """
        载入向量矩阵。

        E-117b 修的一个真 bug：原实现无条件只载有效记忆，
        导致 include_invalid=True 时语义通道仍然看不到失效记忆，
        而语义是召回主通道，于是"查历史"实际失效。

        现在按 include_invalid 分开缓存两份；任何写入/失效都必须调
        invalidate_vector_cache() 让缓存失效。
        """
        import numpy as np

        # 缓存键：0=仅有效，1=含失效
        if not hasattr(self, "_vec_cache"):
            self._vec_cache = {}
        key = 1 if include_invalid else 0
        if key in self._vec_cache:
            self._vec, self._vec_ids = self._vec_cache[key]
            return

        sql = """
            SELECT v.ep_id, v.vec FROM vectors v JOIN episodes e ON e.ep_id=v.ep_id"""
        if not include_invalid:
            sql += " WHERE e.invalid_at IS NULL"

        rows = self.con.execute(sql).fetchall()
        if not rows:
            self._vec, self._vec_ids = np.zeros((0, 512), "float32"), []
        else:
            self._vec_ids = [r[0] for r in rows]
            self._vec = np.vstack([np.frombuffer(r[1], dtype="float32") for r in rows])

        self._vec_cache[key] = (self._vec, self._vec_ids)

    def rebuild_vectors(self):
        rows = self.con.execute("SELECT ep_id, raw_text FROM episodes").fetchall()
        if not rows:
            return 0
        V = Embedder.get().encode([r[1] for r in rows])
        for (eid, _), v in zip(rows, V):
            self.con.execute("INSERT OR REPLACE INTO vectors VALUES (?,?,?)",
                             (eid, int(len(v)), v.tobytes()))
        self.invalidate_vector_cache()
        return len(rows)

    # ---------- 检索（双通道）----------
    def search(self, query, top=5, include_invalid=False,
               expand=False, hops=2, fixed_slots=0, familiarity=0.0,
               structured=False, sem_pool=0, like_pool=0,
               rerank=False, rerank_pool=0):
        """
        fixed_slots: 预留多少个席位给 cost 最高的记忆（"永远在场"）

        familiarity: 熟练度加权系数（E-146 实测标定，默认 0 = 不启用）
          0    = 不参与排序（基线）
          >0   = 熟练度加成，值越大加成越强
          具体取值和是否启用，由 A/B 实验决定；这里只提供旋钮。

        structured: 是否启用结构化通道（person/time/location 命中加 0.8）
          E-169/E-172 实测：在实体高频语料（LoCoMo）上是负收益，
          开 0.5495 / 关 0.5919；逐通道测试关掉 +8.61pp。
          → 默认 False。需要时显式开启。

        sem_pool / like_pool: 候选池大小（0 = 用默认 top*4 / top*2）
          E-174 实测：top 同时控制候选池与最终截断，放大会让噪声被抬进前 10。
          这两个参数用于把"池大小"与"返回条数"解耦，便于实验隔离。
          生产使用保持 0（现状行为）。

        rerank: 是否用 cross-encoder（bge-reranker-v2-m3 fp16 GPU）对结果重排。
          E-207 实测：GPU 上 156ms/题，命中率 78.3%。
          E-208 实测：与 LLM 精排融合后命中率 88.1%（LLM 79.1% ∪ CE 78.3%）。
          默认 False（保持基线行为）。开启时对候选池做 cross-encoder 精排。
        rerank_pool: cross-encoder 精排的候选池大小（0 = 用 top*3，即多取几条再精排）
        """
        w = "" if include_invalid else "AND e.invalid_at IS NULL"
        scores = {}

        # (1) 语义
        if self.use_semantic:
            try:
                import numpy as np
                self._load_vectors(include_invalid=include_invalid)
                if self._vec is not None and len(self._vec):
                    qv = Embedder.get().encode(query)[0]
                    sims = self._vec @ qv
                    # 候选池大小：默认 top*4（现状）。
                    # E-174 实测：top 同时控制这个池与最终截断，
                    # 放大 top 会让噪声记忆被抬进前 10（top=50 时降到 0.32）。
                    # 这里用独立参数语义池，便于实验隔离。
                    k = min(sem_pool if sem_pool > 0 else top * 4,
                            len(sims))
                    for i in np.argpartition(-sims, k - 1)[:k]:
                        scores[self._vec_ids[i]] = float(sims[i]) * 2.0
            except Exception as e:
                print(f"[warn] 语义失败: {e}", file=sys.stderr)

        # (2) 关键词
        terms = split_query_terms(query)
        if terms:
            expr = " OR ".join(f'"{t}"' for t in terms)
            # E-26 实测：原写法 JOIN episodes 回表 69.79ms（2000条），
            # 占整个检索 95%。改成两段式（先 FTS 拿 rowid+bm25，再 IN 查 ep_id）
            # 后降到 0.80ms，提速 87 倍。
            # 根因：ep_fts 是 external content 表，JOIN 时 SQLite 要回表读
            # raw_text 重建索引内容，开销 O(命中行 × 文本长度)。
            try:
                fts_rows = self.con.execute(
                    "SELECT rowid, bm25(ep_fts) AS s FROM ep_fts "
                    "WHERE ep_fts MATCH ? ORDER BY s LIMIT ?",
                    (expr, top * 6)).fetchall()
                if fts_rows:
                    rids = [r[0] for r in fts_rows]
                    smap = {r[0]: r[1] for r in fts_rows}
                    ph = ",".join("?" * len(rids))
                    for (eid, rid) in self.con.execute(
                            f"SELECT ep_id, rowid FROM episodes e "
                            f"WHERE rowid IN ({ph}) {w}", rids).fetchall():
                        s = smap.get(rid, 0.0)
                        scores[eid] = scores.get(eid, 0) + 1.0 / (1 + abs(s))
            except sqlite3.OperationalError:
                pass
            for t in terms[:8]:
                lp = like_pool if like_pool > 0 else top * 2
                for (eid,) in self.con.execute(f"""
                        SELECT ep_id FROM episodes e
                        WHERE (raw_text LIKE ? OR atmosphere LIKE ?) {w} LIMIT ?""",
                        (f"%{t}%", f"%{t}%", lp)).fetchall():
                    scores[eid] = scores.get(eid, 0) + 0.4

        # (3) 结构化（E-169/E-172 实测：默认关闭）
        #   证据1（E-169 A/B，3 个对话）：开 0.5495 / 关 0.5919 → 开更差 4.24pp
        #   证据2（E-172 逐通道，2 个对话）：关掉结构化 +8.61pp
        #   根因：LoCoMo 里说话人名字覆盖率 100%，"命中 person +0.8" 对所有记忆
        #         近似等值加分，失去区分度，反而抬高含多实体长文本的分数。
        #   IDF 加权（E-170）只比关闭高 0.20pp，不值得增加复杂度。
        #   → 结论：默认关闭；需要时可通过 structured=True 显式开启。
        if structured:
            el = extract_elements(query)
            for fname, vals in (("person", el["person"]),
                                ("time", el["time"]),
                                ("location", el["location"])):
                for v in vals:
                    for (eid,) in self.con.execute(f"""
                            SELECT ep_id FROM episodes e WHERE {fname} LIKE ? {w} LIMIT ?""",
                            (f"%{v}%", top * 2)).fetchall():
                        scores[eid] = scores.get(eid, 0) + 0.8

        # (4) 情景触发（affect_peak 高 + 触发场景命中）
        q_trig = set(detect_triggers(query))
        q_aff = estimate_affect_peak(query)
        for eid, trigs, peak, val in self.con.execute(f"""
                SELECT ep_id, triggers, affect_peak, valence FROM episodes e
                WHERE 1=1 {w}""").fetchall():
            tl = json.loads(trigs) if trigs else []
            add = 0.0
            # 触发场景命中 → 情绪唤醒（"看到狗就想起被咬"）
            if q_trig and (q_trig & set(tl)):
                add += 0.8 + 0.7 * (peak or 0)
            # 情绪共振
            if q_aff["peak"] > 0.15 and val * q_aff["valence"] > 0:
                add += 0.5 * (peak or 0)
            if add:
                scores[eid] = scores.get(eid, 0) + add

        # (5) 固定席位：cost 最高的 memory 永远在场（不参与竞争）
        fixed = []
        if fixed_slots > 0:
            fixed = [r[0] for r in self.con.execute(f"""
                SELECT ep_id FROM episodes e WHERE 1=1 {w}
                ORDER BY cost DESC LIMIT ?""", (fixed_slots,)).fetchall()]

        # (6) 熟练度加成（"手上有惯性"）
        #     familiarity=0 时完全不影响排序（保持基线行为，确保不退化）
        if familiarity and familiarity > 0 and scores:
            try:
                import channel as _ch
                rows = self.con.execute(
                    "SELECT key, val FROM meta WHERE key LIKE 'channel:%'"
                ).fetchall()
                if rows:
                    for k, v in rows:
                        eid = k.replace("channel:", "")
                        if eid not in scores:
                            continue
                        try:
                            d = json.loads(v)
                        except Exception:
                            continue
                        fam = _ch.familiarity(
                            {c: int(d.get(c) or 0) for c in _ch.CHANNELS})
                        # 加成是乘性的：熟练度 0~1，最多提升 familiarity 倍
                        scores[eid] = scores[eid] * (1.0 + familiarity * fam)
            except Exception:
                pass

        order = sorted(scores, key=lambda k: -scores[k])

        # 固定席位是"直接在场"（不参与竞争），必须**追加**而不是**替换**检索结果。
        # E-148 实测的 bug：原写法 fixed + order 后统一截断 [:max(top, fixed_slots)]，
        # 当 fixed_slots >= top 时，5 个席位全被固定记忆占满，检索结果一个都进不来，
        # 召回从 21/23 掉到 5/23。
        # 正确语义（用户："手上有惯性，不需要动脑子"）：
        #   · 检索结果照常给 top 条（不影响召回）
        #   · 固定席位额外追加在后面（标记 flag=1，调用方可自行取舍）
        picked = list(dict.fromkeys(order))[:top]
        for eid in fixed:
            if eid not in picked:
                picked.append(eid)

        out = []
        for eid in picked:
            ep = self.get(eid)
            if ep:
                out.append((ep, round(scores.get(eid, 0.0), 3),
                            1 if eid in fixed else 0))

        # 记录访问（E-242 修：原来 channel 计数只被睡眠调用，
        # 真实检索命中从未记账，导致"常用度"永远是 0，
        # 分诊（triage）无法区分常用/少见）。
        # 这里在**检索命中**时记账：这是"被用到"的真实证据。
        try:
            import channel as _ch
            hit_ids = [ep.ep_id for ep, _, _ in out]
            if hit_ids:
                _ch.bump_many(self, hit_ids, "recall_by_query", 1)
        except Exception:
            pass

        # L0/L1 记忆永远在场（docs/72）：
        # 红线（L0）与常驻准则（L1）必须每次都参与，缺了模型行为就变。
        # 与固定席位一样是**追加**而非替换，flag=2 标识受保护。
        try:
            import hook as _hook
            hid = _hook.present_ids(self)
            if hid:
                present = {ep.ep_id for ep, _, _ in out}
                for eid in hid:
                    if eid in present:
                        # 已在结果里，升级 flag 为 2
                        out = [(ep, sc, 2 if ep.ep_id == eid else fl)
                               for ep, sc, fl in out]
                        continue
                    ep = self.get(eid)
                    if ep and not ep.invalid_at:
                        out.append((ep, 0.0, 2))
        except Exception:
            pass

        if expand:
            out = self._expand(out, include_invalid, hops)

        # cross-encoder 精排（可选，E-207 实测 GPU 156ms/题）
        if rerank and out:
            try:
                from rerank_v1 import CrossEncoderReranker
                _rk = CrossEncoderReranker()
                pool_n = rerank_pool if rerank_pool > 0 else top * 3
                # 取前 pool_n 条做精排（rerank 只重排已有结果，不扩大召回）
                candidates = out[:pool_n]
                texts = [ep.raw_text for ep, _, _ in candidates]
                rk_scores = _rk.score(query, texts)
                idx = sorted(range(len(rk_scores)),
                             key=lambda i: -rk_scores[i])
                reranked = [candidates[i] for i in idx]
                # 重排后的 top，保留 flag
                out = [(ep, sc, fl) for ep, sc, fl in reranked[:top]]
                # 若固定席位被重排挤出，补回
                for ep, sc, fl in reranked[top:]:
                    if fl == 1:
                        out.append((ep, sc, fl))
            except Exception as e:
                # 精排失败降级：保留原结果，不因 rerank 崩溃
                print(f"[warn] cross-encoder 精排失败: {e}", file=sys.stderr)

        return out

    def _expand(self, base, include_invalid, hops=2):
        """链式展开：只沿情景/情绪/触发场景，不含 cost"""
        import numpy as np
        if not base:
            return base
        w = "" if include_invalid else "AND e.invalid_at IS NULL"
        out = list(base)
        seen = {ep.ep_id for ep, _, _ in base}
        seed = base[0][0]

        # 基准相似度
        base_map = {}
        try:
            self._load_vectors()
            if self._vec is not None and len(self._vec):
                sv = Embedder.get().encode(seed.raw_text)[0]
                sims = self._vec @ sv
                base_map = {self._vec_ids[i]: float(sims[i])
                            for i in range(len(self._vec_ids))}
        except Exception:
            pass
        seed_sim = base_map.get(seed.ep_id, 1.0)
        DECAY = 0.75

        frontier = [(seed, seed_sim)]
        added = 0
        for hop in range(1, hops + 1):
            nxt = []
            for cur, cur_sim in frontier:
                cands = {}
                # 路 A 情景相似（带衰减）
                for eid, sim in base_map.items():
                    if eid != cur.ep_id and sim >= cur_sim * DECAY:
                        cands[eid] = sim
                # 路 B 触发场景共现
                ctr = set(cur.triggers)
                if ctr:
                    for eid, trigs in self.con.execute(f"""
                            SELECT ep_id, triggers FROM episodes e
                            WHERE ep_id != ? {w}""", (cur.ep_id,)).fetchall():
                        tl = json.loads(trigs) if trigs else []
                        if ctr & set(tl):
                            cands[eid] = cands.get(eid, 0) + 0.9
                # 路 C 情绪共振
                if abs(cur.valence) >= 0.15 and cur.affect_peak >= 0.15:
                    for eid, v, p in self.con.execute(f"""
                            SELECT ep_id, valence, affect_peak FROM episodes e
                            WHERE ep_id != ? {w}""", (cur.ep_id,)).fetchall():
                        if v is None or p is None:
                            continue
                        if v * cur.valence > 0 and abs(p - cur.affect_peak) <= 0.25:
                            cands[eid] = cands.get(eid, 0) + 0.6
                for eid in sorted(cands, key=lambda x: -cands[x])[:2]:
                    if eid in seen or added >= hops * 2:
                        continue
                    ep = self.get(eid)
                    if ep:
                        seen.add(eid); added += 1
                        out.append((ep, round(cands[eid], 3), hop))
                        nxt.append((ep, base_map.get(eid, cur_sim * DECAY)))
            frontier = nxt
            if not frontier or added >= hops * 2:
                break
        return out

    def stats(self):
        n = self.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        nv = self.con.execute("SELECT COUNT(*) FROM episodes WHERE invalid_at IS NULL").fetchone()[0]
        nvec = self.con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
        hc = self.con.execute("SELECT COUNT(*) FROM episodes WHERE cost>=0.7").fetchone()[0]
        ha = self.con.execute("SELECT COUNT(*) FROM episodes WHERE affect_peak>=0.4").fetchone()[0]
        return {"total": n, "valid": nv, "invalid": n - nv, "vectors": nvec,
                "high_cost": hc, "high_affect": ha,
                "size_mb": round(os.path.getsize(self.path) / 1024 / 1024, 2)
                if os.path.exists(self.path) else 0}
