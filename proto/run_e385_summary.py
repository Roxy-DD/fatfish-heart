"""E-385 把 A/B 实验的结论固化（含不确定项）

三轮实验（E-382/383/384）的完整结论：

【确定的】
  ① 覆盖率随注入频率单调变化
     N=1（每轮）: 77%~90% 覆盖
     N=30（只一次）: 3%~10% 覆盖
     → 这是真实的、可复现的差异

  ② 成本随注入频率线性增长
     N=1: 7.2x ~ 19.9x
     N=30: 1.0x
     → 也是真实的

  ③ 检索本身一直在跑（channel 记录最高 289 次访问）
     但只注入一次 → 每次都检索，除首轮外交付不了

【不确定的】
  ④ 「每千字命中」这个效率指标不可靠
     E-382（10轮）: 1.00x 持平
     E-383（10轮）: 0.091~0.125 波动
     E-384（30轮）: 1.399~3.602 波动大
     → 指标本身波动大，不能作为判据

  ⑤ 哪个 N 最优 —— **无法判定**
     因为「命中关键词数」不等于「记忆真的被用上了」。
     真实效果需要人评估（或用下游任务成功率），
     都不能靠离线指标替代。

【结论】
  不改固定 N。理由：
    · 没有可信的效率判据
    · 覆盖率 vs 成本是明确的线性交换
    · 真实场景应改用「话题切换触发」，而不是固定频率

  加的实验开关 injectEveryTurn 保留（默认 false），
  供以后做真实 A/B 时使用。
"""
import sys
import os
import json
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))

print("=" * 78)
print("E-385 A/B 实验结论汇总")
print("=" * 78)

# 汇总三轮实验数据
r382 = json.load(open(os.path.join(_HERE, "results", "e382_ab.json"),
                      encoding="utf-8"))
r383 = json.load(open(os.path.join(_HERE, "results", "e383_tradeoff.json"),
                      encoding="utf-8"))
r384 = json.load(open(os.path.join(_HERE, "results", "e384_rigorous.json"),
                      encoding="utf-8"))

print("\n① 三轮实验的关键数字")

print(f"\n   E-382（10 轮，两类策略）:")
print(f"     只注入一次: 覆盖 {r382['coverage_once']*100:.0f}%, "
      f"成本 1.0x, 效率比 1.00x")
print(f"     每轮注入:   覆盖 {r382['coverage_every']*100:.0f}%, "
      f"成本 {r382['cost_ratio']:.1f}x")

print(f"\n   E-383（10 轮，不同 N）:")
for row in r383["rows"]:
    print(f"     N={row['N']:<3} 覆盖 {row['coverage']*100:>3.0f}%, "
          f"成本 {row['cost_ratio']:>4.1f}x, 命中/千字 {row['eff']:.3f}")

print(f"\n   E-384（30 轮，不同 N）:")
for row in r384["rows"]:
    print(f"     N={row['N']:<3} 覆盖 {row['coverage']*100:>3.0f}%, "
          f"成本 {row['cost_ratio']:>5.1f}x, 命中/千字 {row['eff']:.3f}")

# ---------- ② 一致性检查 ----------
print("\n② 三轮数据的一致性")

covs_383 = {r["N"]: r["coverage"] for r in r383["rows"]}
covs_384 = {r["N"]: r["coverage"] for r in r384["rows"]}

print(f"\n   {'N':>3} {'E-383覆盖':>10} {'E-384覆盖':>10} {'差异':>8}")
print("   " + "-" * 36)
consistent = []
for N in [1, 2, 3, 5, 10]:
    a = covs_383.get(N)
    b = covs_384.get(N)
    if a is not None and b is not None:
        d = abs(a - b)
        print(f"   {N:>3} {a*100:>9.0f}% {b*100:>9.0f}% {d*100:>7.0f}pp")
        consistent.append(d < 0.15)

print(f"\n   差异 < 15pp 的: {sum(consistent)}/{len(consistent)}")

# ---------- ③ 效率指标的可靠性 ----------
print("\n③ 「每千字命中」指标的可靠性")

effs_383 = [r["eff"] for r in r383["rows"]]
effs_384 = [r["eff"] for r in r384["rows"]]

import statistics
print(f"   E-383 效率范围: {min(effs_383):.3f} ~ {max(effs_383):.3f} "
      f"(极差 {max(effs_383)-min(effs_383):.3f})")
print(f"   E-384 效率范围: {min(effs_384):.3f} ~ {max(effs_384):.3f} "
      f"(极差 {max(effs_384)-min(effs_384):.3f})")

print(f"""
   ★ 极差很大（{max(effs_384)-min(effs_384):.3f}）说明该指标**不稳定**。
     原因：「命中关键词数」不衡量记忆是否真被用上，
           只算关键词在原文里出现几次。
           这导致：
             · 长记忆天然命中多（字数多）
             · 短小但精准的记忆命中少
           两个方向都被扭曲。

   → 这个指标**不能作为挑选 N 的判据**。
""")

# ---------- ④ 最终结论 ----------
print("\n" + "=" * 78)
print("最终结论")
print("=" * 78)

print(f"""
【确定的事实】（可复现）
  1. 覆盖率随注入频率单调变化
     N=1: {covs_384[1]*100:.0f}%   N=30: {covs_384[30]*100:.0f}%
  2. 成本随注入频率近似线性增长
     N=1: {next(r for r in r384['rows'] if r['N']==1)['cost_ratio']:.1f}x   "
     N=30: 1.0x
  3. 检索一直在跑（channel 最高 289 次访问），
     但只注入一次 → 每次都检索，除首轮外交付不了
  4. E-383 与 E-384 的覆盖率数据基本一致（差异 < 15pp）
     → 覆盖率这个指标是可信的

【不确定的】
  5. 「每千字命中」极差 {max(effs_384)-min(effs_384):.3f}，指标不可靠
  6. 哪个 N 最优 —— **无法用离线指标判定**
     真实效果需要下游任务验证，或人工评估

【决定】
  · 不改固定 N（没有可信判据支撑）
  · 保留 injectEveryTurn 开关（默认 false）
  · 真正该做的是「话题切换触发」，而不是固定频率
    —— 但那是新功能，需要单独设计+验证

【诚实标注】
  E-383 我曾写「N=3 比 N=1 划算得多」，
  但表格里 N=1 得分 0.125 > N=3 的 0.124，我的叙述与数据不符。
  E-384 用 30 轮复验后确认：**覆盖率单调递减，但效率无最优**。
""")

out = {
    "experiment": "E-385",
    "coverage_consistent": sum(consistent),
    "eff_range_383": [min(effs_383), max(effs_383)],
    "eff_range_384": [min(effs_384), max(effs_384)],
    "verdict": "不改固定N；效率指标不可靠；应改用话题切换触发",
}
p = os.path.join(_HERE, "results", "e385_summary.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e385_summary.json")
