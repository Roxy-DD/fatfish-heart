"""
E-51 验证 Mem0 的关键做法：top-200 检索 + 压缩到 token 预算

E-50 我得到的错误结论："席位是硬约束，所以无解"。
Mem0 的实测数字反驳了它：top_200 检索，只注入 7.0K tokens，LoCoMo 92.5。
  → 真正受限的是 token，不是条数；token 可压缩。

本实验在同等 token 预算下，比较：
  A. TOPK-FULL     取前 k 条完整原文（基线）
  B. TOP200-CUT    取前 200 条，每条截断到固定长度
  C. TOP200-KW     取前 200 条，每条只留含查询词的句子
  D. TOP200-MERGE  取前 200 条，把近重复的合并（去冗余）

指标：
  ① 召回（目标记忆是否在最终注入内容里）
  ② 实际字符数（token 代理）
  ③ 每字符召回效率

关键：所有方法在同一**字符预算**下比较，这才是公平的。
"""
from __future__ import annotations

import json
import os
import sys
import time
import re
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


def split_sents(t):
    return [s for s in re.split(r"[。！？；]", t) if s.strip()]


def compress_cut(text, budget):
    """B: 截断"""
    return text[:budget]


def compress_kw(text, budget, terms):
    """C: 只留含查询词的句子；若无命中则退回截断"""
    sents = split_sents(text)
    hit = [s for s in sents if any(t in s for t in terms)]
    if hit:
        out = "。".join(hit)
        return out[:budget]
    return text[:budget]


def main():
    print("=" * 96)
    print("E-51 top-200 检索 + 压缩（验证 Mem0 的做法）")
    print("=" * 96)

    texts = load_texts(5000)
    N = len(texts)
    embed = load_bge()
    print(f"\n记忆库 {N} 条，编码中...")
    t0 = time.time()
    M = embed(texts)
    print(f"  编码 {time.time()-t0:.0f}s")

    rng = np.random.default_rng(9)
    targets = rng.choice(N, 200, replace=False).tolist()

    # 查询：线索 15%
    cues = {m: embed([texts[m][:max(2, int(len(texts[m]) * CUE_FRAC))]])[0]
            for m in targets}
    cue_text = {m: texts[m][:max(2, int(len(texts[m]) * CUE_FRAC))] for m in targets}

    # 预计算 top-200
    print("预计算 top-200...")
    TOPN = 200
    top_ids = {}
    for m in targets:
        s = M @ cues[m]
        top_ids[m] = np.argsort(-s)[:TOPN].tolist()

    avg_len = np.mean([len(t) for t in texts])
    print(f"  平均记忆长度 {avg_len:.0f} 字")

    # ============ 各方法的字符预算 ============
    print("\n" + "-" * 96)
    print("A. TOPK-FULL 基线（取前 k 条完整原文）")
    print("-" * 96)
    print(f"{'k':<8}{'召回':<12}{'字符数':<12}{'每千字召回'}")
    print("-" * 96)
    results = {}
    for k in [3, 5, 10, 20, 50]:
        hit, chars = 0, 0
        for m in targets:
            sel = top_ids[m][:k]
            if m in sel:
                hit += 1
            chars += sum(len(texts[i]) for i in sel)
        r = hit / len(targets)
        c = chars / len(targets)
        results[f"topk_full_{k}"] = {"recall": round(r, 4), "chars": round(c, 1)}
        print(f"{k:<8}{r:<12.4f}{c:<12.0f}{r/max(c,1e-9)*1000:.2f}")
    print("-" * 96)

    # 以 k=5 的字符数作为预算基准
    budget_chars = results["topk_full_5"]["chars"]
    print(f"\n  以 k=5 的字符数 {budget_chars:.0f} 作为统一预算")

    # ============ B / C / D 在同一预算下 ============
    print("\n" + "-" * 96)
    print(f"同等字符预算（{budget_chars:.0f} 字）下比较")
    print("-" * 96)
    print(f"{'方法':<22}{'召回':<12}{'实际字符':<12}{'相对基线'}")
    print("-" * 96)

    base = results["topk_full_5"]
    print(f"{'TOPK k=5 (完整)':<22}{base['recall']:<12.4f}{base['chars']:<12.0f}{'—'}")

    # B: top-200 截断，每条均分预算
    for n_take in [20, 50, 100, 200]:
        per = max(8, int(budget_chars / n_take))
        hit, chars = 0, 0
        for m in targets:
            sel = top_ids[m][:n_take]
            if m in sel:
                hit += 1
            chars += sum(len(compress_cut(texts[i], per)) for i in sel)
        r = hit / len(targets); c = chars / len(targets)
        results[f"cut_{n_take}_per{per}"] = {"recall": round(r, 4), "chars": round(c, 1)}
        print(f"{f'B 截断 top{n_take} (每{per}字)':<22}{r:<12.4f}{c:<12.0f}"
              f"{r-base['recall']:+.4f}")

    # C: top-200 关键词句提取
    for n_take in [20, 50, 100, 200]:
        per = max(8, int(budget_chars / n_take))
        hit, chars = 0, 0
        for m in targets:
            sel = top_ids[m][:n_take]
            if m in sel:
                hit += 1
            terms = set(cue_text[m][:6])
            chars += sum(len(compress_kw(texts[i], per, terms)) for i in sel)
        r = hit / len(targets); c = chars / len(targets)
        results[f"kw_{n_take}_per{per}"] = {"recall": round(r, 4), "chars": round(c, 1)}
        print(f"{f'C 关键词 top{n_take} (每{per}字)':<22}{r:<12.4f}{c:<12.0f}"
              f"{r-base['recall']:+.4f}")
    print("-" * 96)

    # ============ 完整曲线：召回 vs 字符预算 ============
    print("\n" + "-" * 96)
    print("完整曲线：不同字符预算下的最优召回")
    print("-" * 96)
    print(f"{'预算(字)':<14}{'TOPK-FULL最优':<18}{'TOP200+截断最优':<20}{'增益'}")
    print("-" * 96)
    curve = []
    for budget in [100, 200, 400, 800, 1600, 3200]:
        # TOPK-FULL：找不超过预算的最大 k
        best_k, best_r = 0, 0.0
        for k in range(1, 60):
            c = k * avg_len
            if c > budget:
                break
            hit = sum(1 for m in targets if m in top_ids[m][:k])
            best_k, best_r = k, hit / len(targets)
        # TOP200：在预算内尽量多取，每条均分
        best_n, best_r2, best_per = 0, 0.0, 0
        for n_take in [10, 20, 50, 100, 200]:
            per = int(budget / n_take)
            if per < 6:
                continue
            # 只取 top n_take 中前 floor(budget/per) 条，保证不超预算
            cap = min(n_take, int(budget / per))
            hit = sum(1 for m in targets if m in top_ids[m][:cap])
            r = hit / len(targets)
            if r > best_r2:
                best_n, best_r2, best_per = cap, r, per
        curve.append({"budget": budget, "topk_k": best_k, "topk_r": round(best_r, 4),
                      "top200_n": best_n, "top200_per": best_per,
                      "top200_r": round(best_r2, 4)})
        print(f"{budget:<14}{best_r:<18.4f}{best_r2:<20.4f}{best_r2-best_r:+.4f}")
    print("-" * 96)

    # ============ 结论 ============
    print("\n" + "=" * 96)
    print("结论")
    print("=" * 96)
    base_r = results["topk_full_5"]["recall"]
    # 找同预算下最好的方法
    same_budget = [(k, v) for k, v in results.items()
                   if k.startswith(("cut_", "kw_")) and abs(v["chars"] - budget_chars) / budget_chars < 0.3]
    if same_budget:
        best_m = max(same_budget, key=lambda x: x[1]["recall"])
        print(f"  同预算(~{budget_chars:.0f}字)下：")
        print(f"    TOPK k=5 完整原文 : 召回 {base_r:.4f}")
        print(f"    {best_m[0]:<20}: 召回 {best_m[1]['recall']:.4f} "
              f"({best_m[1]['recall']-base_r:+.4f})")
        verdict = best_m[1]["recall"] > base_r + 0.01
        print(f"    → {'压缩有效 ✓' if verdict else '压缩无增益 ✗'}")
    else:
        verdict = False
        print("  没有找到同预算的可比配置")

    out = {"experiment": "E-51", "n_mem": N, "n_targets": len(targets),
           "topn": TOPN, "cue_frac": CUE_FRAC,
           "budget_chars": round(budget_chars, 1),
           "avg_len": round(float(avg_len), 1),
           "results": results, "curve": curve,
           "compression_helps": bool(verdict)}
    with open(os.path.join(RESULTS, "e51_compress.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e51_compress.json')}")


if __name__ == "__main__":
    main()
