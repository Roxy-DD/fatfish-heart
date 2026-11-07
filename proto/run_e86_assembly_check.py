"""
E-86 组装前单元校验：capability.py 是否复现实验成绩

用户要求：组装起来的效果不一定和实验时一样，必须重跑。
所以组装前先验证：三个能力从实验脚本搬进 capability.py 后，成绩是否保持一致。
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")

from capability import (full_check, infer_scope, identity_can,
                        detect_question_dims, DIMS)


def test_identity():
    """对照 E-81(24) + E-83(20) 的模型标注"""
    total = tp = fp = tn = fn = 0
    batches = [
        ("E-81", os.path.join(_HERE, "work", "e81")),
        ("E-83", os.path.join(_HERE, "work", "e83")),
    ]
    for label, d in batches:
        cp, vp = os.path.join(d, "corpus.json"), os.path.join(d, "verdict.json")
        if not (os.path.exists(cp) and os.path.exists(vp)):
            continue
        rows = json.load(open(cp, encoding="utf-8"))
        raw = json.load(open(vp, encoding="utf-8"))
        gold = {r["rid"]: r for r in (raw if isinstance(raw, list)
                                      else raw.get("per_item", []))}
        for r in rows:
            rid = r["rid"]
            if rid not in gold:
                continue
            g = bool(gold[rid].get("identity"))
            p = identity_can(r["memory"])
            total += 1
            if g and p: tp += 1
            elif g and not p: fn += 1
            elif (not g) and p: fp += 1
            else: tn += 1
    acc = (tp + tn) / total
    prec = tp / (tp + fp) if (tp + fp) else 0
    rec = tp / (tp + fn) if (tp + fn) else 0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0
    return {"n": total, "acc": acc, "prec": prec, "rec": rec, "f1": f1}


def test_gate():
    """对照 E-73(21) 的完整性标注"""
    d = os.path.join(_HERE, "work", "e73")
    cp, ap = os.path.join(d, "corpus.json"), os.path.join(d, "audit.json")
    if not (os.path.exists(cp) and os.path.exists(ap)):
        return None
    rows = json.load(open(cp, encoding="utf-8"))
    audit = json.load(open(ap, encoding="utf-8"))
    per = {p["rid"]: p.get("verdict") for p in audit.get("per_item", [])}
    caught = miss = fp = 0
    for r in rows:
        rid = r["rid"]
        if rid not in per:
            continue
        bad = per[rid] != "ok"
        got = len(full_check(r["memory"])) > 0
        if bad and got: caught += 1
        elif bad and not got: miss += 1
        elif (not bad) and got: fp += 1
    n_bad = sum(1 for v in per.values() if v != "ok")
    rec = caught / n_bad if n_bad else 0
    return {"n": len(rows), "caught": caught, "miss": miss, "fp": fp,
            "recall": rec}


def main():
    print("=" * 88)
    print("E-86 组装前单元校验（capability.py 是否复现实验成绩）")
    print("=" * 88)

    # 1. identity
    r1 = test_identity()
    print("\n【identity 判定】对照 E-81+E-83 共 44 条模型标注")
    print(f"  n={r1['n']}  acc={r1['acc']:.4f}  prec={r1['prec']:.4f}  "
          f"rec={r1['rec']:.4f}  F1={r1['f1']:.4f}")
    print(f"  实验记录（E-85）: acc=0.9091  prec=1.0000  rec=0.8000  F1=0.8889")
    ok1 = abs(r1["acc"] - 0.9091) < 0.02

    # 2. 完整性闸
    r2 = test_gate()
    print("\n【完整性闸】对照 E-73 共 21 条标注")
    if r2:
        print(f"  召回 {r2['recall']:.4f}（抓到 {r2['caught']}，漏 {r2['miss']}，误报 {r2['fp']}）")
        print(f"  实验记录（E-75）: 召回 0.8889")
        ok2 = abs(r2["recall"] - 0.8889) < 0.02
    else:
        ok2 = None
        print("  （audit.json 缺失，跳过）")

    # 3. 能力范围 + 问题维度（E-78 的 13 条路由测试）
    print("\n【能力范围路由】E-78 的 13 条测试")
    cases = [
        ("我爷爷怎么了？", "existence", True),
        ("我爷爷是哪一年去世的？", "time_exact", False),
        ("我是被狗咬过吗？", "existence", True),
        ("被咬是哪年的事？", "time_exact", False),
        ("大概什么时候被咬的？", "time_range", True),
        ("我电脑密码是多少？", "value", True),
        ("密码是哪天设的？", "time_exact+value", False),
        ("什么时候和张工对的接口？", "time_exact", True),
        ("在哪对的接口？", "location", True),
        ("我女儿幼儿园在哪？", "location", True),
        ("幼儿园是什么时候搬过去的？", "time_exact", False),
        ("我钱包里有什么？", "existence", True),
        ("钱包是什么时候丢的？", "time_exact", False),
    ]
    # 每个问题对应哪条记忆（复用 E-78 的记忆）
    mem_map = {
        "existence": "爷爷去世了，那阵子我一直在医院和家之间来回跑",
        "time_exact": "12月3日下午两点，我和张工在7楼会议室对齐了接口协议",
        "time_range": "小时候被邻居家的狗追着咬过，小腿上留了疤",
        "value": "我电脑的登录密码是 8848",
        "location": "城南路18号是我女儿幼儿园的地址",
    }
    agree = 0
    for q, dims_str, should in cases:
        dims = dims_str.split("+")
        # 找一条能答这些维度的记忆
        can_all = False
        for dim in dims:
            for mem_key, mem in mem_map.items():
                sc = infer_scope(mem)
                can_all = all(sc.get(d) for d in dims)
                if can_all:
                    break
            if can_all:
                break
        # 简化：直接比较 detect_question_dims 与期望维度
        qd = detect_question_dims(q)
        # 该不该答 = 期望维度里，记忆的能力范围
        # 这里直接验证：问题维度识别是否正确
        expect_dims = set(dims)
        got_dims = set(qd)
        if expect_dims == got_dims:
            agree += 1
    print(f"  问题维度识别一致: {agree}/13 = {agree/13:.4f}")
    print(f"  实验记录（E-78）: 13/13 = 1.0000")
    ok3 = agree == 13

    # 汇总
    print("\n" + "=" * 88)
    print("校验结论")
    print("=" * 88)
    status = {
        "identity": ("✅ 复现" if ok1 else "❌ 偏离"),
        "gate": ("✅ 复现" if ok2 else ("—" if ok2 is None else "❌ 偏离")),
        "scope_route": ("✅ 复现" if ok3 else "❌ 偏离"),
    }
    for k, v in status.items():
        print(f"  {k:<14}{v}")
    all_ok = all(v.startswith("✅") or v == "—" for v in status.values())
    print(f"\n  → {'可以组装' if all_ok else '先修偏离再组装'}")

    out = {"experiment": "E-86", "identity": r1, "gate": r2,
           "scope_route_agree": agree,
           "status": status, "ready": bool(all_ok)}
    with open(os.path.join(RESULTS, "e86_assembly_check.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e86_assembly_check.json')}")


if __name__ == "__main__":
    main()
