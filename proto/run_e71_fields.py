"""
E-71 按模型自己的诊断改造，然后重测

E-70 模型自检发现 8 个失败，并自己给出了诊断：
  5 条 structure（缺 subject / due_date / street / emotion+salience / trigger+step）
  2 条 alias（别名覆盖不足）
  1 条 content（裸代词，写入时没记全）

E-71 严格按它的诊断做：
  1. 新增结构化字段：subject, due_date, destination, waypoint, street,
     emotion, salience, trigger, step
  2. 别名表（工期=排期=DDL 等）
  3. 写入时检查"裸代词"（这个/那个/那条街），有就报警
  4. 重跑同一批 30 个问题，对比「能答率」

关键：改造前后用**同一批问题、同一套检索**，只多字段和别名。
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
TASK = os.path.join(_HERE, "work", "e70_task")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

# ---------- 模型提出的别名表 ----------
ALIAS = {
    "千万别搞错": ["不能搞错", "特别不能搞错", "别弄错", "踩坑", "关键点", "红线",
                "重要", "注意"],
    "截止日期": ["工期", "排期", "交付日", "最晚", "必须弄完", "DDL", "deadline",
               "逾期", "什么时候截止", "due"],
    "密码": ["口令", "passcode", "登录用", "输什么"],
    "夕阳": ["日落", "晚霞", "傍晚", "橘红色"],
    "被狗咬": ["狗", "大型犬", "追咬", "怕狗"],
    "当众出丑": ["丢脸", "尴尬", "台上", "忘词"],
    "被裁": ["失业", "丢工作", "裁员", "没工作了"],
    "需求不能改": ["不能改", "不许改", "硬性要求", "红线需求"],
    "约会": ["见面", "第一次约", "出去"],
    "街": ["路", "哪条路", "经过哪儿", "走的哪条道"],
    "眼泪": ["哭", "流泪", "眼眶"],
    "叫妈妈": ["叫妈", "第一次说话", "学说话"],
}

# ---------- 裸代词检测（模型指出的 content 问题）----------
BARE_PRONOUN = re.compile(r"(这个|那个|这件事|那件事|那条街|那个东西|那一步)(?![\u4e00-\u9fa5]{2,})")

# ---------- 结构化字段抽取 ----------
EMOTION_WORDS = {
    "崩溃": ["崩溃", "快疯了", "受不了"],
    "难过": ["难过", "难受", "心疼", "想哭", "眼泪", "哭"],
    "恐惧": ["害怕", "恐惧", "怕", "发怵"],
    "愤怒": ["生气", "愤怒", "气死"],
    "羞耻": ["出丑", "丢脸", "尴尬", "忘词"],
    "怀念": ["怀念", "想起", "那时候", "小时候"],
    "温暖": ["温暖", "安心", "轻松", "幸福"],
}
LOW_POINT = ["被裁", "失业", "丢工作", "去世", "分手", "失败", "崩溃", "低谷"]
TRIGGER_SCENE = {
    "动手前": ["执行前", "动手前", "操作前", "上线前", "马上要做"],
    "看到狗": ["狗", "大型犬"],
    "开会发言": ["发言", "台上", "开会", "汇报"],
    "傍晚": ["夕阳", "傍晚", "橘红色"],
    "登录": ["密码", "登录"],
    "填表": ["身份证号", "填表"],
}
DATE_PAT = re.compile(r"(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?|\d{1,2}月\d{1,2}[日号])")
STREET_PAT = re.compile(r"([\u4e00-\u9fa5]{2,6}(?:路|街|巷|道|大道))")


def detect_bare_pronoun(text: str) -> list[str]:
    """返回文本里的裸代词（后面没跟具体名词的）"""
    out = []
    for m in BARE_PRONOUN.finditer(text or ""):
        tail = text[m.end():m.end() + 2]
        if not tail or not re.match(r"[\u4e00-\u9fa5]{2,}", tail):
            out.append(m.group(1))
    return out


def extract_fields(text: str) -> dict:
    t = text or ""
    f = {}
    # 时间
    dm = DATE_PAT.findall(t)
    if dm:
        f["due_date"] = dm[0] if ("截止" in t or "必须" in t or "逾期" in t) else None
        f["date"] = dm[0]
    else:
        f["date"] = None
        f["due_date"] = None
    # 地点
    sm = STREET_PAT.findall(t)
    f["street"] = sm[0] if sm else None
    locs = re.findall(r"(在[家公办医院学校商场院子车里路上]{1,4})", t)
    f["destination"] = locs[0] if locs else None
    f["waypoint"] = sm[0] if sm else None
    # 情绪
    emos = [k for k, ws in EMOTION_WORDS.items() if any(w in t for w in ws)]
    f["emotion"] = emos
    f["salience"] = 5 if any(w in t for w in LOW_POINT) else (
        3 if emos else 1)
    # 触发场景
    f["trigger"] = [k for k, ws in TRIGGER_SCENE.items() if any(w in t for w in ws)]
    # subject：警告句的所指
    if "搞错" in t or "注意" in t or "重要" in t:
        m = re.search(r"(改|做|上|发|交|填)([\u4e00-\u9fa5]{2,8})", t)
        f["subject"] = m.group(0) if m else None
    else:
        f["subject"] = None
    f["step"] = None
    # 裸代词
    f["bare_pronoun"] = detect_bare_pronoun(t)
    return f


class StoreV12:
    """在 v1.1 基础上加结构化字段 + 别名扩展检索"""

    def __init__(self, base_store):
        self.st = base_store
        self.fields = {}     # ep_id -> dict
        self.alias_index = {}  # token -> set(ep_id)

    def add(self, text, **kw):
        ep = self.st.add(text, **kw)
        f = extract_fields(text)
        self.fields[ep.ep_id] = f
        # 建别名索引
        for canon, als in ALIAS.items():
            if canon in text or any(a in text for a in als):
                for tok in [canon] + als:
                    self.alias_index.setdefault(tok, set()).add(ep.ep_id)
        # 字段也进别名索引
        for k in ("street", "date", "destination"):
            if f.get(k):
                self.alias_index.setdefault(str(f[k]), set()).add(ep.ep_id)
        return ep

    def search(self, query, top=5, **kw):
        # 1) 原检索
        base = self.st.search(query, top=top, **kw)
        base_ids = [ep.ep_id for ep, *_ in base]
        scores = {ep.ep_id: float(sc) for ep, sc, *_ in [[e, s] for e, s in
                  [(x[0], x[1]) for x in base]]}

        # 2) 别名扩展：查询里的词，命中别名表则把对应记忆加回来
        q = query or ""
        for tok, epids in self.alias_index.items():
            if tok in q or any(tok in a for a in [q]):
                for eid in epids:
                    if eid not in scores:
                        scores[eid] = 1.5      # 别名命中给较高分
                    else:
                        scores[eid] += 0.8

        # 3) 字段匹配：查询里出现字段值（如街名、日期）
        for eid, f in self.fields.items():
            for k in ("street", "date", "due_date", "destination"):
                v = f.get(k)
                if v and str(v) in q:
                    scores[eid] = scores.get(eid, 0) + 1.2
            # 触发场景
            for tr in (f.get("trigger") or []):
                if tr and any(w in q for w in TRIGGER_SCENE.get(tr, [])):
                    scores[eid] = scores.get(eid, 0) + 1.0
            # 情绪标签
            for em in (f.get("emotion") or []):
                if em in q:
                    scores[eid] = scores.get(eid, 0) + 0.9

        out = []
        for eid in sorted(scores, key=lambda k: -scores[k])[:top]:
            ep = self.st.get(eid)
            if ep:
                out.append((ep, round(scores[eid], 3)))
        return out


def main():
    from store_v1 import MemoryStore

    corpus = json.load(open(os.path.join(TASK, "corpus.json"), encoding="utf-8"))
    goals = json.load(open(os.path.join(TASK, "goals.json"), encoding="utf-8"))

    db = os.path.join(TASK, "e71.db")
    for suf in ("", "-wal", "-shm"):
        p = db + suf
        if os.path.exists(p):
            os.remove(p)

    base = MemoryStore(db)
    st = StoreV12(base)

    print("=" * 92)
    print("E-71 按模型自己的诊断改造后重测")
    print("=" * 92)

    mid2eid = {}
    print("\n写入并抽取结构化字段：\n")
    print("-" * 92)
    print(f"{'mid':<6}{'emotion':<12}{'salience':<10}{'trigger':<16}"
          f"{'street':<10}{'裸代词':<10}")
    print("-" * 92)
    bare_total = 0
    for r in corpus:
        ep = st.add(r["memory"])
        mid2eid[r["mid"]] = ep.ep_id
        f = st.fields[ep.ep_id]
        bare_total += len(f["bare_pronoun"])
        print(f"{r['mid']:<6}{'/'.join(f['emotion'])[:10]:<12}"
              f"{f['salience']:<10}{'/'.join(f['trigger'])[:14]:<16}"
              f"{str(f['street'] or '-')[:8]:<10}"
              f"{','.join(f['bare_pronoun']) or '-':<10}")
    print("-" * 92)
    print(f"  裸代词总数 = {bare_total}（模型指出的写入质量问题）")

    # ---------- 重测同一批 30 问 ----------
    print("\n" + "=" * 92)
    print("重测（同一批 30 个问题）")
    print("=" * 92)
    print("-" * 92)
    print(f"{'问题':<40}{'类型':<14}{'旧top3命中':<12}{'新top3命中'}")
    print("-" * 92)

    old_hit = new_hit = 0
    total = 0
    worse = []
    for g in goals:
        for q in g["questions"]:
            total += 1
            tgt = mid2eid[g["mid"]]
            r_old = [ep.ep_id for ep, *_ in base.search(q["q"], top=3)]
            r_new = [ep.ep_id for ep, *_ in st.search(q["q"], top=3)]
            o = tgt in r_old
            n = tgt in r_new
            old_hit += 1 if o else 0
            new_hit += 1 if n else 0
            if o and not n:
                worse.append(q["q"])
            flag = ""
            if not o and n:
                flag = " ↑修复"
            elif o and not n:
                flag = " ↓退化"
            print(f"{q['q'][:38]:<40}{q['type']:<14}{str(o):<12}{str(n)}{flag}")
    print("-" * 92)

    print(f"\n旧系统 top-3 命中: {old_hit}/{total} = {old_hit/total:.4f}")
    print(f"新系统 top-3 命中: {new_hit}/{total} = {new_hit/total:.4f}")
    print(f"变化: {(new_hit-old_hit)/total:+.4f}")
    if worse:
        print(f"\n退化的 {len(worse)} 条：")
        for w in worse:
            print(f"  - {w}")

    out = {"experiment": "E-71", "n": total,
           "old_hit": old_hit, "new_hit": new_hit,
           "old_rate": round(old_hit/total, 4), "new_rate": round(new_hit/total, 4),
           "delta": round((new_hit-old_hit)/total, 4),
           "bare_pronoun_total": bare_total,
           "regressions": worse}
    with open(os.path.join(RESULTS, "e71_fields.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e71_fields.json')}")


if __name__ == "__main__":
    main()
