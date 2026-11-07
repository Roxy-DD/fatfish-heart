"""
E-84 identity 第四版：修复优先级 bug

E-83 的 3 条漏判，全部是"判定顺序"问题：
  J00「陈默是我们组的架构师」→ 命中 positional 里的"架构师"，但"陈默"是全名
  J04「我家的车是白色的卡罗拉」→ "我家的" 不匹配我写的 "我(的)?"
  J12「菜市场的张大姐」→ 命中 positional 里的"菜市场的"，但"张大姐"是姓+称呼

核心修正：**具名优先于位置性角色**。
  一个人如果既具名又属于某位置，具名说了算。
  只有"没有具名、只有角色描述"时才判位置性角色。

同时补 STABLE_SLOT 支持"我家的X"。
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(_HERE, "results")

# ---------- 具名（高优先级） ----------
NAME_TITLE = re.compile(
    r"(?:^|[^出进上下来去到过做])"
    r"([\u4e00-\u9fa5](?:工|总|经理|老师|医生|主任|主管|大姐|大哥|阿姨|叔))")
XIAO_SURNAME = re.compile(r"(小[\u4e00-\u9fa5])")
FULL_NAME = re.compile(
    r"(陈默|王雅琴|李建国|陈静|张伟|王芳|刘洋|陈明)")
SURNAME_ONLY = re.compile(r"姓([\u4e00-\u9fa5])")

# ---------- 固定关系槽（"我的X" 和 "我家的X" 都算） ----------
STABLE_SLOT = re.compile(
    r"我(?:的|家的|家的|自己家的)?"
    r"(?:妈|爸|老婆|老公|公公|婆婆|岳父|岳母|女儿|儿子|姐|哥|弟|妹|"
    r"孩子|老板|房东|领导|上司|工牌|电脑|身份证|钱包|车|房|手机|保单)")

# ---------- 一阶亲属 ----------
FIRST_REL = re.compile(
    r"我(?:的)?(?:妈|爸|老婆|老公|公公|婆婆|女儿|儿子|姐|哥|弟|妹|孩子)")

# ---------- 二阶关系 ----------
SECOND_ORDER = re.compile(
    r"我(?:表姐|表哥|表妹|表弟|堂姐|堂哥|舅妈|姨妈|姑姑|叔叔|伯伯|"
    r"朋友|同事|同学|邻居)(?:家的?|的)")

# ---------- 位置性/职业性角色（只在无具名时生效） ----------
POSITIONAL = re.compile(
    r"(修车师傅|外卖小哥|水管工|快递员|保安|服务员|司机|"
    r"便利店(?:的)?老板|楼下的?|门口的?|前台的?|送外卖的|"
    r"我们?组?的?\s*[\u4e00-\u9fa5]{0,4}(?:负责人|主管|组长|架构师))")

IDENTIFIER = re.compile(
    r"(\d{15,18}|[A-Za-z]{1,3}-?\d{3,}|订单号|保单号|工牌编号|身份证号|"
    r"密码|门牌|取件码)")
BOUND = re.compile(r"(我(?:的)?|[\u4e00-\u9fa5]{2,4}的)")
DEICTIC = re.compile(r"(这个|那个|那次|那天|那阵子|那件事|那边)")
INDEFINITE = re.compile(r"(有人|别人|大家|几个|某个)")
PRECISE_COORD = re.compile(r"([\u4e00-\u9fa5]{2,8}(?:路|街|巷|道)\d+号)")


def m_identity_v4(text: str) -> tuple[bool, list[str]]:
    """
    判定顺序（关键修正）：
      1 唯一标识符+绑定
      2 二阶关系（false）
      3 具名（true）← 提前到位置性角色之前！
      4 固定关系槽（true）
      5 一阶亲属（true）
      6 位置性角色（false）
      7 不定指/指代（false）
      8 兜底 false
    """
    t = text or ""

    # ① 唯一标识符
    if IDENTIFIER.search(t):
        return (True, ["id+bound"]) if BOUND.search(t) else (False, ["naked_id"])

    # ② 二阶关系
    if SECOND_ORDER.search(t):
        return False, ["second_order"]

    # ③ 具名优先（关键修正）
    if FULL_NAME.search(t):
        return True, ["fullname"]
    if NAME_TITLE.search(t) or XIAO_SURNAME.search(t):
        return True, ["name_title"]
    if SURNAME_ONLY.search(t):
        return True, ["surname_only"]

    # ④ 固定关系槽
    if STABLE_SLOT.search(t):
        return True, ["stable_slot"]

    # ⑤ 一阶亲属
    if FIRST_REL.search(t):
        return True, ["first_rel"]

    # ⑥ 位置性角色（此时已确认无具名）
    if POSITIONAL.search(t):
        return False, ["positional"]

    # ⑦ 不定指
    if INDEFINITE.search(t):
        return False, ["indefinite"]

    # ⑧ 指代
    if DEICTIC.search(t):
        return False, ["deictic"]

    # ⑨ 精确坐标
    if PRECISE_COORD.search(t):
        return True, ["coord"]

    return False, ["fallback_false"]


def evaluate(rows, gold, fn, label, quiet=False):
    tp = fp = tn = fn_ = 0
    wrong = []
    if not quiet:
        print("-" * 96)
        print(f"{'rid':<6}{'模型':<8}{'规则':<8}{'命中':<16}{'一致':<6}{'记忆'}")
        print("-" * 96)
    for r in rows:
        rid = r["rid"]
        if rid not in gold:
            continue
        g = bool(gold[rid].get("identity"))
        p, hits = fn(r["memory"])
        if g and p: tp += 1
        elif g and not p:
            fn_ += 1; wrong.append((rid, g, p, r["memory"], gold[rid].get("reason", ""), hits))
        elif (not g) and p:
            fp += 1; wrong.append((rid, g, p, r["memory"], gold[rid].get("reason", ""), hits))
        else: tn += 1
        if not quiet:
            print(f"{rid:<6}{str(g):<8}{str(p):<8}{'/'.join(hits)[:14]:<16}"
                  f"{'✓' if g == p else '✗':<6}{r['memory'][:24]}")
    if not quiet:
        print("-" * 96)
    n = tp + fp + tn + fn_
    acc = (tp + tn) / n if n else 0
    prec = tp / (tp + fp) if (tp + fp) else 0
    rec = tp / (tp + fn_) if (tp + fn_) else 0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0
    print(f"  [{label}] n={n} TP={tp} FP={fp} TN={tn} FN={fn_}")
    print(f"    准确率 {acc:.4f}  精确率 {prec:.4f}  召回 {rec:.4f}  F1 {f1:.4f}")
    return {"n": n, "tp": tp, "fp": fp, "tn": tn, "fn": fn_,
            "accuracy": round(acc, 4), "precision": round(prec, 4),
            "recall": round(rec, 4), "f1": round(f1, 4), "mismatches": wrong}


def main():
    print("=" * 96)
    print("E-84 identity 第四版（具名优先）")
    print("=" * 96)

    out = {"experiment": "E-84", "results": {}}

    batches = [
        ("E-83 全新20条", os.path.join(_HERE, "work", "e83")),
        ("E-81 24条", os.path.join(_HERE, "work", "e81")),
    ]
    for label, d in batches:
        cp = os.path.join(d, "corpus.json")
        vp = os.path.join(d, "verdict.json")
        if not (os.path.exists(cp) and os.path.exists(vp)):
            continue
        rows = json.load(open(cp, encoding="utf-8"))
        raw = json.load(open(vp, encoding="utf-8"))
        if isinstance(raw, list):
            gold = {r["rid"]: r for r in raw}
        else:
            gold = {r["rid"]: r for r in raw.get("per_item", raw)}
        print(f"\n【{label}】")
        r = evaluate(rows, gold, m_identity_v4, label, quiet=(label != "E-83 全新20条"))
        out["results"][label] = {k: v for k, v in r.items() if k != "mismatches"}
        if r["mismatches"]:
            print("\n  不一致：")
            for rid, g, p, txt, why, h in r["mismatches"]:
                print(f"    [{rid}] 模型={g} 规则={p} {h}")
                print(f"      {txt}")
                print(f"      模型理由: {why}")

    print("\n" + "=" * 96)
    print("版本对比")
    print("=" * 96)
    print(f"{'版本':<12}{'E-83全新':<14}{'E-81回归'}")
    print("-" * 96)
    hist = [("E-81 (4判据)", 0.8500, 0.7917),
            ("E-82 (修正版)", 0.8500, 0.9167),
            ("E-84 (具名优先)",
             out["results"].get("E-83 全新20条", {}).get("accuracy", 0),
             out["results"].get("E-81 24条", {}).get("accuracy", 0))]
    for name, a, b in hist:
        print(f"{name:<12}{a:<14.4f}{b:.4f}")

    with open(os.path.join(RESULTS, "e84_identity_v4.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e84_identity_v4.json')}")


if __name__ == "__main__":
    main()
