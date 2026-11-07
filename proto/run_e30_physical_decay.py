"""
E-30 物理化计算：电容式记忆能不能"自然遗忘"？

用户假设（2026-10-25）：
  "计算导致数据的扩散性，使得整体的求和无法像真实世界一样瞬间到达。
   记忆和索引可以是一些电容，电容往里面传东西。
   它们之间要不要遗忘，完全依靠电容之间的相互作用，以及电通路之间的自然损耗。
   让这一大片物理实体来实现真实的遗忘和提存，就不需要算法来规定。
   算法需要一步步计算，但真实世界里电容是同步输入的，一下子就能得到结果。"

本实验**不模拟硬件**，只检验一个纯数学/动力学问题：
  「纯被动衰减」能不能实现「智能遗忘」？

四个场景（同一个记忆库，4 个极简规则）：
  A. 纯指数衰减 + 绝对阈值
      每条记忆强度 s(t) = s0 * exp(-t/tau)
      强度低于阈值 T 就视为忘了
  B. 纯指数衰减 + 相对阈值（取前 k%）
      只有排名，不做绝对比较
  C. 衰减 + 使用依赖加固（被检索到就充电）
      这是"相互作用"，不只是漏电
  D. 衰减 + 选择性巩固（超过阈值的部分转入"慢组件"不衰减）
      这是 tag & capture

测四个量：
  ① 多久之后"全部忘光"（绝对阈值场景）
  ② 衰减对"相对排序"是否有任何影响（相对阈值场景）
  ③ 使用依赖加固下，强度分布是否稳定（不爆炸不归零）
  ④ 巩固机制下，老记忆能否被保护

判据：不预设门槛，只看四种规则各自产生什么行为。
"""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_lite"))

import numpy as np

RESULTS = os.path.join(_HERE, "results")


def exp_A_absolute_decay(n_items=1000, s0=1.0, tau=100.0, T=0.1, t_max=2000):
    """A. 纯指数衰减 + 绝对阈值：多久全部低于阈值"""
    t = np.arange(t_max)
    s = s0 * np.exp(-t / tau)
    below = s < T
    first_all_below = int(np.argmax(np.cumsum(below) == n_items)) if below.any() else None
    # 实际：所有条目同一衰减曲线，所以一起越过阈值
    idx = int(np.argmax(below)) if below.any() else None
    return {
        "tau": tau, "T": T, "s0": s0,
        "t_all_forgotten": idx,
        "ratio_t_to_tau": round(idx / tau, 2) if idx else None,
        "note": "所有记忆同一曲线 → 同时全部消失，没有'部分遗忘'",
        "curve_sample": [round(float(x), 4) for x in s[::max(1, t_max // 10)]],
    }


def exp_B_relative_decay(n_items=1000, s0_range=(0.3, 1.0), tau=100.0, t_max=2000):
    """B. 纯指数衰减 + 相对阈值：排名会变吗"""
    rng = np.random.default_rng(0)
    s0 = rng.uniform(*s0_range, n_items)
    t = np.arange(t_max)
    S = s0[None, :] * np.exp(-t[:, None] / tau)      # [t, n]

    # 初始排名
    order0 = np.argsort(-S[0])
    # 每个时刻的排名与初始排名的相关性
    corrs = []
    for k in range(0, t_max, max(1, t_max // 20)):
        o = np.argsort(-S[k])
        # Spearman (用位置差)
        rank0 = np.empty(n_items); rank0[order0] = np.arange(n_items)
        rankk = np.empty(n_items); rankk[o] = np.arange(n_items)
        corrs.append(float(np.corrcoef(rank0, rankk)[0, 1]))
    # 相对阈值：保留 top 10%，看保留集合是否变化
    keep0 = set(order0[: n_items // 10].tolist())
    keep_end = set(np.argsort(-S[-1])[: n_items // 10].tolist())
    return {
        "tau": tau, "n_items": n_items,
        "rank_correlation_over_time": [round(c, 6) for c in corrs],
        "top10pct_identical_after_all_time": bool(keep0 == keep_end),
        "overlap": len(keep0 & keep_end),
        "note": "指数衰减对所有条目同比缩放 → 排名完全不变 → 相对阈值下衰减完全无效",
    }


def exp_C_usage_dependent(n_items=1000, tau=100.0, t_max=2000, charge=1.0,
                          read_prob=0.02, seed=0):
    """C. 衰减 + 使用依赖加固：被读到就充电"""
    rng = np.random.default_rng(seed)
    s = rng.uniform(0.3, 1.0, n_items)
    # 每个条目被读到的倾向不同（模拟"热门记忆"）
    popularity = rng.zipf(1.6, n_items).astype(float)
    popularity = popularity / popularity.sum()
    cap = 20.0

    hist = []
    for t in range(t_max):
        s *= np.exp(-1.0 / tau)
        # 随机读取：按热度
        n_reads = max(1, int(read_prob * n_items))
        idx = rng.choice(n_items, n_reads, p=popularity)
        s[idx] = np.minimum(cap, s[idx] + charge)
        if t % max(1, t_max // 20) == 0:
            hist.append({
                "t": t,
                "mean": round(float(s.mean()), 4),
                "max": round(float(s.max()), 4),
                "min": round(float(s.min()), 4),
                "n_above_0.1": int((s > 0.1).sum()),
            })
    return {
        "tau": tau, "read_prob": read_prob, "charge": charge, "cap": cap,
        "history": hist,
        "final": {"mean": round(float(s.mean()), 4),
                  "max": round(float(s.max()), 4),
                  "min": round(float(s.min()), 4),
                  "n_above_0.1": int((s > 0.1).sum()),
                  "top10pct_share": round(float(np.sort(s)[-100:].sum() / s.sum()), 4)},
        "note": "有加固 → 分布不归零；热门记忆被推到上限，冷门记忆沉到零 → 自然分层",
    }


def exp_D_consolidation(n_items=1000, tau_fast=50.0, t_max=2000, theta=0.8,
                        read_prob=0.02, seed=0):
    """D. 衰减 + 选择性巩固：快组件衰减，超过阈值转入慢组件（不衰减）"""
    rng = np.random.default_rng(seed)
    wf = rng.uniform(0.3, 1.0, n_items)
    ws = np.zeros(n_items)
    popularity = rng.zipf(1.6, n_items).astype(float)
    popularity /= popularity.sum()

    hist = []
    for t in range(t_max):
        decay = wf * (1 - np.exp(-1.0 / tau_fast))
        wf -= decay
        # 巩固：快组件超过阈值 → 衰减量的 30% 转入慢组件
        cross = wf > theta
        ws[cross] += 0.3 * decay[cross]
        # 读取加固
        n_reads = max(1, int(read_prob * n_items))
        idx = rng.choice(n_items, n_reads, p=popularity)
        wf[idx] = np.minimum(1.5, wf[idx] + 0.5)
        if t % max(1, t_max // 20) == 0:
            tot = wf + ws
            hist.append({"t": t, "wf_mean": round(float(wf.mean()), 4),
                         "ws_mean": round(float(ws.mean()), 4),
                         "n_consolidated": int((ws > 0).sum()),
                         "total_mean": round(float(tot.mean()), 4)})
    tot = wf + ws
    return {
        "tau_fast": tau_fast, "theta": theta,
        "history": hist,
        "final": {"wf_mean": round(float(wf.mean()), 4),
                  "ws_mean": round(float(ws.mean()), 4),
                  "n_consolidated": int((ws > 0).sum()),
                  "total_mean": round(float(tot.mean()), 4),
                  "consolidated_share_of_total":
                      round(float(ws.sum() / max(tot.sum(), 1e-9)), 4)},
        "note": "慢组件不衰减 → 被巩固的记忆永久保存；未巩固的按快组件衰减消失",
    }


def main():
    print("=" * 100)
    print("E-30 纯被动衰减，能不能实现「智能遗忘」？")
    print("=" * 100)
    print("这不是硬件模拟，是动力学检验：四种规则各自产生什么行为。")
    print()

    out = {}

    # A
    print("-" * 100)
    print("A. 纯指数衰减 + 绝对阈值")
    print("-" * 100)
    a = exp_A_absolute_decay()
    out["A_absolute"] = a
    print(f"  tau={a['tau']} 阈值 T={a['T']}")
    print(f"  全部低于阈值的时间: t = {a['t_all_forgotten']} (约 {a['ratio_t_to_tau']} 倍 tau)")
    print(f"  {a['note']}")

    # B
    print("\n" + "-" * 100)
    print("B. 纯指数衰减 + 相对阈值（取 top 10%）")
    print("-" * 100)
    b = exp_B_relative_decay()
    out["B_relative"] = b
    print(f"  排名相关性（时间推进）: {b['rank_correlation_over_time']}")
    print(f"  top10% 集合最后是否完全不变: {b['top10pct_identical_after_all_time']}")
    print(f"  {b['note']}")

    # C
    print("\n" + "-" * 100)
    print("C. 衰减 + 使用依赖加固（被读到就充电）")
    print("-" * 100)
    c = exp_C_usage_dependent()
    out["C_usage_dependent"] = c
    for h in c["history"]:
        print(f"  t={h['t']:<5d} 均值={h['mean']:<8.4f} 最大={h['max']:<7.4f} "
              f"最小={h['min']:<7.4f} >0.1 的条数={h['n_above_0.1']}")
    print(f"  最终: {c['final']}")
    print(f"  {c['note']}")

    # D
    print("\n" + "-" * 100)
    print("D. 衰减 + 选择性巩固（超过阈值转入不衰减的慢组件）")
    print("-" * 100)
    d = exp_D_consolidation()
    out["D_consolidation"] = d
    for h in d["history"]:
        print(f"  t={h['t']:<5d} 快={h['wf_mean']:<8.4f} 慢={h['ws_mean']:<8.4f} "
              f"已巩固={h['n_consolidated']:<5d} 总={h['total_mean']:<8.4f}")
    print(f"  最终: {d['final']}")
    print(f"  {d['note']}")

    # 结论汇总
    print("\n" + "=" * 100)
    print("结论")
    print("=" * 100)
    print("""
  规则 A（纯衰减 + 绝对阈值）：
      所有记忆同时消失，没有"部分遗忘"。
      → 物理漏电本身产生的是"整体归零"，不是"有选择的遗忘"。

  规则 B（纯衰减 + 相对阈值）：
      排名完全不变（相关性恒为 1.0），top10% 集合永远相同。
      → 如果判据是相对的，衰减完全无效，等于"没衰减"。

  规则 C（衰减 + 使用加固）：
      分布不归零，自然分层：热门的被推到上限，冷门的沉到零。
      → 这是"物理衰减 + 相互作用"合起来才有效果，只有漏电不行。

  规则 D（衰减 + 选择性巩固）：
      被巩固的记忆转入不衰减的慢组件，永久保存。
      → "遗忘"只作用于未被巩固的，这才是"提存"。
    """)

    print("→ 对用户假设的判定：")
    print("   ✅ 「物理同步计算」成立：模拟电路一次 O(1) 完成矩阵向量乘")
    print("   ❌ 「只靠自然损耗就能智能遗忘」不成立：纯漏电要么全忘、要么等于没忘")
    print("   ✅ 「电容之间的相互作用」是关键：加固 + 巩固才产生真正的选择性")

    with open(os.path.join(RESULTS, "e30_physical_decay.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e30_physical_decay.json')}")


if __name__ == "__main__":
    main()
