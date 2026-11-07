"""
E-82 identity 修正版

E-81 的 5 个错误：
  I02「小李是我的大学室友」→ 模型 True，规则 False
       bug: 正则只认"姓+称呼"，不认"小+姓"
  I07「那次出差回来总觉得累」→ 模型 False，规则 True
       bug: "出差"的"出"被当姓、"差"被当称呼
  I12「我表姐家的孩子」→ 模型 False，规则 True
       bug: 关系链跨两层，我当一阶处理
  I20「小区门口修车师傅姓王」→ 模型 False，规则 True
       bug: 姓氏+位置性角色，角色可换人
  I22「房东说下个月涨房租」→ 模型 True，规则 False
       bug: RELATION_SLOT 漏了"房东"

模型揭示的两条关键规则（我没掌握）：
  规则 A 关系链深度：一阶（我女儿/我妈）唯一；二阶（表姐家的孩子）不唯一
  规则 B 位置唯一性：「我老板姓张」True（固定关系槽唯一）；
          「修车师傅姓王」False（位置性角色可换人）
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.join(_HERE, "work", "e81")
RESULTS = os.path.join(_HERE, "results")

# ---------- 修正 1：称呼性专名（含"小+姓"） ----------
NAME_TITLE = re.compile(
    r"((?:[\u4e00-\u9fa5](?:工|总|经理|老师|医生|主任|主管))|"
    r"(?:小[\u4e00-\u9fa5]))")          # 张工、小张
FULL_NAME = re.compile(r"(李建国|陈静|张伟|王芳|刘洋|陈明)")
# 排除假阳性："出差""出问题"这类
FALSE_TITLE = re.compile(r"(出差|出来|出去|出错|出发|处理|主任|主管)")

# ---------- 修正 2：关系槽（一阶 vs 二阶） ----------
FIRST_ORDER_REL = re.compile(
    r"我(?:的)?(?:妈|爸|老婆|老公|女儿|儿子|姐|哥|弟|妹|孩子|"
    r"老板|房东|工牌|电脑|身份证|钱包|车|房|手机)")
# 二阶关系：表姐家的/表哥家的/亲戚的/朋友的
SECOND_ORDER_REL = re.compile(
    r"我(?:表姐|表哥|表妹|表弟|堂姐|堂哥|朋友|同事|同学|邻居)家的?")

# ---------- 修正 3：位置性角色（可换人，不算唯一） ----------
POSITIONAL_ROLE = re.compile(
    r"(修车师傅|前台|保安|服务员|司机|快递员|门口的?|楼下的?|"
    r"我们?组?的?\s*[\u4e00-\u9fa5]{0,4}(?:负责人|主管|组长))")
# 固定关系槽（唯一）
STABLE_SLOT = re.compile(
    r"我(?:的)?(?:老板|房东|领导|上司|妈|爸|老婆|老公)")

IDENTIFIER = re.compile(r"(\d{15,18}|[A-Za-z]-?\d{2,}|密码|工牌编号|身份证号|门牌|取件码)")
BOUND = re.compile(r"(我(?:的)?|[\u4e00-\u9fa5]{2,4}的)")
DEICTIC = re.compile(r"(这个|那个|那次|那天|那阵子|那件事|那边)")
PRECISE_COORD = re.compile(r"([\u4e00-\u9fa5]{2,8}(?:路|街|巷|道)\d+号)")
SURNAME_ONLY = re.compile(r"姓([\u4e00-\u9fa5])")


def m_identity_v2(text: str) -> tuple[bool, list[str]]:
    t = text or ""
    hits = []

    # ① 唯一标识符 + 主体绑定
    if IDENTIFIER.search(t):
        if BOUND.search(t):
            return True, ["id+bound"]
        return False, ["naked_id"]

    # ② 二阶关系 → 不唯一（优先于其他）
    if SECOND_ORDER_REL.search(t):
        return False, ["second_order_rel"]

    # ③ 位置性角色 → 不唯一（哪怕带姓）
    if POSITIONAL_ROLE.search(t):
        return False, ["positional_role"]

    # ④ 固定关系槽（老板/房东/亲属）→ 唯一
    if STABLE_SLOT.search(t):
        return True, ["stable_slot"]

    # ⑤ 称呼性专名（含"小+姓"），但要排除假阳性
    m = NAME_TITLE.search(t)
    if m and not FALSE_TITLE.fullmatch(m.group(1)):
        return True, ["name_title"]

    # ⑥ 全名
    if FULL_NAME.search(t):
        return True, ["fullname"]

    # ⑦ 一阶关系槽
    if FIRST_ORDER_REL.search(t):
        return True, ["first_order_rel"]

    # ⑧ 精确坐标
    if PRECISE_COORD.search(t):
        return True, ["coord"]

    # ⑨ 兜底：否
    return False, ["fallback_false"]


def main():
    rows = json.load(open(os.path.join(TASK, "corpus.json"), encoding="utf-8"))
    gold = {r["rid"]: r for r in
            json.load(open(os.path.join(TASK, "verdict.json"),
                           encoding="utf-8"))["per_item"]}

    print("=" * 96)
    print("E-82 identity 修正版（应用模型揭示的两条规则）")
    print("=" * 96)
    print("-" * 96)
    print(f"{'rid':<6}{'模型':<8}{'规则':<8}{'命中':<22}{'一致':<6}{'记忆'}")
    print("-" * 96)

    tp = fp = tn = fn = 0
    wrong = []
    for r in rows:
        rid = r["rid"]
        if rid not in gold:
            continue
        g = bool(gold[rid].get("identity"))
        p, hits = m_identity_v2(r["memory"])
        if g and p: tp += 1
        elif g and not p: fn += 1; wrong.append((rid, g, p, r["memory"],
                                                  gold[rid].get("reason", ""), hits))
        elif (not g) and p: fp += 1; wrong.append((rid, g, p, r["memory"],
                                                    gold[rid].get("reason", ""), hits))
        else: tn += 1
        print(f"{rid:<6}{str(g):<8}{str(p):<8}{'/'.join(hits)[:20]:<22}"
              f"{'✓' if g == p else '✗':<6}{r['memory'][:24]}")
    print("-" * 96)

    n = tp + fp + tn + fn
    acc = (tp + tn) / n
    prec = tp / (tp + fp) if (tp + fp) else 0
    rec = tp / (tp + fn) if (tp + fn) else 0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0
    print(f"\n  TP={tp} FP={fp} TN={tn} FN={fn}")
    print(f"  准确率 {acc:.4f}  精确率 {prec:.4f}  召回 {rec:.4f}  F1 {f1:.4f}")
    print(f"\n  对比：E-80 0.7083 → E-81 0.7917 → E-82 {acc:.4f}")

    if wrong:
        print("\n仍不一致：")
        for rid, g, p, txt, why, hits in wrong:
            print(f"  [{rid}] 模型={g} 规则={p}  命中={hits}")
            print(f"    {txt}")
            print(f"    模型理由: {why}")

    out = {"experiment": "E-82", "n": n, "tp": tp, "fp": fp, "tn": tn, "fn": fn,
           "accuracy": round(acc, 4), "precision": round(prec, 4),
           "recall": round(rec, 4), "f1": round(f1, 4),
           "baseline": {"e80": 0.7083, "e81": 0.7917},
           "mismatches": [{"rid": r, "model": g, "rule": p, "text": t,
                           "model_reason": w, "hits": h}
                          for r, g, p, t, w, h in wrong]}
    with open(os.path.join(RESULTS, "e82_identity_v2.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e82_identity_v2.json')}")


if __name__ == "__main__":
    main()
