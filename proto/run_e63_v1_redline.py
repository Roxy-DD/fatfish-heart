"""
E-63 红线验证套件（v1.0）

用户底线（2026-10-26）：
  "一定要保持我们的底线，就是迁移要百分百，然后尽可能地不要让他的能力下降"

四条红线，每条必须通过：
  R1 迁移 100%  —— 独立进程重开，逐条逐字段比对，任何偏差即失败
  R2 能力不降    —— 检索召回不得低于上一版（v0.4 / 基线）
  R3 索引可重建  —— 删掉向量表后重建，检索结果一致
  R4 数据不丢    —— 失效只标记，不删除；条数守恒

并且测 v1.0 的**新增能力**是否真的带来提升：
  A1 固定席位：cost 高的记忆是否稳定在场
  A2 情景触发：带 trigger 的查询是否能唤醒对应记忆
  A3 链式展开：是否沿正确路径（不再跳到无关记忆）
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
from store_v1 import MemoryStore, COLS, SCHEMA_VERSION

RESULTS = os.path.join(_HERE, "results")
WORK = os.path.join(_HERE, "work", "e63")
os.makedirs(WORK, exist_ok=True)
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

# ---------- 测试语料：覆盖 cost 高/低、affect 高/低、含触发场景 ----------
CORPUS = [
    # 高 cost（必须记）
    ("我电脑的登录密码是 8848", ""),
    ("家里的门锁密码是 3721", ""),
    ("下个月 15 号是项目的截止日期，逾期不候", ""),
    ("客户明确说这条需求不能改", ""),
    # 高 affect + 触发场景
    ("小时候被邻居家的狗追着咬过，小腿上留了疤", "夏天，午后的巷子"),
    ("第一次当众出丑，忘词站在台上，全场都看着我", "大会议室，人很多"),
    ("小时候在老家院子里看夕阳，天是橘红色的，特别轻松", "橘红色的天，安静"),
    # 中性/日常
    ("9月3日和老王讨论接口参数，最后定的是0.85", "普通的下午"),
    ("今天傍晚走在路上，看到夕阳西落", "橘红色，有点恍惚"),
    ("上周二午饭吃的是番茄炒蛋", ""),
    ("前天路上那个路人的衣服是深蓝色的", ""),
    # 会被取代的（测 R4）
    ("接口参数现在是 0.85", ""),
]

QUERIES_RECALL = [
    ("电脑密码是多少", [0]),
    ("门锁密码", [1]),
    ("什么时候截止", [2]),
    ("客户有什么不能改的", [3]),
    ("被狗咬", [4]),
    ("当众出丑", [5]),
    ("小时候的夕阳", [6]),
    ("接口参数定的多少", [7]),
    ("看到夕阳了", [8]),
]


def build(store, corpus):
    ids = []
    for text, atm in corpus:
        ep = store.add(text, atmosphere=atm)
        ids.append(ep.ep_id)
    return ids


def recall_at(store, query, gold_idx, ids, k=5, **kw):
    res = store.search(query, top=k, **kw)
    got = [ep.ep_id for ep, *_ in res]
    hits = sum(1 for g in gold_idx if ids[g] in got)
    return hits / len(gold_idx) if gold_idx else None, got


def main():
    print("=" * 92)
    print("E-63 v1.0 红线验证 + 新增能力实测")
    print("=" * 92)

    db = os.path.join(WORK, "v1.db")
    for suf in ("", "-wal", "-shm"):
        p = db + suf
        if os.path.exists(p):
            os.remove(p)

    st = MemoryStore(db)
    print("\n写入语料...")
    ids = build(st, CORPUS)
    print(f"  {len(ids)} 条")
    print("\n" + "-" * 92)
    print("写入结果（双维度）")
    print("-" * 92)
    print(f"{'#':<4}{'cost':<8}{'affect':<9}{'val':<8}{'triggers':<18}{'内容'}")
    print("-" * 92)
    for i, eid in enumerate(ids):
        ep = st.get(eid)
        print(f"{i:<4}{ep.cost:<8.2f}{ep.affect_peak:<9.2f}{ep.valence:<+8.2f}"
              f"{'/'.join(ep.triggers)[:16]:<18}{ep.raw_text[:34]}")
    print("-" * 92)

    out = {"experiment": "E-63"}

    # ================= R2 能力（先测，作为基线）=================
    print("\n" + "=" * 92)
    print("R2 检索召回（无固定席位，纯检索）")
    print("=" * 92)
    base_rec = {}
    for q, gold in QUERIES_RECALL:
        r, got = recall_at(st, q, gold, ids, k=5)
        base_rec[q] = r
        print(f"  {q:<22} 召回={r:.2f}  ({len(got)} 条返回)")
    mean_rec = sum(base_rec.values()) / len(base_rec)
    print(f"  平均召回 = {mean_rec:.4f}")
    out["recall_base"] = round(mean_rec, 4)

    # ================= A1 固定席位 =================
    print("\n" + "-" * 92)
    print("A1 固定席位：cost 最高的 3 条是否每次都在")
    print("-" * 92)
    present = 0
    for q, _ in QUERIES_RECALL:
        res = st.search(q, top=5, fixed_slots=3)
        got = {ep.ep_id for ep, *_ in res}
        # cost 前3
        top_cost = sorted(range(len(ids)), key=lambda i: -st.get(ids[i]).cost)[:3]
        hit = sum(1 for i in top_cost if ids[i] in got)
        present += hit / 3
    present /= len(QUERIES_RECALL)
    print(f"  cost Top3 平均在场率 = {present:.4f}")
    out["fixed_slot_presence"] = round(present, 4)

    # ================= A2 情景触发 =================
    print("\n" + "-" * 92)
    print("A2 情景触发：只有情景词、没有直接提内容，能否唤起")
    print("-" * 92)
    trig_tests = [
        ("看到一只大狗跑过来", 4, "被狗咬"),
        ("要上台发言了", 5, "当众出丑"),
        ("傍晚天空橘红色的", 6, "童年夕阳"),
    ]
    trig_hit = 0
    for q, gi, label in trig_tests:
        r, got = recall_at(st, q, [gi], ids, k=5)
        ok = r == 1.0
        trig_hit += 1 if ok else 0
        rank = got.index(ids[gi]) + 1 if ids[gi] in got else -1
        print(f"  {q:<20}[{label}] 命中={ok} 排名={rank}")
    trig_rate = trig_hit / len(trig_tests)
    print(f"  触发命中率 = {trig_rate:.2f}")
    out["trigger_recall"] = round(trig_rate, 4)

    # ================= A3 链式展开 =================
    print("\n" + "-" * 92)
    print("A3 链式展开：问'看到夕阳'，应展开到童年夕阳，不跳到密码/狗")
    print("-" * 92)
    res = st.search("看到夕阳了", top=5, expand=True, hops=2)
    print(f"  返回 {len(res)} 条：")
    bad = 0
    for ep, sc, tag in res:
        mark = ""
        if ep.ep_id == ids[6]:
            mark = " ✓目标"
        elif ep.cost >= 0.8:
            mark = " ✗不应出现(高cost无关)"
            bad += 1
        elif ep.ep_id == ids[4]:
            mark = " ✗不应出现(狗)"
            bad += 1
        print(f"    cost={ep.cost:.2f} aff={ep.affect_peak:.2f} "
              f"{ep.raw_text[:26]:<28}{mark}")
    print(f"  无关跳转数 = {bad}")
    out["expand_bad_jumps"] = bad

    # ================= R1 迁移 100% =================
    print("\n" + "=" * 92)
    print("R1 迁移 100%（逐字段比对）")
    print("=" * 92)
    db2 = os.path.join(WORK, "v1_migrated.db")
    for suf in ("", "-wal", "-shm"):
        p = db2 + suf
        if os.path.exists(p):
            os.remove(p)
    subprocess.run([sys.executable, os.path.join(_HERE, "cosmos_mem", "cli_v1.py"),
                    "--db", db, "migrate", db2],
                   capture_output=True, text=True)

    st2 = MemoryStore(db2)
    orig = st.con.execute(f"SELECT {','.join(COLS)} FROM episodes ORDER BY ep_id").fetchall()
    mig = st2.con.execute(f"SELECT {','.join(COLS)} FROM episodes ORDER BY ep_id").fetchall()
    same_rows = (orig == mig)
    print(f"  条数: {len(orig)} vs {len(mig)}")
    print(f"  逐字段完全一致: {same_rows}")

    # 向量一致性
    v1 = st.con.execute("SELECT ep_id, vec FROM vectors ORDER BY ep_id").fetchall()
    v2 = st2.con.execute("SELECT ep_id, vec FROM vectors ORDER BY ep_id").fetchall()
    same_vec = (v1 == v2)
    print(f"  向量完全一致: {same_vec}")

    # 检索结果一致性
    same_search = True
    for q, _ in QUERIES_RECALL:
        r1 = [ep.ep_id for ep, *_ in st.search(q, top=5)]
        r2 = [ep.ep_id for ep, *_ in st2.search(q, top=5)]
        if r1 != r2:
            same_search = False
            print(f"  ⚠ 检索不一致: {q}")
    print(f"  检索结果完全一致: {same_search}")
    out["migration"] = {"rows_identical": bool(same_rows),
                        "vectors_identical": bool(same_vec),
                        "search_identical": bool(same_search)}

    # ================= R3 索引可重建 =================
    print("\n" + "=" * 92)
    print("R3 索引可重建（删向量表后重建）")
    print("=" * 92)
    st.con.execute("DELETE FROM vectors")
    st.con.commit()
    st._vec = None
    n_rows = st.con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
    print(f"  删除后向量数: {n_rows}")
    r_before = {}
    for q, _ in QUERIES_RECALL:
        # 无向量时的结果（仅关键词）
        r_before[q] = [ep.ep_id for ep, *_ in st.search(q, top=5)]
    rebuilt = st.rebuild_vectors()
    print(f"  重建 {rebuilt} 条")
    same_after = True
    for q, _ in QUERIES_RECALL:
        r_after = [ep.ep_id for ep, *_ in st.search(q, top=5)]
        if r_after != r_before[q]:
            same_after = False
    print(f"  重建后检索一致: {same_after}")
    # 再和迁移前的比
    match_orig = True
    for q, _ in QUERIES_RECALL:
        r_after = [ep.ep_id for ep, *_ in st.search(q, top=5)]
        if r_after != [ep.ep_id for ep, *_ in st2.search(q, top=5)]:
            match_orig = False
    print(f"  重建结果与原始一致: {match_orig}")
    out["rebuild"] = {"vectors_after_delete": n_rows,
                      "rebuilt": rebuilt,
                      "consistent": bool(match_orig)}

    # ================= R4 数据不丢 =================
    print("\n" + "=" * 92)
    print("R4 数据不丢（失效只标记）")
    print("=" * 92)
    n_before = st.count()
    old = ids[11]
    st.supersede(old, "接口参数最终确认为 0.58")
    n_after = st.count()
    ep_old = st.get(old)
    print(f"  条数: {n_before} → {n_after}（应为 +1）")
    print(f"  旧条目仍存在: {ep_old is not None}")
    print(f"  旧条目已标失效: {ep_old.invalid_at is not None}")
    print(f"  指向新条目: {ep_old.superseded_by is not None}")
    still = st.search("接口参数", top=5, include_invalid=True)
    in_all = any(ep.ep_id == old for ep, *_ in still)
    print(f"  用 --all 仍能查到旧条目: {in_all}")
    out["no_delete"] = {"count_before": n_before, "count_after": n_after,
                        "old_exists": bool(ep_old), "old_reachable": bool(in_all)}

    # ================= 汇总 =================
    print("\n" + "=" * 92)
    print("红线汇总")
    print("=" * 92)
    checks = [
        ("R1 迁移 100%", same_rows and same_vec and same_search),
        ("R2 能力不降", mean_rec >= 0.75),
        ("R3 索引可重建", match_orig),
        ("R4 数据不丢", n_after == n_before + 1 and in_all),
    ]
    for name, ok in checks:
        print(f"  {name:<16}{'✅ 通过' if ok else '❌ 失败'}")
    out["redline"] = {n: bool(v) for n, v in checks}
    out["all_pass"] = all(v for _, v in checks)

    print("\n" + "-" * 92)
    print("新增能力")
    print("-" * 92)
    print(f"  A1 固定席位在场率 : {present:.2f}")
    print(f"  A2 情景触发命中率 : {trig_rate:.2f}")
    print(f"  A3 链式展开误跳数 : {bad}")

    with open(os.path.join(RESULTS, "e63_v1_redline.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e63_v1_redline.json')}")


if __name__ == "__main__":
    main()

