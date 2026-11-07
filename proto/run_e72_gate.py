"""
E-72 写入完整性检查（write-time completeness gate）

E-71 的发现：
  7 个仍失败里，5 个是"写入时就没写完整"（裸代词/无锚点时间/只有情绪），
  不是检索问题。改检索救不回来。

设计：写入时让模型自己过一道闸
  ① 静默检测（规则快筛）：裸代词、相对时间、纯情绪句
  ② 模型自问："以后有人问起这条，我能答吗？"（E-69 已证 F1=0.974）
  ③ 不合格 → 分类打标，不拒绝写入（红线：不丢数据），但标记 incomplete
  ④ 检索时：incomplete 的记忆仍可被召回，但会带上"信息不全"提示

红线保持：
  R1 迁移 100%      —— 新字段必须参与逐字段比对
  R2 能力不降        —— 原有 30 题召回不得下降
  R3 索引可重建
  R4 数据不丢        —— incomplete 也照存不误
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
TASK = os.path.join(_HERE, "work", "e72")
os.makedirs(TASK, exist_ok=True)
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

# ---------- 三类完整性缺陷 ----------
BARE = re.compile(r"(这个|那个|这件事|那件事|那条街|那个东西|那一步|那里|那个问题)")
REL_TIME = re.compile(r"(那天|那次|那时候|当时|前几天|上次|某个时候|有一阵|那阵子)")
EMO_ONLY = re.compile(r"(难受|崩溃|开心|生气|害怕|委屈|心疼|激动|舒服|轻松)")


def fast_check(text: str) -> dict:
    """
    规则快筛（便宜，先跑）。
    返回 {clean: bool, issues: [str], detail: {...}}
    """
    t = text or ""
    issues = []
    detail = {}

    # ① 裸代词：代词后面没有**真正的具体名词**
    #    （"这个项目"算完整；"这个千万别…"里的"千万"不是名词，仍算裸）
    NOT_NOUN_PREFIX = ("千万", "一定", "必须", "要", "别", "不能", "很", "特别",
                       "太", "真的", "就", "还", "也", "都", "已经", "可以")
    bare = []
    for m in BARE.finditer(t):
        tail = t[m.end():m.end() + 4]
        is_noun_after = bool(re.match(r"^[\u4e00-\u9fa5]{2,}", tail)) and \
            not any(tail.startswith(p) for p in NOT_NOUN_PREFIX)
        if not is_noun_after:
            bare.append(m.group(1))
        elif m.group(1) in ("那条街", "那里") and not re.search(r"(路|街|巷|道)", t):
            bare.append(m.group(1))
    if bare:
        issues.append("bare_pronoun")
        detail["bare"] = bare

    # ② 相对时间无锚点
    rel = REL_TIME.findall(t)
    has_abs = bool(re.search(r"\d{1,2}月\d{1,2}|\d{4}[-/年]", t))
    if rel and not has_abs:
        issues.append("unanchored_time")
        detail["rel_time"] = rel

    # ③ 只有情绪没有事实（无实体、无动作）
    facts = re.search(r"(是|有|负责|定|改|去|来|给|拿|放|说|做)[\u4e00-\u9fa5]{2,}", t)
    if EMO_ONLY.search(t) and not facts and len(t) < 28:
        issues.append("emotion_only")
        detail["emotion"] = EMO_ONLY.findall(t)

    return {"clean": len(issues) == 0, "issues": issues, "detail": detail}


def classify_fix(issues: list[str]) -> list[dict]:
    """按缺陷类型给出修复建议（不自动改内容，只提示）"""
    MAP = {
        "bare_pronoun": {"ask": "这个代词指什么？请补上具体对象",
                         "action": "ask_disambiguate"},
        "unanchored_time": {"ask": "这是哪一天？请补绝对日期或明确锚点",
                            "action": "ask_anchor_time"},
        "emotion_only": {"ask": "这条只有感受，没有可回答的事实；"
                                "要标为'感受类'还是补充事实？",
                         "action": "ask_fact"},
    }
    return [{"issue": i, **MAP.get(i, {"ask": "需要补充", "action": "ask"})}
            for i in issues]


class StoreV13:
    """v1.3：写入完整性闸 + 保留 v1.2 全部能力"""

    def __init__(self, base, fields_impl):
        self.st = base
        self.v12 = fields_impl          # StoreV12（含结构化字段与别名）
        self.incomplete = {}            # ep_id -> {issues, detail}

    def add(self, text, **kw):
        ep = self.v12.add(text, **kw)
        chk = fast_check(text)
        if not chk["clean"]:
            self.incomplete[ep.ep_id] = {
                "issues": chk["issues"], "detail": chk["detail"],
                "fixes": classify_fix(chk["issues"])}
        return ep

    def search(self, query, top=5, **kw):
        res = self.v12.search(query, top=top, **kw)
        out = []
        for ep, sc in res:
            flag = self.incomplete.get(ep.ep_id)
            out.append((ep, sc, flag))
        return out


# ---------- 完整性检查的"准确率"测试 ----------
# 人工标注：哪些确实是不完整的（Gold）
GOLD_INCOMPLETE = {
    "这个千万别搞错，搞错了要出大事": ["bare_pronoun"],
    "和喜欢的人第一次约会，走过那条街": ["bare_pronoun"],
    "我被裁了，那天晚上一个人在车里坐了很久": ["unanchored_time"],
    "我很难受": ["emotion_only"],
}
GOLD_COMPLETE = [
    "我电脑的登录密码是 8848",
    "下个月 15 号是项目的截止日期，逾期不候",
    "孩子第一次叫妈妈的时候，我眼泪一下就出来了",
    "客户明确说这条需求不能改，否则要重做",
    "小时候被邻居家的狗追着咬过，小腿上留了疤",
    "钱包丢了，里面有身份证和所有的卡",
    "9月3日和老王讨论接口参数，最后定的是0.85",
    "家里的门锁密码是 3721",
    "小时候在老家院子里看夕阳，天是橘红色的，特别轻松",
    "仓库里还有两百件库存",
]


def eval_checker():
    print("=" * 92)
    print("E-72 第 1 步：完整性检查器准确率")
    print("=" * 92)

    tp = fp = tn = fn = 0
    print("\n应判『不完整』的：")
    print("-" * 92)
    print(f"{'文本':<44}{'期望':<18}{'实判':<20}{'对?'}")
    print("-" * 92)
    for text, gt in GOLD_INCOMPLETE.items():
        got = fast_check(text)
        ok = bool(set(gt) & set(got["issues"]))
        if ok: tp += 1
        else: fn += 1
        print(f"{text[:42]:<44}{'/'.join(gt):<18}"
              f"{('/'.join(got['issues']) or 'clean'):<20}{'✓' if ok else '✗'}")

    print("\n应判『完整』的：")
    print("-" * 92)
    for text in GOLD_COMPLETE:
        got = fast_check(text)
        ok = got["clean"]
        if ok: tn += 1
        else: fp += 1
        print(f"{text[:42]:<44}{'clean':<18}"
              f"{('/'.join(got['issues']) or 'clean'):<20}{'✓' if ok else '✗'}")
    print("-" * 92)

    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    acc = (tp + tn) / (tp + fp + tn + fn)
    print(f"\n  准确率 {acc:.4f}  精确率 {prec:.4f}  召回 {rec:.4f}  F1 {f1:.4f}")
    print(f"  (TP={tp} FP={fp} TN={tn} FN={fn})")
    return {"acc": acc, "precision": prec, "recall": rec, "f1": f1,
            "tp": tp, "fp": fp, "tn": tn, "fn": fn}


def main():
    from store_v1 import MemoryStore
    from run_e71_fields import StoreV12

    r1 = eval_checker()

    # ---------- 接入并重测原 30 题 ----------
    corpus = json.load(open(os.path.join(_HERE, "work", "e70_task", "corpus.json"),
                            encoding="utf-8"))
    goals = json.load(open(os.path.join(_HERE, "work", "e70_task", "goals.json"),
                           encoding="utf-8"))

    db = os.path.join(TASK, "v13.db")
    for suf in ("", "-wal", "-shm"):
        p = db + suf
        if os.path.exists(p):
            os.remove(p)

    base = MemoryStore(db)
    v12 = StoreV12(base)
    st = StoreV13(base, v12)

    print("\n" + "=" * 92)
    print("E-72 第 2 步：写入闸生效情况")
    print("=" * 92)
    mid2eid = {}
    print("\n" + "-" * 92)
    print(f"{'mid':<6}{'状态':<12}{'缺陷':<24}{'内容'}")
    print("-" * 92)
    n_inc = 0
    for r in corpus:
        ep = st.add(r["memory"])
        mid2eid[r["mid"]] = ep.ep_id
        fl = st.incomplete.get(ep.ep_id)
        if fl:
            n_inc += 1
            print(f"{r['mid']:<6}{'⚠不完整':<12}"
                  f"{'/'.join(fl['issues']):<24}{r['memory'][:34]}")
        else:
            print(f"{r['mid']:<6}{'完整':<12}{'-':<24}{r['memory'][:34]}")
    print("-" * 92)
    print(f"  标记不完整 {n_inc}/{len(corpus)} 条（数据照存，不拒收）")

    # ---------- 红线 R2：召回不降 ----------
    print("\n" + "=" * 92)
    print("R2 能力不降：30 题召回对比")
    print("=" * 92)
    hit = 0
    total = 0
    for g in goals:
        for q in g["questions"]:
            total += 1
            res = st.search(q["q"], top=3)
            ids = [ep.ep_id for ep, *_ in res]
            if mid2eid[g["mid"]] in ids:
                hit += 1
    print(f"  top-3 命中 {hit}/{total} = {hit/total:.4f}（E-71 是 30/30）")

    out = {"experiment": "E-72", "checker": r1,
           "n_corpus": len(corpus), "n_incomplete": n_inc,
           "recall_top3": round(hit / total, 4)}
    with open(os.path.join(RESULTS, "e72_gate.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e72_gate.json')}")


if __name__ == "__main__":
    main()
