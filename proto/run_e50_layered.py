"""
E-50 分层激活 vs top-k 排序：能否打破零和

E-49 的发现：top-k 是固定预算，加强度权重必然零和
（S1 加权把 burst 组 0.60→0.90，代价是 spread 组 0.54→0.00）。

人脑假设：不是"排序取前 k"，而是"阈值激活 + 分层"
  - 核心层：高强度的记忆**总是**在场（不参与竞争，占固定席位）
  - 检索层：其余席位由"余弦超过阈值"填充，条数不定

本实验对比三种机制，在**相同上下文预算**下比较：
  A. TOPK      排序取前 k（现状基线）
  B. THRESH    纯阈值激活（cosine > T 全返回）
  C. LAYERED   核心层(按强度固定) + 阈值填充

关键指标：
  ① 目标召回（该找到的找到了吗）
  ② 上下文条数（真实代价）
  ③ 每席位效率（召回 / 条数）
  ④ 分组召回（burst / spread / emotional / baseline 是否都不被牺牲）
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HOME"] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".hf")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import numpy as np

MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "bge-small-zh")
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "tnews")
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
    rng = np.random.default_rng(seed)
    patterns, events = {}, defaultdict(list)
    names = ["burst", "spread", "emotional", "baseline"]
    for i, mid in enumerate(mem_ids):
        p = names[i % 4]
        patterns[mid] = p
        total = int(rng.integers(6, 14))
        if p == "burst":
            st = int(rng.integers(0, days - 5))
            for j in range(total):
                events[st + (j % 3)].append(mid)
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


def make_signals(mem_ids, events, days=300):
    sig = {}
    for mid in mem_ids:
        days_on = sorted(d for d in range(days) if mid in events[d])
        if not days_on:
            sig[mid] = 0.0
            continue
        # 长周期强度：活跃周数 × 跨度
        weeks = len(set(d // 7 for d in days_on))
        span = (days_on[-1] - days_on[0]) / days
        sig[mid] = weeks * (0.5 + span)
    return sig


def main():
    print("=" * 96)
    print("E-50 分层激活 vs top-k：能否打破零和")
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
    strength = make_signals(mem_ids, events)

    # 查询：线索 15%
    cues = {m: embed([texts[m][:max(2, int(len(texts[m]) * CUE_FRAC))]])[0]
            for m in mem_ids}

    # 预计算余弦
    print("预计算余弦...")
    S = {m: (M @ cues[m]) for m in mem_ids}

    def run(groups=None):
        return groups if groups is not None else mem_ids

    def eval_mech(select_fn):
        """select_fn(sims) -> list of indices; 返回召回、平均条数、分组召回"""
        hits, total_ctx = 0, 0
        by_g = defaultdict(lambda: [0, 0])
        for mid in mem_ids:
            s = S[mid]
            sel = select_fn(s)
            total_ctx += len(sel)
            ok = mid in sel
            hits += int(ok)
            g = patterns[mid]
            by_g[g][0] += int(ok)
            by_g[g][1] += 1
        n = len(mem_ids)
        return {"recall": hits / n, "ctx": total_ctx / n,
                "groups": {g: by_g[g][0] / by_g[g][1] for g in by_g}}

    results = {}

    # ---------- A. TOPK 基线 ----------
    print("\n" + "-" * 96)
    print("A. TOPK 排序取前 k（现状）")
    print("-" * 96)
    print(f"{'k':<8}{'召回':<12}{'上下文条数':<14}{'每席位效率'}")
    print("-" * 96)
    for k in [3, 5, 10, 20]:
        r = eval_mech(lambda s, k=k: np.argsort(-s)[:k].tolist())
        results[f"topk_{k}"] = r
        print(f"{k:<8}{r['recall']:<12.4f}{r['ctx']:<14.1f}"
              f"{r['recall']/r['ctx']:<10.3f}")
    print("-" * 96)

    # ---------- B. 纯阈值激活 ----------
    print("\n" + "-" * 96)
    print("B. THRESH 纯阈值激活（cosine > T 全返回）")
    print("-" * 96)
    print(f"{'T':<8}{'召回':<12}{'上下文条数':<14}{'每席位效率'}")
    print("-" * 96)
    for T in [0.30, 0.40, 0.50, 0.60, 0.70]:
        def fn(s, T=T):
            return np.where(s >= T)[0].tolist()
        r = eval_mech(fn)
        results[f"thresh_{T}"] = r
        print(f"{T:<8.2f}{r['recall']:<12.4f}{r['ctx']:<14.1f}"
              f"{r['recall']/max(r['ctx'],1e-9):<10.3f}")
    print("-" * 96)

    # ---------- C. 分层激活 ----------
    print("\n" + "-" * 96)
    print("C. LAYERED 核心层(按长周期强度固定) + 阈值填充")
    print("-" * 96)
    # 核心层：长周期强度最高的 C 条
    order = sorted(mem_ids, key=lambda m: -strength[m])

    print(f"{'核心数C':<10}{'阈值T':<10}{'召回':<10}{'上下文':<10}"
          f"{'效率':<10}{'burst':<9}{'spread':<9}{'emo':<9}")
    print("-" * 96)
    best = ("topk_3", -1)
    for C in [1, 2, 3]:
        core = set(order[:C])
        for T in [0.45, 0.55, 0.65]:
            def fn(s, core=core, T=T):
                sel = set(np.where(s >= T)[0].tolist())
                sel |= core                      # 核心层总是加入
                return sorted(sel)
            r = eval_mech(fn)
            gr = r["groups"]
            results[f"layer_C{C}_T{T}"] = r
            eff = r["recall"] / max(r["ctx"], 1e-9)
            print(f"{C:<10}{T:<10.2f}{r['recall']:<10.4f}{r['ctx']:<10.1f}"
                  f"{eff:<10.3f}{gr.get('burst',0):<9.4f}"
                  f"{gr.get('spread',0):<9.4f}{gr.get('emotional',0):<9.4f}")
            if r["recall"] > best[1]:
                best = (f"layer_C{C}_T{T}", r["recall"])
    print("-" * 96)

    # ---------- 对照：等预算比较 ----------
    print("\n" + "-" * 96)
    print("D. 等上下文预算下比较（关键）")
    print("-" * 96)
    base = results["topk_3"]
    target_ctx = base["ctx"]
    print(f"  以 topk_3 的上下文条数 {target_ctx:.1f} 为预算：")
    print(f"{'机制':<20}{'召回':<12}{'上下文':<12}{'相对基线'}")
    print("-" * 96)
    print(f"{'TOPK k=3':<20}{base['recall']:<12.4f}{base['ctx']:<12.1f}{'—'}")
    # 找最接近该预算的分层配置
    cands = [(k, v) for k, v in results.items() if k.startswith("layer_")]
    cands.sort(key=lambda kv: abs(kv[1]["ctx"] - target_ctx))
    for k, v in cands[:4]:
        print(f"{k:<20}{v['recall']:<12.4f}{v['ctx']:<12.1f}"
              f"{v['recall']-base['recall']:+.4f}")
    print("-" * 96)

    # ---------- 结论 ----------
    print("\n" + "=" * 96)
    print("结论")
    print("=" * 96)
    tk3 = results["topk_3"]
    print(f"  TOPK k=3 基线: 召回 {tk3['recall']:.4f}, 上下文 {tk3['ctx']:.1f} 条")
    print(f"  分组: " + "  ".join(f"{g}={v:.3f}" for g, v in tk3["groups"].items()))

    best_key, best_rec = best
    bv = results[best_key]
    print(f"\n  最优分层 {best_key}: 召回 {bv['recall']:.4f}, 上下文 {bv['ctx']:.1f} 条")
    print(f"  分组: " + "  ".join(f"{g}={v:.3f}" for g, v in bv["groups"].items()))
    print(f"\n  相对基线: 召回 {bv['recall']-tk3['recall']:+.4f}, "
          f"上下文 {bv['ctx']-tk3['ctx']:+.1f} 条")

    # 零和检验：burst 与 spread 是否都被保住
    print("\n  零和检验（burst 和 spread 是否同时不降）：")
    b_base = tk3["groups"].get("burst", 0)
    s_base = tk3["groups"].get("spread", 0)
    b_new = bv["groups"].get("burst", 0)
    s_new = bv["groups"].get("spread", 0)
    print(f"    burst : {b_base:.3f} → {b_new:.3f} ({b_new-b_base:+.3f})")
    print(f"    spread: {s_base:.3f} → {s_new:.3f} ({s_new-s_base:+.3f})")
    broke = (b_new >= b_base - 0.02) and (s_new >= s_base - 0.02)
    print(f"    → {'打破零和 ✓' if broke else '仍是零和 ✗'}")

    out = {"experiment": "E-50", "cue_frac": CUE_FRAC, "n_mem": N,
           "n_tracked": len(mem_ids),
           "results": {k: {"recall": round(v["recall"], 4),
                           "ctx": round(v["ctx"], 2),
                           "groups": {g: round(x, 4) for g, x in v["groups"].items()}}
                       for k, v in results.items()},
           "zero_sum_broken": bool(broke),
           "best_layered": best_key}
    with open(os.path.join(RESULTS, "e50_layered.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e50_layered.json')}")


if __name__ == "__main__":
    main()


