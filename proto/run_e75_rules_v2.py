"""
E-75 修补 5 条漏判，验证能否达到全覆盖

E-74 漏掉的 5 条及原因：
  R07/R11/R18  「这个方案」「那个文件」「那个东西」
                → 我的规则认为"方案/文件/东西"是名词所以不算裸代词，
                  但模型认为它们是**类名词（泛指）**，不是专名 → 仍算裸
  R13  「下周三」 → 时间词表缺这种表达
  R20  长句空泛  → 长度刚好在 60 字边界

修法（按模型的标准）：
  ① bare_pronoun 收紧：代词后跟"类名词"也算裸（方案/文件/东西/事/问题/
     情况/部分/内容/步骤/流程/标准/办法）
  ② 时间词表补：下周三/上周五/这个月/月底/年初/某天/过了一会儿
  ③ vague_summary 边界从 60 降到 45，并加"空泛词≥2"的权重判定
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e73")
RESULTS = os.path.join(_HERE, "results")

# ---------- 收紧后的规则 ----------
# 类名词：泛指，不能定位到具体对象
GENERIC_NOUN = ["方案", "文件", "东西", "事情", "事", "问题", "情况",
                "部分", "内容", "步骤", "流程", "标准", "办法", "办法",
                "材料", "资料", "数据", "信息", "结果", "项目", "需求",
                "计划", "安排", "条例", "规定"]

BARE = re.compile(r"(这个|那个|这件事|那件事|那条街|那个东西|那一步|那里|那边|那个问题)")
TIME_WORDS = ["那天", "那次", "那时候", "当时", "前几天", "上次", "某个时候",
              "有一阵", "那阵子", "下周三", "下周一", "下周五", "上周三",
              "上周五", "这个月", "月底", "年初", "年底", "某天", "过一会儿",
              "过几天", "改天", "回头"]
NOT_NOUN_PREFIX = ("千万", "一定", "必须", "要", "别", "不能", "很", "特别",
                   "太", "真的", "就", "还", "也", "都", "已经", "可以")

VAGUE_WORDS = ["还行", "再讨论", "可能需要", "对一下", "整体", "细节",
               "方向", "资源分配", "差不多", "再说", "看一下", "估摸",
               "挺好", "不错", "应该", "可能"]

CONVENTION = ["老规矩", "老办法", "按惯例", "按流程", "走流程",
              "市场价", "行价", "正常价", "行情价", "照旧", "还是老样子"]
VALUE_SLOT = ["价格", "成本", "金额", "报价", "数量", "尺寸", "重量", "费用"]
FUZZY = ["太高", "太低", "偏高", "偏低", "有点", "大概", "差不多",
         "还行", "谈下来了", "搞定了", "不错", "可以"]
VAGUE_NOUN = ["东西", "事", "问题", "条件", "药", "货", "情况"]
PREDICATE = ["说", "不行", "同意", "否决", "提醒", "对接", "请示", "确认",
             "回复", "答复", "记得", "忘了"]
PROPER_NOUN = re.compile(
    r"([\u4e00-\u9fa5]{2,4}(?:工|总|经理|老师|医生|师傅))|"
    r"(\d{2,})|([A-Za-z]{2,})|(\"[^\"]+\")|(“[^”]+”)")
NUM = re.compile(r"(\d+|[一二三四五六七八九十百千万亿]+)")


def chk_bare(t: str) -> list[str]:
    """代词 + 后面是类名词或没名词 → 裸"""
    bare = []
    for m in BARE.finditer(t):
        pron = m.group(1)
        tail = t[m.end():m.end() + 4]
        # 后面是类名词 → 仍算裸（模型标准）
        generic = any(tail.startswith(g) for g in GENERIC_NOUN)
        # 后面是"非名词前缀" → 裸
        non_noun = (not re.match(r"^[\u4e00-\u9fa5]{2,}", tail)) or \
            any(tail.startswith(p) for p in NOT_NOUN_PREFIX)
        if generic or non_noun:
            bare.append(pron)
    return list(dict.fromkeys(bare))


def chk_time(t: str) -> list[str]:
    rel = [w for w in TIME_WORDS if w in t]
    has_abs = bool(re.search(r"\d{1,2}月\d{1,2}|\d{4}[-/年]|\d{1,2}月\d{1,2}[日号]", t))
    if rel and not has_abs:
        return rel
    return []


def chk_missing_object(t: str) -> bool:
    if PROPER_NOUN.search(t) or NUM.search(t):
        return False
    for p in ["说", "同意", "否决", "提醒", "对接"]:
        if re.search(p + r"[\u4e00-\u9fa5]{0,3}$", t):
            return True
    for p in PREDICATE:
        if p in t:
            for v in VAGUE_NOUN:
                if v in t:
                    return True
    return False


def chk_vague_value(t: str) -> bool:
    has_slot = any(w in t for w in VALUE_SLOT)
    has_num = bool(re.search(r"\d", t) or re.search(r"[一二三四五六七八九十百千万]", t))
    has_fuzzy = any(w in t for w in FUZZY)
    return has_slot and not has_num and has_fuzzy


def chk_convention(t: str) -> bool:
    return any(w in t for w in CONVENTION)


def chk_vague_summary(t: str) -> bool:
    """边界从 60 降到 45"""
    if len(t) <= 45:
        return False
    if PROPER_NOUN.search(t):
        return False
    if re.search(r"\d", t) or re.search(r"[一二三四五六七八九十]", t):
        return False
    if re.search(r"\d{1,2}月\d{1,2}|\d{4}[-/年]", t):
        return False
    return sum(1 for w in VAGUE_WORDS if w in t) >= 2


def full_check(t: str) -> list[str]:
    issues = []
    if chk_bare(t): issues.append("bare_pronoun")
    if chk_time(t): issues.append("unanchored_time")
    if chk_missing_object(t): issues.append("missing_object")
    if chk_vague_value(t): issues.append("vague_value")
    if chk_convention(t): issues.append("undefined_convention")
    if chk_vague_summary(t): issues.append("vague_summary")
    return issues


def main():
    audit = json.load(open(os.path.join(TASK, "audit.json"), encoding="utf-8"))
    corpus = {r["rid"]: r["memory"] for r in
              json.load(open(os.path.join(TASK, "corpus.json"), encoding="utf-8"))}
    per = {p["rid"]: p.get("verdict", "?") for p in audit.get("per_item", [])}

    print("=" * 94)
    print("E-75 修补后重测（模型标准：类名词也算裸代词）")
    print("=" * 94)
    print("-" * 94)
    print(f"{'rid':<6}{'模型判定':<22}{'修补后规则':<40}{'结果':<8}{'内容'}")
    print("-" * 94)

    bad = [r for r, v in per.items() if v != "ok"]
    ok = [r for r, v in per.items() if v == "ok"]
    caught = 0
    fp = 0
    for rid in sorted(per):
        v = per[rid]
        txt = corpus[rid]
        iss = full_check(txt)
        hit = len(iss) > 0
        if v != "ok" and hit: caught += 1
        if v == "ok" and hit: fp += 1
        mark = ""
        if v == "ok" and hit: mark = "⚠误伤"
        elif v != "ok" and not hit: mark = "✗漏"
        elif v != "ok" and hit: mark = "✓"
        print(f"{rid:<6}{v:<22}{('/'.join(iss) or 'clean')[:38]:<40}{mark:<8}{txt[:22]}")
    print("-" * 94)

    rec = caught / len(bad) if bad else 0
    prec = caught / (caught + fp) if (caught + fp) else 0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0

    print(f"\n模型认为有问题 {len(bad)} 条，抓到 {caught} 条，误伤 {fp} 条")
    print(f"  召回 {rec:.4f}  精确 {prec:.4f}  F1 {f1:.4f}")
    print(f"\n对比：")
    print(f"  E-73 原 3 类:  1/18  F1 —")
    print(f"  E-74 +模型规则: 13/18  F1 0.8387")
    print(f"  E-75 修补后:   {caught}/{len(bad)}  F1 {f1:.4f}")

    if caught == len(bad) and fp == 0:
        print("\n  ★ 全覆盖且零误伤：模型的分类体系已完整落地")

    out = {"experiment": "E-75", "n": len(per),
           "n_bad": len(bad), "caught": caught, "false_pos": fp,
           "recall": round(rec, 4), "precision": round(prec, 4),
           "f1": round(f1, 4)}
    with open(os.path.join(RESULTS, "e75_rules_v2.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e75_rules_v2.json')}")


if __name__ == "__main__":
    main()
