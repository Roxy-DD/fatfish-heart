"""
E-49 修正版：先建"强度"通道，再测哪种重要性信号对

E-48 失败的两个原因（必须记住）：
  ① 检索是纯余弦，历史零参与 → 重要性无处生效，相关系数必然趋 0
  ② 任务太简单（k=10 / 3000 条）→ 命中率 96-99%，天花板，无方差

修正：
  ① 给系统加一条强度通道：
        score = cosine × (1 + β × strength)
        strength 由历史信号累积
  ② 任务难度提高：k=3，线索只剩 15%，记忆库 5000 条 → 有方差

本实验对比四种强度定义：
  NONE     不加强度（基线，等价于现在的系统）
  S1_SHORT 短期高频（任意连续3天内的最大提及次数）
  S2_LONG  长周期重复（活跃周数 × 时间跨度）
  S3_EMO   情绪标记（孤立程度，代理"生理唤醒"）
  S2+SAT   长周期 + 语义饱和惩罚（密集重复反而降低强度）

测三件事：
  A. 加强度后，整体召回是否改善（不能变差——这是红线）
  B. "重要记忆"（长周期反复出现的）召回是否显著提升
  C. 密集重复是否真的损害（语义饱和）
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
K = 3
CUE_FRAC = 0.15


def load_texts(n=5000, seed=0):
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


def build_history(mem_ids, days=300, seed=5):
    """构造 300 天的提及历史，四种模式"""
    rng = np.random.default_rng(seed)
    patterns = {}
    events = defaultdict(list)
    pat_names = ["burst", "spread", "emotional", "baseline"]
    for i, mid in enumerate(mem_ids):
        p = pat_names[i % 4]
        patterns[mid] = p
        total = int(rng.integers(6, 14))
        if p == "burst":
            start = int(rng.integers(0, days - 5))
            for j in range(total):
                events[start + (j % 3)].append(mid)
        elif p == "spread":
            for j in range(total):
                d = int(j * days / total) + int(rng.integers(0, 10))
                events[min(d, days - 1)].append(mid)
        elif p == "emotional":
            d = int(rng.integers(0, days))
            for j in range(3):
                events[min(d + j, days - 1)].append(mid)
        else:
            for j in range(total):
                events[int(rng.integers(0, days))].append(mid)
    return patterns, events


def make_signals(mem_ids, patterns, events, days=300):
    """计算四种强度"""
    sig = {}
    for mid in mem_ids:
        days_on = sorted(d for d in range(days) if mid in events[d])
        if not days_on:
            sig[mid] = {"S1": 0.0, "S2": 0.0, "S3": 0.0, "SAT": 0.0}
            continue

        # S1 短期高频：连续3天窗口内最大提及数
        best = 1
        for d in days_on:
            c = sum(1 for x in days_on if d <= x <= d + 2)
            best = max(best, c)
        s1 = float(best)

        # S2 长周期：活跃周数 × 跨度权重
        weeks = len(set(d // 7 for d in days_on))
        span = (days_on[-1] - days_on[0]) / days
        s2 = weeks * (0.5 + span)

        # S3 情绪：孤立度（间隔均值）+ 罕见加成
        gaps = np.diff(days_on) if len(days_on) > 1 else np.array([days])
        s3 = float(np.mean(gaps)) / 10.0 + (1.5 if len(days_on) <= 4 else 0.0)

        # SAT 语义饱和惩罚：密集重复越多，惩罚越大
        # 参照 Kuhl & Anderson 2011：20-40s 密集重复消除 5-10s 的启动
        density = len(days_on) / max((days_on[-1] - days_on[0] + 1), 1)
        sat = -min(density * 20.0, 3.0) if len(days_on) > 8 else 0.0

        sig[mid] = {"S1": s1, "S2": s2, "S3": s3, "SAT": sat}
    return sig


def main():
    print("=" * 96)
    print("E-49 先建强度通道，再测信号（修正 E-48）")
    print("=" * 96)

    texts = load_texts(5000)
    N = len(texts)
    embed = load_bge()
    print(f"\n记忆库 {N} 条，编码中...")
    t0 = time.time()
    M = embed(texts)
    print(f"  编码 {time.time()-t0:.0f}s")

    rng = np.random.default_rng(9)
    mem_ids = rng.choice(N, 400, replace=False).tolist()
    patterns, events = build_history(mem_ids)
    sig = make_signals(mem_ids, patterns, events)

    # 预生成查询（线索只剩 15%，制造难度）
    cues = {}
    for mid in mem_ids:
        cues[mid] = embed([texts[mid][:max(2, int(len(texts[mid]) * CUE_FRAC))]])[0]

    def evaluate(strength_fn, beta, k=K, subset=None):
        """strength_fn(mid) -> float；返回整体与分组召回"""
        ids = subset if subset is not None else mem_ids
        hits = 0
        by_group = defaultdict(lambda: [0, 0])
        for mid in ids:
            qv = cues[mid]
            base = M @ qv                      # 余弦
            if beta > 0 and strength_fn is not None:
                # 强度只对"库内"调整，用 id 索引的数组更快，但这里 400 条够小
                pass
            top = np.argpartition(-base, k)[:k].tolist()
            ok = mid in top
            hits += int(ok)
            g = patterns[mid]
            by_group[g][0] += int(ok)
            by_group[g][1] += 1
        res = {g: by_group[g][0] / by_group[g][1] for g in by_group}
        return hits / len(ids), res

    def evaluate_weighted(strength_map, beta, k=K):
        """真正的加权版：分数 = cosine × (1 + β × z(strength))"""
        ids = mem_ids
        vals = np.array([strength_map.get(m, 0.0) for m in mem_ids])
        mu, sd = vals.mean(), vals.std()
        sd = sd if sd > 1e-9 else 1.0
        z = (vals - mu) / sd
        zw = {m: float(z[i]) for i, m in enumerate(mem_ids)}

        # 把权重铺到全库（未跟踪的记忆权重 0）
        w = np.ones(N, dtype=np.float32)
        for m in mem_ids:
            w[m] = 1.0 + beta * zw[m]

        hits = 0
        by_group = defaultdict(lambda: [0, 0])
        for mid in ids:
            qv = cues[mid]
            s = (M @ qv) * w
            top = np.argpartition(-s, k)[:k].tolist()
            ok = mid in top
            hits += int(ok)
            g = patterns[mid]
            by_group[g][0] += int(ok)
            by_group[g][1] += 1
        return hits / len(ids), {g: by_group[g][0] / by_group[g][1] for g in by_group}

    # ---------- 基线（无强度）----------
    print("\n" + "-" * 96)
    print(f"基线（无强度），k={K}，线索仅 {CUE_FRAC*100:.0f}%")
    print("-" * 96)
    base_acc, base_grp = evaluate(None, 0.0)
    print(f"  整体召回: {base_acc:.4f}")
    for g in ["burst", "spread", "emotional", "baseline"]:
        print(f"    {g:<12}{base_grp.get(g, 0):.4f}")

    results = {"baseline": {"overall": round(base_acc, 4),
                            "groups": {g: round(v, 4) for g, v in base_grp.items()}}}

    # ---------- 各种强度 ----------
    print("\n" + "-" * 96)
    print("加强度通道：score = cosine × (1 + β × z(strength))")
    print("-" * 96)
    print(f"{'强度定义':<14}{'β':<8}{'整体':<10}{'burst':<10}{'spread':<10}"
          f"{'emotional':<12}{'baseline':<10}")
    print("-" * 96)

    configs = [
        ("S1_SHORT", {m: sig[m]["S1"] for m in mem_ids}),
        ("S2_LONG", {m: sig[m]["S2"] for m in mem_ids}),
        ("S3_EMO", {m: sig[m]["S3"] for m in mem_ids}),
        ("S2+SAT", {m: sig[m]["S2"] + sig[m]["SAT"] for m in mem_ids}),
        ("S1+SAT", {m: sig[m]["S1"] + sig[m]["SAT"] for m in mem_ids}),
    ]

    best = (None, -1)
    for name, smap in configs:
        for beta in [0.2, 0.5, 1.0]:
            acc, grp = evaluate_weighted(smap, beta)
            print(f"{name:<14}{beta:<8.1f}{acc:<10.4f}"
                  f"{grp.get('burst',0):<10.4f}{grp.get('spread',0):<10.4f}"
                  f"{grp.get('emotional',0):<12.4f}{grp.get('baseline',0):<10.4f}")
            results[f"{name}_b{beta}"] = {
                "overall": round(acc, 4),
                "groups": {g: round(v, 4) for g, v in grp.items()},
                "delta_overall": round(acc - base_acc, 4),
                "delta_spread": round(grp.get("spread", 0) - base_grp.get("spread", 0), 4)}
            if acc > best[1]:
                best = (f"{name}_b{beta}", acc)
    print("-" * 96)

    # ---------- 语义饱和专项 ----------
    print("\n" + "-" * 96)
    print("语义饱和检验（密集重复应降低强度，而非提高）")
    print("-" * 96)
    print("  比较：同样被提及 10+ 次，但分布不同的两组")
    dense = [m for m in mem_ids if len([d for d in range(300) if m in events[d]]) >= 8
             and patterns[m] == "burst"]
    sparse = [m for m in mem_ids if patterns[m] == "spread"]
    print(f"  密集组(burst) {len(dense)} 条, 稀疏组(spread) {len(sparse)} 条")
    print(f"  密集组基线召回: {base_grp.get('burst', 0):.4f}")
    print(f"  稀疏组基线召回: {base_grp.get('spread', 0):.4f}")

    # 用 S1（短期高频）加权，看是否反而损害 burst 组
    s1map = {m: sig[m]["S1"] for m in mem_ids}
    acc_s1, grp_s1 = evaluate_weighted(s1map, 1.0)
    print(f"\n  用 S1(短期高频) 加权 β=1.0:")
    print(f"    burst 组: {base_grp.get('burst',0):.4f} → {grp_s1.get('burst',0):.4f} "
          f"（{grp_s1.get('burst',0)-base_grp.get('burst',0):+.4f}）")

    s2map = {m: sig[m]["S2"] for m in mem_ids}
    acc_s2, grp_s2 = evaluate_weighted(s2map, 1.0)
    print(f"  用 S2(长周期) 加权 β=1.0:")
    print(f"    spread 组: {base_grp.get('spread',0):.4f} → {grp_s2.get('spread',0):.4f} "
          f"（{grp_s2.get('spread',0)-base_grp.get('spread',0):+.4f}）")

    # ---------- 结论 ----------
    print("\n" + "=" * 96)
    print("结论")
    print("=" * 96)
    print(f"  基线整体召回: {base_acc:.4f}")
    print(f"  最优配置: {best[0]} → {best[1]:.4f} "
          f"({best[1]-base_acc:+.4f})")
    print(f"\n  判定标准：整体召回不能低于基线（红线），且目标组要提升")
    for k_, v in results.items():
        if isinstance(v, dict) and "delta_spread" in v:
            ok_overall = v["delta_overall"] >= -0.005
            ok_target = v["delta_spread"] > 0
            print(f"    {k_:<16} 整体{v['delta_overall']:+.4f} "
                  f"spread{v['delta_spread']:+.4f} "
                  f"{'✓' if (ok_overall and ok_target) else '✗'}")

    out = {"experiment": "E-49", "k": K, "cue_frac": CUE_FRAC,
           "n_mem": N, "n_tracked": len(mem_ids),
           "results": results,
           "note": "E-48 失败原因：检索纯余弦，历史零参与；本实验先加强度通道"}
    with open(os.path.join(RESULTS, "e49_importance_v2.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e49_importance_v2.json')}")


if __name__ == "__main__":
    main()
