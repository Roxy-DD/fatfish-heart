"""
E-53 评分：把模型答案与标准答案比对，统计各档位正确率。

判定：
  答对  = 标准答案串出现在模型答案中（归一化后）
  答错  = 给了答案但不对
  弃答  = 回答"无法确定"/"不确定"/空
  关键：弃答按错误计（不给分），因为它对用户没价值；
        但我们单独统计弃答率，以区分"答错"和"诚实说不知道"。
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.join(_HERE, "work", "e53_task")
RESULTS = os.path.join(_HERE, "results")

GROUPS = ["FULL", "TRUNC", "KEEP"]
ABSTAIN = ["无法确定", "不确定", "不知道", "无法判断", "不清楚", "无相关", "没有相关"]


def norm(s):
    s = str(s).strip()
    s = re.sub(r"\s+", "", s)
    s = s.replace("，", "").replace("。", "").replace("：", "").replace(":", "")
    return s.lower()


def is_abstain(s):
    s = norm(s)
    if not s:
        return True
    return any(norm(a) in s for a in ABSTAIN)


def main():
    report = {}
    print("=" * 92)
    print("E-53 端到端压缩验证 —— 评分")
    print("=" * 92)
    print(f"{'档位':<10}{'题数':<8}{'答对':<8}{'答错':<8}{'弃答':<8}"
          f"{'准确率':<10}{'弃答率'}")
    print("-" * 92)

    for g in GROUPS:
        af = os.path.join(TASK, f"{g}_answers.json")
        kf = os.path.join(TASK, f"{g}_key.json")
        if not os.path.exists(af):
            print(f"{g:<10}（答案文件缺失）")
            continue
        with open(af, encoding="utf-8") as f:
            ans = json.load(f)
        with open(kf, encoding="utf-8") as f:
            key = json.load(f)

        amap = {r["qid"]: r.get("answer", "") for r in ans}
        right = wrong = abst = 0
        detail = []
        for qid, kv in key.items():
            gold = kv["answer"]
            got = amap.get(qid, "")
            if qid not in amap:
                wrong += 1
                detail.append({"qid": qid, "gold": gold, "got": None, "verdict": "missing"})
                continue
            if is_abstain(got):
                abst += 1
                detail.append({"qid": qid, "gold": gold, "got": got, "verdict": "abstain"})
            elif norm(gold) in norm(got):
                right += 1
                detail.append({"qid": qid, "gold": gold, "got": got, "verdict": "right"})
            else:
                wrong += 1
                detail.append({"qid": qid, "gold": gold, "got": got, "verdict": "wrong"})

        n = len(key)
        acc = right / n if n else 0
        report[g] = {"n": n, "right": right, "wrong": wrong, "abstain": abst,
                     "accuracy": round(acc, 4),
                     "abstain_rate": round(abst / n, 4) if n else 0,
                     "detail": detail}
        print(f"{g:<10}{n:<8}{right:<8}{wrong:<8}{abst:<8}{acc:<10.4f}{abst/n:.4f}")

    print("-" * 92)

    # 对比
    if all(g in report for g in ["FULL", "TRUNC", "KEEP"]):
        fu = report["FULL"]["accuracy"]
        tr = report["TRUNC"]["accuracy"]
        kp = report["KEEP"]["accuracy"]
        print("\n对比（相对完整原文）：")
        print(f"  FULL   {fu:.4f}  （基准）")
        print(f"  TRUNC  {tr:.4f}  ({tr-fu:+.4f})  保留 54% 字符")
        print(f"  KEEP   {kp:.4f}  ({kp-fu:+.4f})  保留 65% 字符")
        print(f"\n  → 压缩后是否仍可用？")
        for name, v in [("TRUNC", tr), ("KEEP", kp)]:
            drop = fu - v
            if drop <= 0.05:
                tag = "几乎无损 ✓"
            elif drop <= 0.15:
                tag = "小幅下降"
            else:
                tag = "显著下降 ✗"
            print(f"    {name}: 下降 {drop:+.4f} → {tag}")

    with open(os.path.join(RESULTS, "e53_e2e.json"), "w", encoding="utf-8") as f:
        json.dump({"experiment": "E-53", "report":
                   {g: {k: v for k, v in r.items() if k != "detail"}
                    for g, r in report.items()},
                   "detail": {g: r["detail"] for g, r in report.items()}},
                  f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e53_e2e.json')}")


if __name__ == "__main__":
    main()
