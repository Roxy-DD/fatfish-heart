"""
E-115 矛盾判定层（conflict.py）的准确率验证

验证两件事：
  1. classify_relation（语义关系词判定）在标注样本上的准确率
  2. judge（整套判定，含相似度阈值 0.50 + 语义细分）的准确率

标注样本：四类矛盾各 4 组，共 16 组
  期望：replace / merge / conflict / none
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
DB = os.path.join(_HERE, "e115_conflict.db")

# (旧记忆, 新记忆, 期望关系, 说明)
CASES = [
    # ---- replace：旧值作废 ----
    ("我电脑的登录密码是 8848", "我把电脑密码改成了 9988", "replace", "密码修改"),
    ("家里的门锁密码是 3721", "门锁密码换成了 9512", "replace", "门锁密码更换"),
    ("这个项目 15 号截止", "客户同意把截止延到 20 号", "replace", "延期"),
    ("我的车是白色卡罗拉", "我换车了现在是黑色大众", "replace", "换车"),

    # ---- merge：两条都成立 ----
    ("客户要求界面必须支持深色模式", "客户还要求界面必须支持大字体", "merge", "追加需求"),
    ("我每天早上要吃药", "另外我晚上也要吃一次", "merge", "追加习惯"),
    ("家里要买牛奶", "此外还要买鸡蛋", "merge", "追加采购"),

    # ---- conflict：不确定/冲突 ----
    ("老王说门锁密码是 3721", "小李说门锁密码是 9512", "conflict", "两个来源"),
    ("这个项目 15 号截止", "我记得好像是18号截止", "conflict", "记不清"),

    # ---- none：无关 ----
    ("我电脑的登录密码是 8848", "这个bug反复出现我快崩溃了", "none", "无关"),
    ("家里的门锁密码是 3721", "小时候被狗追着咬过", "none", "无关"),
    ("这个项目 15 号截止", "我妈打电话说家里都好", "none", "无关"),
    ("我的车是白色卡罗拉", "上周二午饭吃番茄炒蛋", "none", "无关"),
]


def main():
    from store_v2 import MemoryStore
    from store_v1 import Embedder
    import conflict

    for suf in ("", "-wal", "-shm"):
        p = DB + suf
        if os.path.exists(p):
            os.remove(p)

    st = MemoryStore(DB)
    emb = Embedder.get()

    print("=" * 88)
    print("E-115 矛盾判定层准确率")
    print("=" * 88)
    print(f"\n标注样本 {len(CASES)} 组\n")

    # ---------- ① 只看语义关系词 ----------
    print("-" * 88)
    print("① classify_relation（仅语义关系词）")
    print("-" * 88)
    print(f"{'期望':<12}{'实判':<12}{'对?':<6}{'依据':<30}{'新记忆'}")
    print("-" * 88)
    hit1 = 0
    for old, new, expect, note in CASES:
        rel, why = conflict.classify_relation(new)
        ok = (rel == expect) if expect != "none" else (rel == "none")
        hit1 += 1 if ok else 0
        print(f"{expect:<12}{rel:<12}{'✓' if ok else '✗':<6}{why[:28]:<30}{new[:26]}")
    print("-" * 88)
    acc1 = hit1 / len(CASES)
    print(f"准确率: {hit1}/{len(CASES)} = {acc1:.4f}")

    # ---------- ② 整套 judge（相似度 + 语义）----------
    print("\n" + "=" * 88)
    print("② judge（相似度阈值 0.50 + 语义细分）")
    print("=" * 88)

    # 先把所有"旧记忆"写入
    old_ids = {}
    for old, new, expect, note in CASES:
        if old not in old_ids:
            ep = st.add(old)
            old_ids[old] = ep.ep_id

    print(f"\n{'期望':<12}{'实判':<12}{'对?':<6}{'相似':<8}{'新记忆'}")
    print("-" * 88)
    hit2 = 0
    details = []
    for old, new, expect, note in CASES:
        v = conflict.judge(st, emb, new)
        ok = (v.relation == expect)
        hit2 += 1 if ok else 0
        mark = "✓" if ok else "✗"
        print(f"{expect:<12}{v.relation:<12}{mark:<6}{v.similarity:<8.3f}{new[:26]}")
        details.append({
            "old": old, "new": new, "expect": expect,
            "got": v.relation, "similarity": v.similarity,
            "reason": v.reason, "ok": ok,
        })
    print("-" * 88)
    acc2 = hit2 / len(CASES)
    print(f"准确率: {hit2}/{len(CASES)} = {acc2:.4f}")

    # ---------- ③ 四类分别的准确率 ----------
    print("\n" + "=" * 88)
    print("③ 分类型准确率")
    print("=" * 88)
    from collections import defaultdict
    by_type = defaultdict(lambda: [0, 0])
    for d in details:
        by_type[d["expect"]][1] += 1
        if d["ok"]:
            by_type[d["expect"]][0] += 1
    print(f"{'类型':<14}{'对/总':<12}{'准确率'}")
    print("-" * 88)
    for t in ["replace", "merge", "conflict", "none"]:
        if t in by_type:
            h, n = by_type[t]
            print(f"{t:<14}{f'{h}/{n}':<12}{h/n:.4f}")
    print("-" * 88)

    # ---------- ④ 失败分析 ----------
    fails = [d for d in details if not d["ok"]]
    if fails:
        print("\n失败案例：")
        for d in fails:
            print(f"  [{d['expect']} → {d['got']}] sim={d['similarity']:.3f}")
            print(f"    旧: {d['old']}")
            print(f"    新: {d['new']}")
            print(f"    理由: {d['reason']}")
    else:
        print("\n无失败案例")

    out = {"experiment": "E-115", "n": len(CASES),
           "classify_only_acc": round(acc1, 4),
           "full_judge_acc": round(acc2, 4),
           "by_type": {t: {"hit": v[0], "total": v[1]}
                       for t, v in by_type.items()},
           "details": details}
    with open(os.path.join(RESULTS, "e115_judge.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e115_judge.json')}")


if __name__ == "__main__":
    main()
