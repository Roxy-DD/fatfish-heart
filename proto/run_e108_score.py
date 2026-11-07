"""
E-108 评测：模型判断 vs 关键词法

指标：
  准确率（16 条整体判对的比例）
  精确率（模型说"该记"里真的该记的比例）
  召回率（真该记里模型找出的比例）
  F1

关键对比：
  关键词法 baseline = 0.5625（8 个该记只认出 1 个）
  模型判断 = ?
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.join(_HERE, "work", "e108")
RESULTS = os.path.join(_HERE, "results")


def main():
    gold = json.load(open(os.path.join(TASK, "gold.json"), encoding="utf-8"))
    mp = os.path.join(TASK, "model.json")
    if not os.path.exists(mp):
        print("缺少 model.json")
        return
    model = {r["cid"]: r for r in json.load(open(mp, encoding="utf-8"))}

    print("=" * 92)
    print("E-108 模型判断 vs 关键词法")
    print("=" * 92)

    tp = fp = tn = fn = 0
    print("\n" + "-" * 92)
    print(f"{'cid':<6}{'gold':<7}{'模型':<7}{'对?':<5}{'重要':<7}{'内容':<28}{'模型提取的记忆'}")
    print("-" * 92)

    for cid in sorted(gold):
        g = gold[cid]
        m = model.get(cid, {})
        gb = bool(g["gold"])
        mb = bool(m.get("remember"))
        imp = m.get("importance", "-")
        mt = m.get("text", "")

        if gb and mb: tp += 1
        elif gb and not mb: fn += 1
        elif (not gb) and mb: fp += 1
        else: tn += 1

        ok = "✓" if gb == mb else "✗"
        print(f"{cid:<6}{str(gb):<7}{str(mb):<7}{ok:<5}{str(imp):<7}"
              f"{g['text'][:26]:<28}{mt[:30]}")

    print("-" * 92)
    n = len(gold)
    acc = (tp + tn) / n
    prec = tp / (tp + fp) if (tp + fp) else 0
    rec = tp / (tp + fn) if (tp + fn) else 0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0

    print(f"\n  TP={tp} FP={fp} TN={tn} FN={fn}")
    print(f"  准确率 {acc:.4f}  精确率 {prec:.4f}  召回 {rec:.4f}  F1 {f1:.4f}")

    # 关键词法基线（重算，保持一致）
    REMEMBER_SIGNALS = [
        '记住', '别忘了', '别忘记', '记一下', '帮我记', '要记得', '记牢',
        '密码是', '密码为', '我的密码', '账号是', '卡号是', '电话是',
        '地址是', '生日是', '截止', '到期', '必须', '一定要', '保存',
    ]

    def kw(text):
        if not text or len(text) < 6 or len(text) > 400:
            return False
        if text.rstrip().endswith(('?', '？')):
            return False
        return any(s in text for s in REMEMBER_SIGNALS)

    ktp = kfp = ktn = kfn = 0
    for cid in gold:
        gb = bool(gold[cid]["gold"])
        kb = kw(gold[cid]["text"])
        if gb and kb: ktp += 1
        elif gb and not kb: kfn += 1
        elif (not gb) and kb: kfp += 1
        else: ktn += 1

    kacc = (ktp + ktn) / n
    kprec = ktp / (ktp + kfp) if (ktp + kfp) else 0
    krec = ktp / (ktp + kfn) if (ktp + kfn) else 0
    kf1 = 2 * kprec * krec / (kprec + krec) if (kprec + krec) else 0

    print("\n" + "=" * 92)
    print("对比")
    print("=" * 92)
    print(f"{'方法':<16}{'准确率':<10}{'精确率':<10}{'召回':<10}{'F1':<10}")
    print("-" * 92)
    print(f"{'关键词法':<16}{kacc:<10.4f}{kprec:<10.4f}{krec:<10.4f}{kf1:<10.4f}")
    print(f"{'模型判断':<16}{acc:<10.4f}{prec:<10.4f}{rec:<10.4f}{f1:<10.4f}")
    print("-" * 92)
    print(f"  提升: 准确率 {acc - kacc:+.4f}  召回 {rec - krec:+.4f}  "
          f"F1 {f1 - kf1:+.4f}")

    # 分析失败案例
    print("\n" + "-" * 92)
    print("模型判错的案例")
    print("-" * 92)
    any_wrong = False
    for cid in sorted(gold):
        gb = bool(gold[cid]["gold"])
        mb = bool(model.get(cid, {}).get("remember"))
        if gb != mb:
            any_wrong = True
            direction = "漏记（该记没记）" if gb else "误记（不该记却记）"
            print(f"  [{cid}] {direction}")
            print(f"    输入: {gold[cid]['text']}")
            print(f"    人工判断理由: {gold[cid]['note']}")
    if not any_wrong:
        print("  （无，16 条全对）")

    out = {"experiment": "E-108", "n": n,
           "model": {"acc": round(acc, 4), "prec": round(prec, 4),
                     "rec": round(rec, 4), "f1": round(f1, 4),
                     "tp": tp, "fp": fp, "tn": tn, "fn": fn},
           "keyword": {"acc": round(kacc, 4), "prec": round(kprec, 4),
                       "rec": round(krec, 4), "f1": round(kf1, 4),
                       "tp": ktp, "fp": kfp, "tn": ktn, "fn": kfn},
           "delta": {"acc": round(acc - kacc, 4), "rec": round(rec - krec, 4),
                     "f1": round(f1 - kf1, 4)}}
    with open(os.path.join(RESULTS, "e108_model_vs_keyword.json"),
              "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e108_model_vs_keyword.json')}")


if __name__ == "__main__":
    main()
