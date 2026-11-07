"""
E-83 第 2 步：修好的规则 vs 模型标注（全新语料，检验过拟合）

修正：
  I07 bug：「出差」的"出"被误判为姓 → 用明确的排除词表 + 匹配位置校验
  I22 bug：「房东」补进 STABLE_SLOT
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.join(_HERE, "work", "e83")
RESULTS = os.path.join(_HERE, "results")

# ---------- 修正版 identity 规则 ----------
# 姓+称呼：只允许"姓氏字 + 工/总/经理/老师/医生/主任/主管"
# 用 {1} 而非 {1,2}，避免"出差"这种双字误匹配
NAME_TITLE = re.compile(
    r"(?:^|[^出进上下来去到过])"          # 前面不能是动词字
    r"([\u4e00-\u9fa5](?:工|总|经理|老师|医生|主任|主管))")
XIAO_SURNAME = re.compile(r"(小[\u4e00-\u9fa5])")     # 小周、小李
FULL_NAME = re.compile(
    r"(陈默|王雅琴|李建国|陈静|张伟|王芳|刘洋|陈明|张大姐|周经理)")
SURNAME_ONLY = re.compile(r"姓([\u4e00-\u9fa5])")

# 固定关系槽（唯一）
STABLE_SLOT = re.compile(
    r"我(?:的)?(?:妈|爸|老婆|老公|公公|婆婆|岳父|岳母|女儿|儿子|"
    r"姐|哥|弟|妹|孩子|老板|房东|领导|上司|工牌|电脑|身份证|钱包|车|房|手机|保单)")

# 一阶亲属（直接）
FIRST_REL = re.compile(
    r"我(?:的)?(?:妈|爸|老婆|老公|公公|婆婆|女儿|儿子|姐|哥|弟|妹|孩子)")

# 二阶关系（表姐家的、同学的妈妈、舅妈家的表哥）
SECOND_ORDER = re.compile(
    r"我(?:表姐|表哥|表妹|表弟|堂姐|堂哥|舅妈|姨妈|姑姑|叔叔|伯伯|"
    r"朋友|同事|同学|邻居)(?:家的?|的)")

# 位置性/职业性角色（可换人 → false）
POSITIONAL = re.compile(
    r"(修车师傅|外卖小哥|水管工|水管工|快递员|保安|服务员|司机|"
    r"便利店(?:的)?老板|菜市场的?|楼下的?|门口的?|前台的?|送外卖的|"
    r"我们?组?的?\s*[\u4e00-\u9fa5]{0,4}(?:负责人|主管|组长|架构师))")

IDENTIFIER = re.compile(
    r"(\d{15,18}|[A-Za-z]{1,3}-?\d{3,}|订单号|保单号|工牌编号|身份证号|"
    r"密码|门牌|取件码)")
BOUND = re.compile(r"(我(?:的)?|[\u4e00-\u9fa5]{2,4}的)")
DEICTIC = re.compile(r"(这个|那个|那次|那天|那阵子|那件事|那边)")
INDEFINITE = re.compile(r"(有人|别人|大家|几个|某个|一个)")
PRECISE_COORD = re.compile(r"([\u4e00-\u9fa5]{2,8}(?:路|街|巷|道)\d+号)")


def m_identity_v3(text: str) -> tuple[bool, list[str]]:
    t = text or ""

    # ① 唯一标识符 + 主体绑定
    if IDENTIFIER.search(t):
        if BOUND.search(t):
            return True, ["id+bound"]
        return False, ["naked_id"]

    # ② 二阶关系 → false（优先）
    if SECOND_ORDER.search(t):
        return False, ["second_order"]

    # ③ 位置性角色 → false
    if POSITIONAL.search(t):
        return False, ["positional"]

    # ④ 不定指/集合 → false
    if INDEFINITE.search(t):
        return False, ["indefinite"]

    # ⑤ 固定关系槽 → true
    if STABLE_SLOT.search(t):
        return True, ["stable_slot"]

    # ⑥ 全名 → true
    if FULL_NAME.search(t):
        return True, ["fullname"]

    # ⑦ 姓+称呼 / 小姓 → true
    if NAME_TITLE.search(t) or XIAO_SURNAME.search(t):
        return True, ["name_title"]

    # ⑧ 仅姓氏
    if SURNAME_ONLY.search(t):
        return True, ["surname_only"]

    # ⑨ 一阶亲属
    if FIRST_REL.search(t):
        return True, ["first_rel"]

    # ⑩ 精确坐标
    if PRECISE_COORD.search(t):
        return True, ["coord"]

    # ⑪ 指代 → false
    if DEICTIC.search(t):
        return False, ["deictic"]

    return False, ["fallback_false"]


def evaluate(rows, gold, fn, label):
    tp = fp = tn = fn_ = 0
    wrong = []
    print("-" * 96)
    print(f"{'rid':<6}{'模型':<8}{'规则':<8}{'命中':<18}{'一致':<6}{'记忆'}")
    print("-" * 96)
    for r in rows:
        rid = r["rid"]
        if rid not in gold:
            continue
        g = bool(gold[rid].get("identity", gold[rid].get("ok"))) if isinstance(gold[rid], dict) else bool(gold[rid])
        p, hits = fn(r["memory"])
        if g and p: tp += 1
        elif g and not p:
            fn_ += 1; wrong.append((rid, g, p, r["memory"],
                                    gold[rid].get("reason", "") if isinstance(gold[rid], dict) else "", hits))
        elif (not g) and p:
            fp += 1; wrong.append((rid, g, p, r["memory"],
                                   gold[rid].get("reason", "") if isinstance(gold[rid], dict) else "", hits))
        else: tn += 1
        print(f"{rid:<6}{str(g):<8}{str(p):<8}{'/'.join(hits)[:16]:<18}"
              f"{'✓' if g == p else '✗':<6}{r['memory'][:24]}")
    print("-" * 96)
    n = tp + fp + tn + fn_
    acc = (tp + tn) / n if n else 0
    prec = tp / (tp + fp) if (tp + fp) else 0
    rec = tp / (tp + fn_) if (tp + fn_) else 0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0
    print(f"\n  [{label}] TP={tp} FP={fp} TN={tn} FN={fn_}")
    print(f"  准确率 {acc:.4f}  精确率 {prec:.4f}  召回 {rec:.4f}  F1 {f1:.4f}")
    return {"n": n, "tp": tp, "fp": fp, "tn": tn, "fn": fn_,
            "accuracy": round(acc, 4), "precision": round(prec, 4),
            "recall": round(rec, 4), "f1": round(f1, 4),
            "mismatches": wrong}


def main():
    print("=" * 96)
    print("E-83 交叉验证：修好的规则 vs 模型标注（全新语料）")
    print("=" * 96)

    out = {"experiment": "E-83"}

    # ---- 全新第三批 ----
    rows3 = json.load(open(os.path.join(TASK, "corpus.json"), encoding="utf-8"))
    v3p = os.path.join(TASK, "verdict.json")
    if os.path.exists(v3p):
        raw = json.load(open(v3p, encoding="utf-8"))
        gold3 = {r["rid"]: r for r in raw} if isinstance(raw, list) else raw
        print(f"\n【第三批·全新 20 条】")
        r = evaluate(rows3, gold3, m_identity_v3, "E-83 全新")
        out["batch3"] = {k: v for k, v in r.items() if k != "mismatches"}
        if r["mismatches"]:
            print("\n  不一致：")
            for rid, g, p, txt, why, h in r["mismatches"]:
                print(f"    [{rid}] 模型={g} 规则={p} {h}")
                print(f"      {txt}")
                print(f"      模型理由: {why}")
    else:
        print("\n（第三批 verdict 尚未生成）")

    # ---- 回归测试：前两批是否退化 ----
    print("\n" + "=" * 96)
    print("回归测试：前两批成绩是否保持")
    print("=" * 96)

    p1 = os.path.join(_HERE, "work", "e81")
    if os.path.exists(os.path.join(p1, "corpus.json")):
        rows1 = json.load(open(os.path.join(p1, "corpus.json"), encoding="utf-8"))
        g1raw = json.load(open(os.path.join(p1, "verdict.json"),
                               encoding="utf-8"))["per_item"]
        gold1 = {r["rid"]: r for r in g1raw}
        print(f"\n【第一批 E-81 24 条】")
        r = evaluate(rows1, gold1, m_identity_v3, "E-81 回归")
        out["batch1"] = {k: v for k, v in r.items() if k != "mismatches"}

    with open(os.path.join(RESULTS, "e83_crossval.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e83_crossval.json')}")


if __name__ == "__main__":
    main()
