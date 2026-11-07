"""
E-69 评分：模型的自我判断可靠吗

指标（正例=能回答，负例=不能回答）：
  accuracy    总体准确率
  precision   判 yes 的里有多少真的是 yes
  recall      真 yes 里找出了多少
  F1
  specificity 真 no 里判对的比例

关键对照：
  全说 "no" 的基准准确率 = 负例占比（0.80）
  所以 accuracy 必须显著高于 0.80 才算有信息量。
  更有判别力的是 F1 和 recall。
"""
from __future__ import annotations

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.join(_HERE, "work", "e69_task")
RESULTS = os.path.join(_HERE, "results")


def main():
    truth = json.load(open(os.path.join(TASK, "truth.json"), encoding="utf-8"))
    preds = {}
    for f in ("judge_A.json", "judge_B.json"):
        p = os.path.join(TASK, f)
        if not os.path.exists(p):
            print(f"缺少 {f}")
            continue
        for r in json.load(open(p, encoding="utf-8")):
            preds[r["cid"]] = (str(r.get("verdict", "")).strip().lower() == "yes")

    common = [k for k in truth if k in preds]
    print("=" * 88)
    print("E-69 模型自我判断的可靠性")
    print("=" * 88)
    print(f"\n覆盖 {len(common)} / {len(truth)} 条候选\n")

    tp = fp = tn = fn = 0
    errors = []
    for cid in common:
        g = bool(truth[cid]); p = bool(preds[cid])
        if g and p: tp += 1
        elif g and not p:
            fn += 1
            errors.append((cid, g, p, "漏判（该 yes 判 no）"))
        elif not g and p:
            fp += 1
            errors.append((cid, g, p, "误判（该 no 判 yes）"))
        else: tn += 1

    n = tp + fp + tn + fn
    acc = (tp + tn) / n
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    spec = tn / (tn + fp) if (tn + fp) else 0.0
    base = (tn + fp) / n        # 全说 no 的准确率

    print("-" * 88)
    print(f"{'指标':<16}{'值':<12}{'说明'}")
    print("-" * 88)
    print(f"{'TP 判对为正例':<16}{tp:<12}")
    print(f"{'FP 误判为正例':<16}{fp:<12}")
    print(f"{'TN 判对为负例':<16}{tn:<12}")
    print(f"{'FN 漏判正例':<16}{fn:<12}")
    print("-" * 88)
    print(f"{'Accuracy':<16}{acc:<12.4f}（全说no的基准 {base:.4f}）")
    print(f"{'Precision':<16}{prec:<12.4f}")
    print(f"{'Recall':<16}{rec:<12.4f}")
    print(f"{'F1':<16}{f1:<12.4f}")
    print(f"{'Specificity':<16}{spec:<12.4f}")
    print("-" * 88)

    if errors:
        print("\n错误明细：")
        # 需要原文
        pack = json.load(open(os.path.join(TASK, "pack.json"), encoding="utf-8"))
        mem = {}
        qmap = {}
        for p in pack:
            qmap[p["qid"]] = p["question"]
            for c in p["candidates"]:
                mem[c["cid"]] = c["memory"]
        for cid, g, p, kind in errors[:15]:
            print(f"  [{cid}] {kind}")
            print(f"    问题: {qmap.get(cid.split('-')[0], '?')}")
            print(f"    记忆: {mem.get(cid, '?')[:56]}")

    print("\n" + "=" * 88)
    print("判决")
    print("=" * 88)
    print(f"  F1 = {f1:.3f}")
    if f1 >= 0.85:
        v = "闭环可行：模型能可靠判断「这条记忆对不对」，可用来自动调权"
    elif f1 >= 0.65:
        v = "部分可行：判断有信息量但不够可靠，需加约束（如只在高置信度时启用）"
    else:
        v = "不可行：模型自我判断也不可靠，这个方向要换"
    print(f"  → {v}")

    # 与"全说no"基准比较
    print(f"\n  准确率 {acc:.3f} vs 全说no基准 {base:.3f} "
          f"→ {'有信息量' if acc > base + 0.05 else '无信息量'}")

    out = {"experiment": "E-69", "n": n,
           "tp": tp, "fp": fp, "tn": tn, "fn": fn,
           "accuracy": round(acc, 4), "precision": round(prec, 4),
           "recall": round(rec, 4), "f1": round(f1, 4),
           "specificity": round(spec, 4),
           "baseline_all_no": round(base, 4), "verdict": v}
    with open(os.path.join(RESULTS, "e69_selfcheck.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e69_selfcheck.json')}")


if __name__ == "__main__":
    main()
