"""
E-129 候选包召回策略改进：余弦 + 槽位多样性

E-128 暴露的缺陷：
  纯余弦 top-K 在库规模变大时会被"字面更像的干扰项"占满，
  真正的旧值（往往与新值只在"值"上不同、其余字面相同）被挤出去。

  「换车」：目标「我的车是白色卡罗拉」排 12+，前面全是「X的车是黑色大众」
  「会议」：目标「明天下午三点开会」排 18，前面全是「明天上午十点开X会」

本质：
  新记忆 = 旧记忆 + 一个值的改动
  → 新记忆与"旧记忆"的余弦，反而可能低于与"同槽但值也不同"的干扰项。

  余弦最像的往往是"最新写入的同类记忆"，而不是"被它替换的那条"。

改进策略（三选一，实测对比）：
  M0 纯余弦 top-K（基线）
  M1 余弦 top-K + 按槽位（值类型+措辞骨架）分组，每组至少留一条
  M2 余弦 top-K + 时间衰减加权（越久远的同类记忆越可能是被替换的旧值）
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e128")
RESULTS = os.path.join(_HERE, "results")

CASES = [
    ("我的车是白色卡罗拉", "我换车了现在是黑色大众"),
    ("我住在城东花园3栋", "我搬家到城西新村5号"),
    ("家里的门锁密码是 3721", "门锁密码换成了9512"),
    ("接口参数定的是0.85", "接口参数改成了1.2"),
    ("这个项目 15 号截止", "客户同意把截止延到20号"),
    ("明天下午三点开会", "会议改到明天上午十点"),
    ("我电脑的登录密码是 8848", "我把电脑密码改成了9988"),
    ("客户要求界面必须支持深色模式", "客户还要求界面必须支持大字体"),
    ("我要连续吃两周的药", "另外我晚上也要吃一次药"),
    ("老王说门锁密码是 3721", "小李说门锁密码是9512"),
]


def skeleton(text):
    """
    抽取"措辞骨架"：把具体的值挖掉，留下句式结构。
    用于判断两条记忆是不是同一个槽位。
    """
    t = text or ""
    # 挖掉数字与值
    t = re.sub(r"\d+\.?\d*\s*(ms|毫秒|秒|s|核|G|GB|MB|TB|%|倍|元|块|万|"
               r"个|件|条|台|人|次|天|周|月|年|小时|号|日)", "#", t)
    t = re.sub(r"\d+(\.\d+)?", "#", t)
    # 挖掉颜色/时段/星期等可变词
    for w in ["白", "黑", "银", "红", "蓝", "灰", "上午", "下午", "晚上",
              "凌晨", "明天", "后天", "昨天", "今天", "本周", "下周"]:
        t = t.replace(w, "")
    return t


def run(db_path, top_k=10):
    from store_v2 import MemoryStore
    from store_v1 import Embedder
    import numpy as np

    st = MemoryStore(db_path)
    emb = Embedder.get()

    rows = st.con.execute(
        "SELECT ep_id, raw_text, created_at FROM episodes "
        "WHERE invalid_at IS NULL").fetchall()
    text_of = {e: t for e, t, c in rows}
    time_of = {e: (c or 0) for e, t, c in rows}
    id_of_text = {t: e for e, t in text_of.items()}

    st._load_vectors(include_invalid=False)
    norms = np.linalg.norm(st._vec, axis=1)
    norms[norms == 0] = 1.0

    results = {"M0": [], "M1": [], "M2": []}
    max_t = max(time_of.values()) if time_of else 1.0

    for target, new in CASES:
        tgt_id = id_of_text.get(target)
        if not tgt_id:
            continue

        nv = emb.encode(new)[0]
        nv_norm = float(np.linalg.norm(nv)) or 1.0
        sims = (st._vec @ nv) / (norms * nv_norm)

        # 候选池：余弦前 200（够大，用来做后续重排）
        pool_n = min(200, len(sims))
        pool_idx = np.argpartition(-sims, pool_n - 1)[:pool_n]
        pool_idx = pool_idx[np.argsort(-sims[pool_idx])]

        cands = []
        for i in pool_idx:
            eid = st._vec_ids[int(i)]
            cands.append({
                "ep_id": eid,
                "text": text_of.get(eid, ""),
                "cos": float(sims[int(i)]),
                "created": time_of.get(eid, 0),
                "skel": skeleton(text_of.get(eid, "")),
            })

        # ---------- M0 纯余弦 top-K ----------
        m0 = cands[:top_k]
        results["M0"].append({
            "target": target, "new": new,
            "in": any(c["ep_id"] == tgt_id for c in m0),
            "rank": next((i + 1 for i, c in enumerate(m0)
                          if c["ep_id"] == tgt_id), 0),
        })

        # ---------- M1 槽位多样性 ----------
        # 按骨架分组，每组先取余弦最高的，再在剩余席位里按余弦补
        groups = {}
        for c in cands:
            groups.setdefault(c["skel"], []).append(c)
        picked = []
        # 第一轮：每个骨架组取一条（组内余弦最高）
        for skel, items in sorted(groups.items(),
                                 key=lambda kv: -kv[1][0]["cos"]):
            if len(picked) < top_k:
                picked.append(items[0])
        # 第二轮：用剩余席位补余弦最高（保持多样性前提下）
        used = {c["ep_id"] for c in picked}
        for c in cands:
            if len(picked) >= top_k:
                break
            if c["ep_id"] not in used:
                picked.append(c)
                used.add(c["ep_id"])

        results["M1"].append({
            "target": target, "new": new,
            "in": any(c["ep_id"] == tgt_id for c in picked),
            "rank": next((i + 1 for i, c in enumerate(picked)
                          if c["ep_id"] == tgt_id), 0),
        })

        # ---------- M2 时间衰减 ----------
        # 越老越可能是被替换的旧值 → 给老记忆加分
        # 用 created_at 的相对位置做权重
        scored = []
        for c in cands:
            age = (max_t - c["created"]) / max(max_t, 1e-9)   # 0=最新, 1=最老
            # 余弦为主，年龄为辅（最多加 0.08）
            scored.append({**c, "combined": c["cos"] + 0.08 * age})
        scored.sort(key=lambda x: -x["combined"])
        m2 = scored[:top_k]
        results["M2"].append({
            "target": target, "new": new,
            "in": any(c["ep_id"] == tgt_id for c in m2),
            "rank": next((i + 1 for i, c in enumerate(m2)
                          if c["ep_id"] == tgt_id), 0),
        })

    st.con.close()
    return results


def main():
    print("=" * 94)
    print("E-129 候选包召回策略对比（1000 条规模）")
    print("=" * 94)

    db = os.path.join(TASK, "e128_1000.db")
    if not os.path.exists(db):
        print("缺少 1000 条测试库，先跑 E-128")
        return

    res = run(db, top_k=10)

    print(f"\n{'用例':<32}{'M0纯余弦':<12}{'M1槽位多样':<12}{'M2时间衰减'}")
    print("-" * 94)
    for m0, m1, m2 in zip(res["M0"], res["M1"], res["M2"]):
        f0 = "✓#" + str(m0["rank"]) if m0["in"] else "✗"
        f1 = "✓#" + str(m1["rank"]) if m1["in"] else "✗"
        f2 = "✓#" + str(m2["rank"]) if m2["in"] else "✗"
        print(f"{m0['new'][:30]:<32}{f0:<12}{f1:<12}{f2}")

    print("-" * 94)
    for k in ["M0", "M1", "M2"]:
        n = sum(1 for r in res[k] if r["in"])
        rate = n / len(res[k]) if res[k] else 0
        print(f"  {k} 命中率: {n}/{len(res[k])} = {rate:.4f}")

    best = max(["M0", "M1", "M2"],
               key=lambda k: sum(1 for r in res[k] if r["in"]))
    print(f"\n  最优策略: {best}")

    out = {"experiment": "E-129", "db": db,
           "hit_rates": {k: round(sum(1 for r in res[k] if r["in"]) / len(res[k]), 4)
                         for k in res},
           "details": res}
    with open(os.path.join(RESULTS, "e129_strategy.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e129_strategy.json')}")


if __name__ == "__main__":
    main()
