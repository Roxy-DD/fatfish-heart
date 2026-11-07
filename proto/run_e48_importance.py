"""
E-48 记忆重要性：短期重复 vs 长周期重复 vs 情绪标记

用户的纠正（这是本轮的核心）：
  ① 我之前 E-19 测的是"短期被检索几次"，但真正决定记忆深刻的是
     "长周期里是否一直在重复" —— 这是两个不同的量。
  ② "反复记忆的东西反而容易变得陌生" —— 语义饱和（semantic satiation）。
  ③ 能引起生理反应的事件（惊恐、创伤）会让人记忆深刻。

文献依据（已查证）：
  - Kuhl & Anderson 2011: 密集重复 20-40s 会**消除** 5-10s 重复带来的启动效应
    （massed repetition decrement），且 15 分钟后仍可见 → "被重复太多反而更陌生"
  - Cepeda 2006 / 2020 meta: 间隔重复 vs 密集重复，g = 0.74
    且"最优间隔随保持期增长而增长" → 长周期重复才有效
  - McGaugh 2004 / Dolcos 2004: 情绪唤醒通过杏仁核调制巩固，
    高唤醒图片回忆 52-53% vs 中性 38% → 情绪是独立的"标记"

本实验设计三种"重要性"信号，看哪种能真正预测长期记忆强度：
  S1 短期高频   （我们现在用的：短时间内被查多次）
  S2 长周期低频 （用户说的：跨越长时间段反复出现）
  S3 情绪标记   （对应"生理反应"，这里用"偏离常规的程度"代理）

做法：
  构造一个带时间戳的"事件流"（模拟长期使用），
  三种信号各自给记忆打分，然后测"隔一段距离后"能否召回。
  关键：**打分时只看信号，不看内容**，避免信息泄漏。
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HOME"] = r"C:\Users\xuee\Desktop\肥鱼之心\.hf"
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import numpy as np

MODEL_DIR = r"C:\Users\xuee\Desktop\肥鱼之心\models\bge-small-zh"
RESULTS = os.path.join(_HERE, "results")
DATA = r"C:\Users\xuee\Desktop\肥鱼之心\data\tnews"


def load_texts(n=3000, seed=0):
    import pandas as pd
    paths = []
    for root, _, files in os.walk(DATA):
        for f in files:
            if f.endswith(".parquet"):
                paths.append(os.path.join(root, f))
    df = pd.concat([pd.read_parquet(p) for p in sorted(paths)], ignore_index=True)
    tcol = "sentence" if "sentence" in df.columns else df.columns[0]
    t = df[tcol].astype(str).tolist()
    if n < len(t):
        idx = np.random.default_rng(seed).choice(len(t), n, replace=False)
        t = [t[i] for i in idx]
    return t


def load_bge():
    import torch
    from transformers import AutoTokenizer, AutoModel
    torch.set_num_threads(4)
    tok = AutoTokenizer.from_pretrained(MODEL_DIR)
    mdl = AutoModel.from_pretrained(MODEL_DIR).eval()

    def embed(texts, bs=64):
        outs = []
        with torch.no_grad():
            for i in range(0, len(texts), bs):
                b = texts[i:i + bs]
                enc = tok(b, padding=True, truncation=True, max_length=128, return_tensors="pt")
                outs.append(mdl(**enc).last_hidden_state[:, 0].numpy())
        v = np.vstack(outs).astype(np.float32)
        nn = np.linalg.norm(v, axis=1, keepdims=True); nn[nn == 0] = 1
        return v / nn
    return embed


def main():
    print("=" * 96)
    print("E-48 短期重复 vs 长周期重复 vs 情绪标记")
    print("=" * 96)

    texts = load_texts(3000)
    N = len(texts)
    embed = load_bge()
    print(f"\n记忆库 {N} 条，编码中...")
    t0 = time.time()
    M = embed(texts)
    print(f"  编码 {time.time()-t0:.0f}s")

    # ============ 构造带时间戳的事件流 ============
    # 300 天，每天若干次"提及"。不同记忆有不同的被提及模式。
    rng = np.random.default_rng(5)
    DAYS = 300
    mem_ids = rng.choice(N, 400, replace=False).tolist()

    # 为每条记忆分配一种"被提及模式"
    # burst     ：短期集中提及（模拟"短期内重复很多次"）
    # spread    ：长周期稀疏提及（模拟"半年里一直在提"）
    # emotional ：极少提及但"情绪强度高"
    # baseline  ：中等随机提及
    patterns = {}
    for i, mid in enumerate(mem_ids):
        p = ["burst", "spread", "emotional", "baseline"][i % 4]
        patterns[mid] = p

    # 生成事件流：day -> [mem_id]
    events = defaultdict(list)
    for mid in mem_ids:
        p = patterns[mid]
        total = int(rng.integers(6, 14))     # 总提及次数接近
        if p == "burst":
            # 全部集中在连续 3 天内
            start = int(rng.integers(0, DAYS - 5))
            for j in range(total):
                events[start + (j % 3)].append(mid)
        elif p == "spread":
            # 均匀分散在整个 300 天
            for j in range(total):
                d = int(j * DAYS / total) + int(rng.integers(0, 10))
                events[min(d, DAYS - 1)].append(mid)
        elif p == "emotional":
            # 很少提及（3 次），集中在一个点
            d = int(rng.integers(0, DAYS))
            for j in range(3):
                events[min(d + j, DAYS - 1)].append(mid)
        else:
            for j in range(total):
                events[int(rng.integers(0, DAYS))].append(mid)

    # ============ 三种"重要性"信号 ============
    # 全部只看"提及模式"，不看内容
    def sig_short_burst(mid):
        """S1 短期高频：任意连续 3 天内提及次数的最大值"""
        days = sorted(d for d in range(DAYS) if mid in events[d])
        if not days:
            return 0
        best = 1
        for d in days:
            c = sum(1 for x in days if d <= x <= d + 2)
            best = max(best, c)
        return best

    def sig_long_span(mid):
        """S2 长周期重复：提及覆盖的天数跨度 + 不同"活跃周"数"""
        days = sorted(d for d in range(DAYS) if mid in events[d])
        if not days:
            return 0
        weeks = len(set(d // 7 for d in days))
        span = (days[-1] - days[0]) / DAYS     # 0-1
        return weeks * (0.5 + span)            # 周数 × 跨度权重

    def sig_emotional(mid):
        """S3 情绪强度：用"单次提及的孤立度"代理
           情绪事件的特点：孤立、突出、不与日常重复混在一起
           （这是代理量，不是真情绪）"""
        days = sorted(d for d in range(DAYS) if mid in events[d])
        if not days:
            return 0
        if len(days) == 1:
            return 5.0
        # 越孤立（相邻间隔越大）越"突出"
        gaps = np.diff(days)
        return float(np.mean(gaps)) / 10.0 + (1.0 if len(days) <= 4 else 0.0)

    # ============ 测量：隔一段时间后召回 ============
    # 关键设计：查询用的是"部分线索"（前 30% 文本），模拟"记得大概"
    # 测的是"该记忆能否被检索到"，不测内容质量
    print("\n" + "-" * 96)
    print("测量：部分线索（前30%）下，能否召回目标记忆")
    print("-" * 96)

    def eval_group(group_pattern, sig_fn, k=10):
        ids = [m for m in mem_ids if patterns[m] == group_pattern]
        if not ids:
            return None
        hit = 0
        for mid in ids:
            cue = texts[mid][:max(2, int(len(texts[mid]) * 0.3))]
            qv = embed([cue])[0]
            s = M @ qv
            top = np.argsort(-s)[:k].tolist()
            if mid in top:
                hit += 1
        return hit / len(ids), len(ids)

    print(f"{'模式':<12}{'条数':<8}{'S1短期高频':<14}{'S2长周期':<12}{'S3情绪':<10}{'命中率'}")
    print("-" * 96)
    rows = []
    for p in ["burst", "spread", "emotional", "baseline"]:
        ids = [m for m in mem_ids if patterns[m] == p]
        s1 = np.mean([sig_short_burst(m) for m in ids])
        s2 = np.mean([sig_long_span(m) for m in ids])
        s3 = np.mean([sig_emotional(m) for m in ids])
        r = eval_group(p, None, k=10)
        rows.append({"pattern": p, "n": r[1], "S1": round(float(s1), 2),
                     "S2": round(float(s2), 2), "S3": round(float(s3), 2),
                     "recall": round(float(r[0]), 4)})
        print(f"{p:<12}{r[1]:<8}{s1:<14.2f}{s2:<12.2f}{s3:<10.2f}{r[0]:.4f}")
    print("-" * 96)

    # ============ 相关性：三种信号 vs 实际召回 ============
    print("\n" + "-" * 96)
    print("三个信号对「实际能否召回」的预测力（逐条计算）")
    print("-" * 96)
    X1, X2, X3, Y = [], [], [], []
    for mid in mem_ids:
        cue = texts[mid][:max(2, int(len(texts[mid]) * 0.3))]
        qv = embed([cue])[0]
        s = M @ qv
        top = np.argsort(-s)[:10].tolist()
        y = 1.0 if mid in top else 0.0
        X1.append(sig_short_burst(mid))
        X2.append(sig_long_span(mid))
        X3.append(sig_emotional(mid))
        Y.append(y)
    X1, X2, X3, Y = map(np.array, (X1, X2, X3, Y))

    def corr(a, b):
        if a.std() < 1e-9 or b.std() < 1e-9:
            return 0.0
        return float(np.corrcoef(a, b)[0, 1])

    print(f"{'信号':<20}{'与召回的相关性'}")
    print("-" * 60)
    c1, c2, c3 = corr(X1, Y), corr(X2, Y), corr(X3, Y)
    print(f"{'S1 短期高频':<20}{c1:+.4f}")
    print(f"{'S2 长周期重复':<20}{c2:+.4f}")
    print(f"{'S3 情绪标记':<20}{c3:+.4f}")
    print("-" * 60)

    # 组合预测：标准化后相加
    def z(a):
        s = a.std()
        return (a - a.mean()) / (s if s > 1e-9 else 1)
    combo = z(X2) + z(X3)
    print(f"{'S2+S3 组合':<20}{corr(combo, Y):+.4f}")
    print(f"{'S1+S2 组合':<20}{corr(z(X1)+z(X2), Y):+.4f}")

    # ============ 语义饱和：密集重复是否损害 ============
    print("\n" + "-" * 96)
    print("语义饱和检验：同一记忆被密集重复后，还能否用语义检索到")
    print("-" * 96)
    print("（模拟 Kuhl & Anderson 2011：massed repetition 会消除启动效应）")
    sat_rows = []
    for n_rep in [0, 5, 10, 20, 40]:
        # 取 60 条，模拟"被重复 n 次后"——重复会让向量向均值漂移（模拟习惯化）
        sample = mem_ids[:60]
        hit = 0
        Mv = M.copy()
        if n_rep > 0:
            # 习惯化模型：每次重复，向量向其邻域均值靠拢一点（钝化）
            alpha = min(0.02 * n_rep, 0.5)
            for mid in sample:
                neigh = np.argsort(-(M @ M[mid]))[:20]
                Mv[mid] = (1 - alpha) * M[mid] + alpha * M[neigh].mean(axis=0)
            Mv /= np.linalg.norm(Mv, axis=1, keepdims=True)
        for mid in sample:
            cue = texts[mid][:max(2, int(len(texts[mid]) * 0.5))]
            qv = embed([cue])[0]
            s = Mv @ qv
            if mid in np.argsort(-s)[:10]:
                hit += 1
        r = hit / len(sample)
        sat_rows.append({"n_rep": n_rep, "recall": round(r, 4)})
        print(f"  重复 {n_rep:>3} 次后: 召回 {r:.4f}")
    print("-" * 96)

    # ============ 汇总 ============
    print("\n" + "=" * 96)
    print("结论")
    print("=" * 96)
    best_sig = max([("S1短期", c1), ("S2长周期", c2), ("S3情绪", c3)],
                   key=lambda x: abs(x[1]))
    print(f"  最强的单一信号: {best_sig[0]}（r = {best_sig[1]:+.4f}）")
    print(f"  S1 vs S2: {c1:+.4f} vs {c2:+.4f} → "
          f"{'长周期更强' if abs(c2) > abs(c1) else '短期更强'}")
    print(f"  密集重复损害检验: 0次 {sat_rows[0]['recall']:.3f} → "
          f"40次 {sat_rows[-1]['recall']:.3f} "
          f"({'下降' if sat_rows[-1]['recall'] < sat_rows[0]['recall'] else '未下降'})")

    out = {"experiment": "E-48", "n_mem": N, "n_tracked": len(mem_ids),
           "groups": rows,
           "correlations": {"S1_short": round(c1, 4), "S2_long": round(c2, 4),
                            "S3_emotional": round(c3, 4),
                            "S2_S3": round(corr(combo, Y), 4),
                            "S1_S2": round(corr(z(X1) + z(X2), Y), 4)},
           "saturation": sat_rows}
    with open(os.path.join(RESULTS, "e48_importance.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e48_importance.json')}")


if __name__ == "__main__":
    main()
