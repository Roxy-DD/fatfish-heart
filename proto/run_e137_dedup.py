"""
E-137 重复记忆处理：完全重复直接合并，有差异的让模型融合

用户设计（2026-11-02）：
  "完全重复的当然要直接合并。有差异的情况下，就要让模型自己判断，
   让它去分析里面到底有哪些差异，然后把它合并保留成一条更完整的。"

两段式：
  ① 系统判定"完全重复"：文本归一化后高度一致（机械可判）
     → 直接合并，不调模型
  ② 系统判定"有差异"：相似但不完全相同
     → 交给模型：分析差异 → 融合成一条更完整的

红线约束：不物理删除，只标 superseded_by 指向融合后的主记录。
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e137")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DB = os.path.join(_HERE, "cosmos_mem", "dsh_mem.db")


def normalize(text):
    """归一化：去空白、去标点、去常见虚词，用于判"完全重复" """
    t = text or ""
    t = re.sub(r"[\s，。、；：！？""''（）()\[\]【】《》\-—~·.,;:!?\"']", "", t)
    # 去掉语气/连接虚词（不影响语义）
    for w in ["那么", "然后", "并且", "而且", "以及", "还有", "另外",
              "的", "了", "就", "都", "也", "再", "会", "要", "把", "被"]:
        t = t.replace(w, "")
    return t


def is_exact_dup(a, b):
    """完全重复：归一化后相同，或一方是另一方的子串（信息无损）"""
    na, nb = normalize(a), normalize(b)
    if na == nb:
        return True, "归一化后完全相同"
    if len(na) >= 12 and len(nb) >= 12:
        if na in nb:
            return True, f"前者是后者的子集（后者更完整）"
        if nb in na:
            return True, f"后者是前者的子集（前者更完整）"
    return False, ""


def main():
    from store_v2 import MemoryStore
    from store_v1 import Embedder
    import numpy as np
    import conflict

    st = MemoryStore(DB)
    emb = Embedder.get()

    rows = st.con.execute(
        "SELECT ep_id, raw_text FROM episodes WHERE invalid_at IS NULL").fetchall()
    n = len(rows)

    print("=" * 90)
    print("E-137 重复记忆两段式处理")
    print("=" * 90)
    print(f"\n有效记忆 {n} 条")

    st._load_vectors(include_invalid=False)
    norms = np.linalg.norm(st._vec, axis=1)
    norms[norms == 0] = 1.0
    V = st._vec / norms[:, None]
    S = V @ V.T

    # 聚类（0.93 门槛，兼顾召回与精确）
    TH = 0.93
    assigned = [-1] * n
    clusters = []
    for i in range(n):
        if assigned[i] >= 0:
            continue
        cid = len(clusters)
        members = [i]
        assigned[i] = cid
        for j in range(i + 1, n):
            if assigned[j] >= 0:
                continue
            if S[i, j] >= TH:
                assigned[j] = cid
                members.append(j)
        clusters.append(members)

    multi = [c for c in clusters if len(c) > 1]
    print(f"\n{TH} 门槛下：{len(clusters)} 簇，{len(multi)} 个多成员簇")

    # ---------- ① 系统判定完全重复 ----------
    exact_groups = []     # 可直接合并的簇
    fuzzy_groups = []     # 需要模型融合的簇

    for c in multi:
        texts = [rows[i][1] for i in c]
        # 簇内两两比较，看是否全部"完全重复"
        all_exact = True
        reason = ""
        for a in range(len(texts)):
            for b in range(a + 1, len(texts)):
                ok, why = is_exact_dup(texts[a], texts[b])
                if not ok:
                    all_exact = False
                    reason = f"「{texts[a][:18]}」vs「{texts[b][:18]}」不构成完全重复"
                    break
            if not all_exact:
                break

        item = {
            "ep_ids": [rows[i][0] for i in c],
            "texts": texts,
            "size": len(c),
        }
        if all_exact:
            # 选最长的那条作为主记录（信息最全）
            longest = max(range(len(texts)), key=lambda k: len(texts[k]))
            item["keep"] = rows[c[longest]][0]
            item["merge"] = [rows[c[k]][0] for k in range(len(texts))
                             if k != longest]
            item["reason"] = "完全重复，系统直接合并"
            exact_groups.append(item)
        else:
            item["reason"] = reason
            fuzzy_groups.append(item)

    print(f"\n" + "-" * 90)
    print(f"① 系统判定「完全重复」: {len(exact_groups)} 组 —— 直接合并，不调模型")
    print("-" * 90)
    for g in exact_groups:
        print(f"\n  【{g['size']} 条】{g['reason']}")
        print(f"    保留: {g['texts'][0][:60] if g['size'] else ''}...")
        for k, t in enumerate(g["texts"]):
            tid = g["ep_ids"][k]
            mark = " ← 保留" if tid == g["keep"] else " → 合并"
            print(f"      [{tid[:8]}]{mark} {t[:56]}")

    print(f"\n" + "-" * 90)
    print(f"② 模型判定「有差异」: {len(fuzzy_groups)} 组 —— 需融合")
    print("-" * 90)
    for g in fuzzy_groups:
        print(f"\n  【{g['size']} 条】{g['reason'][:60]}")
        for k, t in enumerate(g["texts"]):
            print(f"      [{g['ep_ids'][k][:8]}] {t[:66]}")

    # ---------- 写任务包 ----------
    packs = []
    for i, g in enumerate(fuzzy_groups):
        packs.append({
            "cid": f"F{i:02d}",
            "ep_ids": g["ep_ids"],
            "texts": g["texts"],
        })
    with open(os.path.join(TASK, "fuzzy.json"), "w", encoding="utf-8") as f:
        json.dump(packs, f, ensure_ascii=False, indent=1)

    # ---------- 汇总 ----------
    print("\n" + "=" * 90)
    print("汇总")
    print("=" * 90)
    n_exact_dup = sum(g["size"] - 1 for g in exact_groups)
    n_fuzzy_items = sum(g["size"] for g in fuzzy_groups)
    print(f"  完全重复组: {len(exact_groups)} 组，可省 {n_exact_dup} 条")
    print(f"  有差异组:   {len(fuzzy_groups)} 组，涉及 {n_fuzzy_items} 条，交模型融合")
    print(f"  总可处理:   {n_exact_dup + n_fuzzy_items - len(fuzzy_groups)} 条冗余")

    out = {"experiment": "E-137", "n": n, "threshold": TH,
           "n_clusters": len(clusters),
           "exact_groups": exact_groups,
           "fuzzy_groups": fuzzy_groups,
           "savable": n_exact_dup + n_fuzzy_items - len(fuzzy_groups)}
    with open(os.path.join(RESULTS, "e137_dedup.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    st.con.close()
    print(f"\n  任务包 → {TASK}/fuzzy.json（{len(packs)} 组）")
    print(f"  结果 → {os.path.join(RESULTS, 'e137_dedup.json')}")


if __name__ == "__main__":
    main()
