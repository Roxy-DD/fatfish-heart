"""
E-74 把模型发现的 4 个新类型落成规则，测覆盖率

E-73 结果：
  21 条里，现有 3 类规则只覆盖 1 条，漏 17 条
  模型归纳了 4 个新类型：
    missing_object       有谓词但无承受对象（"他说不行""记得提醒我"）
    vague_value          有数值槽位却无任何数字（"成本太高""价格谈下来了"）
    undefined_convention 用未定义的约定（"老规矩""市场价"）
    vague_summary        长而空，抽不出可验证事实

E-74：严格按它的 detect 描述实现，测这 4 类 + 原 3 类合起来的覆盖率。
判据：能否覆盖模型指出的 17 条漏掉的（且不误伤 ok 的 3 条）。
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

# ================= 模型提出的 4 类规则 =================

# ---- 1. missing_object ----
PREDICATE = ["说", "不行", "同意", "否决", "提醒", "对接", "请示", "确认",
             "回复", "答复", "有", "是", "要", "记得", "忘了", "觉得"]
VAGUE_NOUN = ["东西", "事", "问题", "条件", "药", "货", "情况", "方案",
              "文件", "价格", "那个", "这个"]
PROPER_NOUN = re.compile(
    r"([\u4e00-\u9fa5]{2,4}(?:工|总|经理|老师|医生|师傅))|"
    r"(\d{2,})|([A-Za-z]{2,})|(\"[^\"]+\")|(“[^”]+”)")
NUM = re.compile(r"(\d+|[一二三四五六七八九十百千万亿]+)")


def chk_missing_object(t: str) -> bool:
    """谓词 ∧ 无专名/编号/数字/引号 ∧ (虚义名词 或 谓词后即句末)"""
    if not PROPER_NOUN.search(t) and not NUM.search(t):
        # 谓词后直接结句（"他说不行"）
        for p in ["说", "同意", "否决", "提醒", "对接"]:
            if re.search(p + r"[\u4e00-\u9fa5]{0,3}$", t):
                return True
        # 有谓词但对象是虚义名词
        for p in PREDICATE:
            if p in t:
                for v in VAGUE_NOUN:
                    if v in t:
                        return True
    return False


# ---- 2. vague_value ----
VALUE_SLOT = ["价格", "成本", "金额", "报价", "数量", "尺寸", "重量", "费用"]
FUZZY = ["太高", "太低", "偏高", "偏低", "有点", "大概", "差不多",
         "还行", "谈下来了", "搞定了", "不错", "可以"]


def chk_vague_value(t: str) -> bool:
    """数值槽 ∧ 全句无数字 ∧ 含模糊词"""
    has_slot = any(w in t for w in VALUE_SLOT)
    has_num = bool(re.search(r"\d", t) or
                   re.search(r"[一二三四五六七八九十百千万]", t))
    has_fuzzy = any(w in t for w in FUZZY)
    return has_slot and not has_num and has_fuzzy


# ---- 3. undefined_convention ----
CONVENTION = ["老规矩", "老办法", "按惯例", "按流程", "走流程",
              "市场价", "行价", "正常价", "行情价", "照旧", "还是老样子"]


def chk_undefined_convention(t: str, defined_terms: set) -> bool:
    """命中约定词 ∧ 该词在库内无定义条目"""
    for w in CONVENTION:
        if w in t and w not in defined_terms:
            return True
    return False


# ---- 4. vague_summary ----
VAGUE_WORDS = ["还行", "再讨论", "可能需要", "对一下", "整体", "细节",
               "方向", "资源分配", "差不多", "再说", "看一下", "估摸"]


def chk_vague_summary(t: str) -> bool:
    """句长>60 ∧ 无专名 ∧ 无数字 ∧ 无绝对日期 ∧ 空泛词≥2"""
    if len(t) <= 60:
        return False
    if PROPER_NOUN.search(t):
        return False
    if re.search(r"\d", t) or re.search(r"[一二三四五六七八九十]", t):
        return False
    if re.search(r"\d{1,2}月\d{1,2}|\d{4}[-/年]", t):
        return False
    return sum(1 for w in VAGUE_WORDS if w in t) >= 2


def full_check(t: str, defined_terms: set) -> list[str]:
    sys.path.insert(0, _HERE)
    from run_e72_gate import fast_check
    base = fast_check(t)
    issues = list(base["issues"])
    if chk_missing_object(t) and "missing_object" not in issues:
        issues.append("missing_object")
    if chk_vague_value(t) and "vague_value" not in issues:
        issues.append("vague_value")
    if chk_undefined_convention(t, defined_terms) and \
            "undefined_convention" not in issues:
        issues.append("undefined_convention")
    if chk_vague_summary(t) and "vague_summary" not in issues:
        issues.append("vague_summary")
    return issues


def main():
    audit = json.load(open(os.path.join(TASK, "audit.json"), encoding="utf-8"))
    corpus = {r["rid"]: r["memory"] for r in
              json.load(open(os.path.join(TASK, "corpus.json"), encoding="utf-8"))}

    # 库内已定义的术语 = 出现过的具体约定（这里取所有记忆的并集，简化）
    defined = set()
    for t in corpus.values():
        for w in CONVENTION:
            # 若某条里该术语后面跟着具体内容，视为已定义
            if re.search(re.escape(w) + r"[\u4e00-\u9fa5\d]{2,}", t):
                defined.add(w)

    print("=" * 94)
    print("E-74 按模型 4 类规则重新审计")
    print("=" * 94)
    print(f"\n库内已定义约定术语: {defined or '（无）'}\n")

    per = {p["rid"]: p.get("verdict", "?") for p in audit.get("per_item", [])}
    print("-" * 94)
    print(f"{'rid':<6}{'模型判定':<22}{'新规则判定':<40}{'命中?':<8}{'内容'}")
    print("-" * 94)

    ok_items = [r for r, v in per.items() if v == "ok"]
    bad_items = [r for r, v in per.items() if v != "ok"]

    caught_bad = 0
    for rid in sorted(per):
        v = per[rid]
        txt = corpus.get(rid, "")
        issues = full_check(txt, defined)
        # 命中判定：新规则也认为有问题
        hit = len(issues) > 0
        if v != "ok" and hit:
            caught_bad += 1
        mark = ""
        if v == "ok" and hit:
            mark = "⚠误伤"
        elif v != "ok" and not hit:
            mark = "✗漏"
        elif v != "ok" and hit:
            mark = "✓"
        print(f"{rid:<6}{v:<22}{('/'.join(issues) or 'clean')[:38]:<40}{mark:<8}{txt[:24]}")
    print("-" * 94)

    false_pos = sum(1 for rid in ok_items if full_check(corpus[rid], defined))
    recall = caught_bad / len(bad_items) if bad_items else 0
    prec = caught_bad / (caught_bad + false_pos) if (caught_bad + false_pos) else 0
    f1 = 2 * prec * recall / (prec + recall) if (prec + recall) else 0

    print(f"\n模型认为有问题: {len(bad_items)} 条")
    print(f"新规则抓到:      {caught_bad} 条")
    print(f"误伤（模型说 ok 但规则报警）: {false_pos} 条")
    print()
    print(f"  召回率 {recall:.4f}")
    print(f"  精确率 {prec:.4f}")
    print(f"  F1     {f1:.4f}")
    print(f"\n  对比 E-73（只有原 3 类）：覆盖 1/18，漏 17")
    print(f"  现在：覆盖 {caught_bad}/{len(bad_items)}")

    out = {"experiment": "E-74",
           "n_total": len(per), "n_bad_by_model": len(bad_items),
           "caught": caught_bad, "false_pos": false_pos,
           "recall": round(recall, 4), "precision": round(prec, 4),
           "f1": round(f1, 4),
           "rule_order": ["bare_pronoun", "unanchored_time", "emotion_only",
                          "missing_object", "vague_value",
                          "undefined_convention", "vague_summary"]}
    with open(os.path.join(RESULTS, "e74_rules.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e74_rules.json')}")


if __name__ == "__main__":
    main()
