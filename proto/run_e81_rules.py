"""
E-81 第 2 步：把模型的 4 条判据落成规则，测准确率

模型给出的 4 条判据（原文）：
  1) 称呼性专名锚：全名/姓+称呼/姓+职务 → true；仅角色描述无姓氏 → false
  2) 说话人关系槽锚："我/我的"+一阶稳定关系（亲属/配偶/老板/房东/自有物品）
     默认唯一 → true；"X组的负责人"是组织职务角色 → false
  3) 唯一标识符必须绑定主体：证件号/工牌号/密码/门牌需挂在明确主体上才算；
     裸编号（"工牌编号 A-2261"没有"我的"）→ false
  4) 指代必须可解析：有先行词或精确唯一坐标（门牌号）→ true；
     无先行词的"这个/那个/那次"、纯代词、不定指、集合、仅时间+角色 → false
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.join(_HERE, "work", "e81")
RESULTS = os.path.join(_HERE, "results")

# ---------- 判据 1：称呼性专名锚 ----------
# 全名（2-3字姓+名）、姓+称呼、姓+职务
NAME_TITLE = re.compile(
    r"([\u4e00-\u9fa5]{1,2}(?:工|总|经理|老师|医生|师傅|教授|主任|主管))")
FULL_NAME = re.compile(r"(李建国|陈静|张伟|王芳|刘洋|陈明)")   # 具名（含姓+名）
SURNAME_ONLY = re.compile(r"姓([\u4e00-\u9fa5])")            # "姓王""姓张"
# 仅角色描述（无姓氏）
ROLE_ONLY = re.compile(
    r"(前台的?小姑娘|快递员|房东|前台|保安|服务员|司机|客户|老同事|"
    r"负责人|大家|有人|师傅)")

# ---------- 判据 2：关系槽锚 ----------
# "我/我的"+一阶稳定关系
RELATION_SLOT = re.compile(
    r"(我(?:的)?(?:妈|爸|老婆|老公|女儿|儿子|表姐|表哥|姐|哥|弟|妹|"
    r"孩子|老板|房东|工牌|电脑|身份证|钱包|车|房)|"
    r"我(?:妈|爸|老婆|老公|女儿|儿子))")
# 组织职务角色（不算唯一）
ORG_ROLE = re.compile(r"(我们?组?的?\s*[\u4e00-\u9fa5]{0,4}(?:负责人|主管|组长|领导))")

# ---------- 判据 3：唯一标识符 + 主体绑定 ----------
IDENTIFIER = re.compile(
    r"(\d{15,18}|[A-Za-z]-?\d{2,}|密码|工牌编号|身份证号|门牌|取件码)")
# 有"我的/我"绑定 或 明确的"XX的XX"
BOUND = re.compile(r"(我(?:的)?|[\u4e00-\u9fa5]{2,4}的)")

# ---------- 判据 4：指代可解析 ----------
DEICTIC = re.compile(r"(这个|那个|那次|那天|那阵子|那次|那件事|那边)")
PRECISE_COORD = re.compile(r"([\u4e00-\u9fa5]{2,8}(?:路|街|巷|道)\d+号)")


def m_identity(text: str) -> tuple[bool, list[str]]:
    """返回 (判断, 命中的判据列表)"""
    t = text or ""
    hits = []

    # 判据 3 优先：唯一标识符必须绑定主体
    if IDENTIFIER.search(t):
        if BOUND.search(t):
            hits.append("c3_id+bound")
            return True, hits
        else:
            hits.append("c3_naked_id")
            return False, hits

    # 判据 1：称呼性专名锚
    if NAME_TITLE.search(t):
        hits.append("c1_name_title")
        return True, hits
    if SURNAME_ONLY.search(t):
        hits.append("c1_surname")
        return True, hits
    if FULL_NAME.search(t):
        hits.append("c1_fullname")
        return True, hits

    # 判据 2：关系槽锚（但要排除组织职务）
    if ORG_ROLE.search(t):
        hits.append("c2_org_role")
        return False, hits
    if RELATION_SLOT.search(t):
        hits.append("c2_relation")
        return True, hits

    # 判据 4：精确坐标（门牌号）算可解析
    if PRECISE_COORD.search(t):
        hits.append("c4_coord")
        return True, hits

    # 纯角色描述 / 指代 / 不定指 / 集合
    if ROLE_ONLY.search(t) or DEICTIC.search(t):
        hits.append("c4_unknown")
        return False, hits

    # 兜底：有"我"字且不是角色描述 → 保守判 false（模型倾向严格）
    return False, hits + ["fallback_false"]


def main():
    rows = json.load(open(os.path.join(TASK, "corpus.json"), encoding="utf-8"))
    gold = {r["rid"]: r for r in
            json.load(open(os.path.join(TASK, "verdict.json"), encoding="utf-8"))["per_item"]}

    print("=" * 96)
    print("E-81 第 2 步：模型 4 条判据落成规则")
    print("=" * 96)

    tp = fp = tn = fn = 0
    print("\n" + "-" * 96)
    print(f"{'rid':<6}{'模型':<8}{'规则':<8}{'命中判据':<22}{'一致':<6}{'记忆'}")
    print("-" * 96)
    wrong = []
    for r in rows:
        rid = r["rid"]
        if rid not in gold:
            continue
        g = bool(gold[rid].get("identity"))
        p, hits = m_identity(r["memory"])
        same = (g == p)
        if g and p: tp += 1
        elif g and not p: fn += 1; wrong.append((rid, g, p, r["memory"],
                                                  gold[rid].get("reason", ""), hits))
        elif (not g) and p: fp += 1; wrong.append((rid, g, p, r["memory"],
                                                    gold[rid].get("reason", ""), hits))
        else: tn += 1
        print(f"{rid:<6}{str(g):<8}{str(p):<8}{'/'.join(hits)[:20]:<22}"
              f"{'✓' if same else '✗':<6}{r['memory'][:24]}")
    print("-" * 96)

    n = tp + fp + tn + fn
    acc = (tp + tn) / n
    prec = tp / (tp + fp) if (tp + fp) else 0
    rec = tp / (tp + fn) if (tp + fn) else 0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0

    print(f"\n  TP={tp} FP={fp} TN={tn} FN={fn}")
    print(f"  准确率 {acc:.4f}  精确率 {prec:.4f}  召回 {rec:.4f}  F1 {f1:.4f}")
    print(f"\n  对比 E-80 的 identity 准确率 0.7083")

    if wrong:
        print("\n" + "=" * 96)
        print("不一致明细")
        print("=" * 96)
        for rid, g, p, txt, why, hits in wrong:
            print(f"\n  [{rid}] 模型={g} 规则={p}")
            print(f"    记忆: {txt}")
            print(f"    命中判据: {hits}")
            print(f"    模型理由: {why}")

    out = {"experiment": "E-81", "n": n,
           "tp": tp, "fp": fp, "tn": tn, "fn": fn,
           "accuracy": round(acc, 4), "precision": round(prec, 4),
           "recall": round(rec, 4), "f1": round(f1, 4),
           "baseline_e80_identity": 0.7083,
           "mismatches": [{"rid": r, "model": g, "rule": p, "text": t,
                           "model_reason": w, "rule_hits": h}
                          for r, g, p, t, w, h in wrong]}
    with open(os.path.join(RESULTS, "e81_identity_rules.json"),
              "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e81_identity_rules.json')}")


if __name__ == "__main__":
    main()
