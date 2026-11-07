"""
E-152 评分：模型判断情绪 vs 规则法 vs 人工标注
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.join(_HERE, "work", "e152")
RESULTS = os.path.join(_HERE, "results")

sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))


def main():
    packs = json.load(open(os.path.join(TASK, "packs.json"), encoding="utf-8"))
    mp = os.path.join(TASK, "model.json")
    if not os.path.exists(mp):
        print("缺少 model.json")
        return
    model = {r["cid"]: r for r in json.load(open(mp, encoding="utf-8"))}

    # 人工标注
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "e152", os.path.join(_HERE, "run_e152_affect_model.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    gold = {f"A{i:02d}": (g, k) for i, (_, g, k) in enumerate(mod.SAMPLES)}

    from affect_v3 import estimate_affect_v3

    print("=" * 96)
    print("E-152 模型判情绪 vs 规则法")
    print("=" * 96)

    print(f"\n{'cid':<6}{'类型':<10}{'人工':<8}{'规则':<8}{'模型':<8}"
          f"{'模型-人工':<12}{'文本'}")
    print("-" * 96)

    rows = []
    for p in packs:
        cid = p["cid"]
        text = p["text"]
        g, kind = gold[cid]
        rule = float(estimate_affect_v3(text, explicit=False).get("affect_word") or 0)
        m = model.get(cid, {})
        pred = float(m.get("affect") or 0)
        val = int(m.get("valence") or 0)

        diff = pred - g
        rows.append({
            "cid": cid, "kind": kind, "text": text,
            "gold": g, "rule": round(rule, 2), "model": round(pred, 2),
            "diff": round(diff, 2), "val": val,
            "reason": m.get("reason", ""),
        })

        flag = " ⚠" if abs(diff) >= 0.3 else ""
        print(f"{cid:<6}{kind:<10}{g:<8.2f}{rule:<8.2f}{pred:<8.2f}"
              f"{diff:<+12.2f}{text[:30]}{flag}")

    n = len(rows)
    n_pos = sum(1 for r in rows if r["gold"] > 0.3)
    n_neg = n - n_pos

    def stats(key):
        mae = sum(abs(r[key] - r["gold"]) for r in rows) / n
        miss = sum(1 for r in rows if r["gold"] > 0.3 and r[key] < 0.3)
        false = sum(1 for r in rows if r["gold"] <= 0.3 and r[key] >= 0.3)
        # 相关系数
        xs = [r[key] for r in rows]
        ys = [r["gold"] for r in rows]
        mx, my = sum(xs) / n, sum(ys) / n
        num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        dx = sum((x - mx) ** 2 for x in xs) ** 0.5
        dy = sum((y - my) ** 2 for y in ys) ** 0.5
        corr = num / (dx * dy) if dx and dy else 0
        return {"mae": round(mae, 4), "miss": miss, "false": false,
                "corr": round(corr, 4)}

    rule_s = stats("rule")
    model_s = stats("model")

    print("\n" + "=" * 96)
    print("对比")
    print("=" * 96)
    print(f"\n{'指标':<20}{'规则法':<16}{'模型判断':<16}{'改善'}")
    print("-" * 96)
    print("%-20s%-16.4f%-16.4f%+.4f" % (
        "MAE（越低越好）", rule_s["mae"], model_s["mae"],
        rule_s["mae"] - model_s["mae"]))
    print("%-20s%-16s%-16s%+d" % (
        "漏判（有情绪判 0）",
        "%d/%d" % (rule_s["miss"], n_pos),
        "%d/%d" % (model_s["miss"], n_pos),
        model_s["miss"] - rule_s["miss"]))
    print("%-20s%-16s%-16s%+d" % (
        "误判（无情绪判有）",
        "%d/%d" % (rule_s["false"], n_neg),
        "%d/%d" % (model_s["false"], n_neg),
        model_s["false"] - rule_s["false"]))
    print("%-20s%-16.4f%-16.4f%+.4f" % (
        "相关系数（越高越好）", rule_s["corr"], model_s["corr"],
        model_s["corr"] - rule_s["corr"]))

    # 分类看
    print("\n" + "-" * 96)
    print("按类型看模型表现")
    print("-" * 96)
    from collections import defaultdict
    by = defaultdict(lambda: {"n": 0, "err": 0.0})
    for r in rows:
        by[r["kind"]]["n"] += 1
        by[r["kind"]]["err"] += abs(r["diff"])
    print(f"{'类型':<12}{'条数':<8}{'模型平均误差'}")
    print("-" * 96)
    for k in sorted(by, key=lambda x: -by[x]["err"] / by[x]["n"]):
        d = by[k]
        print(f"{k:<12}{d['n']:<8}{d['err'] / d['n']:.3f}")

    # 大偏差案例
    bad = [r for r in rows if abs(r["diff"]) >= 0.3]
    if bad:
        print(f"\n" + "-" * 96)
        print(f"模型偏差 >=0.3 的案例（{len(bad)} 条）")
        print("-" * 96)
        for r in bad:
            print(f"  {r['cid']} gold={r['gold']:.2f} model={r['model']:.2f}")
            print(f"    文本: {r['text'][:60]}")
            print(f"    模型理由: {r['reason'][:66]}")

    # ---------- 结论 ----------
    print("\n" + "=" * 96)
    print("结论")
    print("=" * 96)
    print(f"\n  规则法: MAE {rule_s['mae']:.4f}  漏判 {rule_s['miss']}/{n_pos}  "
          f"相关 {rule_s['corr']:.3f}")
    print(f"  模型:   MAE {model_s['mae']:.4f}  漏判 {model_s['miss']}/{n_pos}  "
          f"相关 {model_s['corr']:.3f}")
    print(f"\n  MAE 改善: {rule_s['mae'] - model_s['mae']:+.4f}")
    print(f"  漏判减少: {rule_s['miss'] - model_s['miss']} 条")
    print(f"  相关性提升: {model_s['corr'] - rule_s['corr']:+.4f}")

    better = model_s["mae"] < rule_s["mae"] * 0.6
    print(f"\n  → {'✅ 模型判断显著优于规则法（MAE 降低 40% 以上）' if better else '需进一步分析'}")

    out = {"experiment": "E-152", "n": n,
           "n_pos": n_pos, "n_neg": n_neg,
           "rule": rule_s, "model": model_s,
           "detail": rows}
    with open(os.path.join(RESULTS, "e152_affect_model.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e152_affect_model.json')}")


if __name__ == "__main__":
    main()
