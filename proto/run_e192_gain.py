"""
E-192 全量精排收益预期（结合 E-189 可救回题数 × E-191 批量命中率）

E-189 全量可救回题数（基线漏掉但证据在 top-30）: 361 题
E-191 批量精排命中率: 99%

预期：
  全量精排能救回 361 × 0.99 ≈ 357 题
  基线 recall@10 = 654/1982 = 0.3300
  精排后 = (654 + 357) / 1982 ≈ 0.5101
  提升 ≈ +18.0pp

本实验把数字算准，并补上「结构化通道」的影响（E-189 基线 0.3300 是 structured=False，
而 E-166 全量是 0.5802 是 structured=True，需要澄清哪个是对的）。
"""
from __future__ import annotations

import json
import os

RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")

# 读取 E-189 全量可救回数据
e189 = json.load(open(os.path.join(RESULTS, "e189_full_upper.json"),
                      encoding="utf-8"))

base_hit = e189["base_hit"]
total = e189["total"]
pool_rec = e189["pool_recoverable"]

print("=" * 94)
print("E-192 全量精排收益预期")
print("=" * 94)

print(f"\n  基线（structured=False）: {base_hit}/{total} = {base_hit/total:.4f}")

print(f"\n  各候选池的精排预期（批量命中率 99%）:")
print(f"  {'候选池':<10}{'可救回':<10}{'预期救回(99%)':<16}{'精排后整体':<14}{'提升'}")
print("-" * 94)

for p in [15, 20, 30, 50]:
    n_rec = pool_rec.get(str(p), 0)
    saved = n_rec * 0.99
    after = (base_hit + saved) / total
    gain = after - base_hit / total
    print(f"  {p:<10}{n_rec:<10}{saved:<16.1f}{after:<14.4f}{gain:+.4f}")

# 关键：结构化通道的影响
print("\n" + "=" * 94)
print("结构化通道的影响（需要澄清）")
print("=" * 94)

print(f"""
  E-166 全量（structured=True，旧）: 0.5802
  E-189 全量（structured=False，新）: {base_hit/total:.4f}

  差异说明：
    E-169 实测 structured=True 在 3 个对话上是 -4.24pp（有害）
    但 E-166 全量（当时 structured 还是默认开）是 0.5802，
    而 E-189 全量（structured 关）是 {base_hit/total:.4f}。

  这两个数字矛盾！需要查清：
    · E-166 的 0.5802 是不是 structured=True 的"全量"结果？
    · 还是 E-166 和 E-189 之间有别的差异（如停用词修复时机）？

  时间线：
    E-164 停用词修复 → 之后 E-166 全量 0.5802
    E-168 英文实体抽取 → 0.5505
    E-169 结构化 A/B → 默认关
    E-189 全量（关）→ {base_hit/total:.4f}

  结论：E-166 的 0.5802 是在「停用词已修 + 结构化仍开」时测的。
        E-189 的 {base_hit/total:.4f} 是在「结构化关」时测的。
        两个不同配置，不能直接比。
""")

# 用 E-189 的基线 + 精排，这是当前正确配置下的数字
print("=" * 94)
print("当前正确配置下的精排收益")
print("=" * 94)

n30 = pool_rec.get("30", 0)
saved30 = n30 * 0.99
after30 = (base_hit + saved30) / total
print(f"\n  基线（结构化关）: {base_hit/total:.4f}")
print(f"  + 精排 top-30（99% 命中）: {after30:.4f}")
print(f"  提升: {after30 - base_hit/total:+.4f}")

out = {"experiment": "E-192",
       "baseline": round(base_hit / total, 4),
       "rerank_gains": {
           str(p): {"recoverable": pool_rec.get(str(p), 0),
                    "saved_99pct": round(pool_rec.get(str(p), 0) * 0.99, 1),
                    "after": round((base_hit + pool_rec.get(str(p), 0) * 0.99) / total, 4)}
           for p in [15, 20, 30, 50]
       }}
with open(os.path.join(RESULTS, "e192_gain_expect.json"), "w",
          encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 {os.path.join(RESULTS, 'e192_gain_expect.json')}")


if __name__ == "__main__":
    main()
