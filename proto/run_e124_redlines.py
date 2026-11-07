"""
E-124 四条红线复验（改了存储层之后必做）

改动内容：
  · store_v1._load_vectors  → 按 include_invalid 分开缓存
  · store_v2.add            → 接入矛盾处理（候选包 + 可选模型判断）
  · meta 表                 → 新增 conflict: / conflict-pack: 记录

《34 实验基准》要求每个实验必报四项，其中 2、3 项是硬性要求：
  1. 性能指标
  2. ★ 无损迁移复验（必须 100%）
  3. ★ 召回未退化（对比基线 0.881）
  4. 结论：是否触碰红线

本实验使用的固定测试集（不随机，保证可复现可对比）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
DB = os.path.join(_HERE, "e124_redline.db")

# ============================================================
# 固定测试集：40 条记忆 + 15 条查询（含五类查询形态）
# ============================================================
MEMORIES = [
    # 人-属性
    "张伟的工号是A1024", "张伟的手机号是13800138001",
    "李娜的工号是B2048", "李娜的手机号是13900139002",
    "王强的工号是C3072", "王强的手机号是13700137003",
    "赵敏的工号是D4096", "赵敏的手机号是13600136004",
    # 项目-属性
    "星火项目的负责人是张伟", "星火项目的截止日期是11月15号",
    "曙光项目的负责人是李娜", "曙光项目的截止日期是12月20号",
    "灯塔项目的负责人是王强", "灯塔项目的预算是50万",
    # 任务
    "接口文档由张伟负责", "单元测试由李娜负责",
    "性能压测由王强负责", "验收报告由赵敏负责",
    # 事件
    "上周三开了星火项目启动会", "上周五曙光项目通过了评审",
    "昨天灯塔项目遇到接口超时问题", "今天上午修复了登录页的bug",
    # 约定/偏好
    "团队规定代码必须过CI才能合并", "团队要求接口文档必须在开发前完成",
    "客户要求所有接口响应时间低于200ms", "客户强调界面必须支持深色模式",
    # 其他事实
    "服务器部署在阿里云华东区", "数据库用的是PostgreSQL 15",
    "缓存层用的是Redis 7", "消息队列用的是Kafka",
    "前端框架用React 18", "后端框架用FastAPI",
    "测试环境在192.168.1.50", "生产环境在10.0.0.20",
    "代码仓库托管在GitLab", "持续集成用Jenkins",
    "监控用Prometheus", "日志用ELK",
    "文档放在Confluence", "需求管理用Jira",
]

# 查询（kind, 查询文本, 期望命中的记忆文本片段列表）
QUERIES = [
    ("Q1_单跳", "张伟的工号是多少", ["张伟的工号是A1024"]),
    ("Q1_单跳", "李娜的手机号", ["李娜的手机号是13900139002"]),
    ("Q1_单跳", "王强的工号", ["王强的工号是C3072"]),
    ("Q2_两跳", "星火项目的负责人是谁", ["星火项目的负责人是张伟"]),
    ("Q2_两跳", "曙光项目谁负责", ["曙光项目的负责人是李娜"]),
    ("Q2_两跳", "灯塔项目预算是多少", ["灯塔项目的预算是50万"]),
    ("Q3_话题", "接口文档谁负责", ["接口文档由张伟负责"]),
    ("Q3_话题", "性能测试由谁做", ["性能压测由王强负责"]),
    ("Q3_话题", "验收报告归谁", ["验收报告由赵敏负责"]),
    ("Q4_标识", "代码合并有什么要求", ["团队规定代码必须过CI才能合并"]),
    ("Q4_标识", "接口响应时间要求", ["客户要求所有接口响应时间低于200ms"]),
    ("Q5_改写", "咱们服务器放在哪了", ["服务器部署在阿里云华东区"]),
    ("Q5_改写", "缓存用的啥", ["缓存层用的是Redis 7"]),
    ("Q5_改写", "前端技术栈", ["前端框架用React 18"]),
    ("Q5_改写", "数据库是什么", ["数据库用的是PostgreSQL 15"]),
]

K = 5   # 召回@5


def fresh(p):
    for suf in ("", "-wal", "-shm"):
        f = p + suf
        if os.path.exists(f):
            os.remove(f)


def build_and_write():
    """写入测试库，返回 ep_id 映射"""
    from store_v2 import MemoryStore
    fresh(DB)
    st = MemoryStore(DB)
    ids = {}
    for m in MEMORIES:
        # 关键：测试召回时不做矛盾判定（避免用例互相干扰），
        # 矛盾判定本身已在 E-123 单独验证
        ep = st.add(m, resolve_conflict=False)
        ids[m] = ep.ep_id
    st.con.close()
    return ids


def measure_recall(label=""):
    """测召回@5"""
    from store_v2 import MemoryStore
    st = MemoryStore(DB)
    ids = {}
    for eid, txt in st.con.execute(
            "SELECT ep_id, raw_text FROM episodes").fetchall():
        ids[txt] = eid

    per_kind = {}
    details = []
    for kind, q, expected in QUERIES:
        gold_ids = {ids[e] for e in expected if e in ids}
        res = st.search(q, top=K)
        got = [ep.ep_id for ep, *_ in res]
        hit = len(set(got[:K]) & gold_ids)
        r = hit / len(gold_ids) if gold_ids else None
        per_kind.setdefault(kind, []).append(r)
        details.append({"kind": kind, "q": q, "recall": r,
                        "top1": res[0][0].raw_text if res else ""})

    all_r = [r for rs in per_kind.values() for r in rs if r is not None]
    overall = sum(all_r) / len(all_r)
    by_kind = {k: (sum(v) / len(v)) for k, v in per_kind.items() if v}
    st.con.close()
    return overall, by_kind, details


def test_migration_v1():
    """R1：无损迁移 —— 写入 → 重开连接 → 逐条比对"""
    from store_v2 import MemoryStore

    # 阶段1：记录基准
    st = MemoryStore(DB)
    rows1 = st.con.execute(
        "SELECT ep_id, raw_text, cost, affect_peak, valence, confidence, "
        "revision, text_hash FROM episodes ORDER BY ep_id").fetchall()
    vecs1 = st.con.execute(
        "SELECT ep_id, vec FROM vectors ORDER BY ep_id").fetchall()
    # 记录检索结果
    qres1 = {}
    for kind, q, _ in QUERIES:
        qres1[q] = [ep.ep_id for ep, *_ in st.search(q, top=K)]
    st.con.close()

    # 阶段2：重新打开
    st2 = MemoryStore(DB)
    rows2 = st2.con.execute(
        "SELECT ep_id, raw_text, cost, affect_peak, valence, confidence, "
        "revision, text_hash FROM episodes ORDER BY ep_id").fetchall()
    vecs2 = st2.con.execute(
        "SELECT ep_id, vec FROM vectors ORDER BY ep_id").fetchall()
    qres2 = {}
    for kind, q, _ in QUERIES:
        qres2[q] = [ep.ep_id for ep, *_ in st2.search(q, top=K)]
    st2.con.close()

    # 逐条比对
    n_rows = len(rows1)
    row_diff = sum(1 for a, b in zip(rows1, rows2) if a != b)
    vec_diff = sum(1 for a, b in zip(vecs1, vecs2) if a[1] != b[1])
    q_diff = sum(1 for q in qres1 if qres1[q] != qres2[q])

    return {
        "records": n_rows,
        "row_mismatch": row_diff,
        "vector_mismatch": vec_diff,
        "query_mismatch": q_diff,
        "consistent": (row_diff == 0 and vec_diff == 0 and q_diff == 0
                       and len(rows1) == len(rows2)),
    }


def test_migration_v2():
    """R1 加强：写到新文件后重载，与原地重载结果一致"""
    from store_v2 import MemoryStore

    new_db = os.path.join(_HERE, "e124_redline_copy.db")
    fresh(new_db)
    shutil.copy(DB, new_db)

    st = MemoryStore(new_db)
    rows = st.con.execute(
        "SELECT ep_id, raw_text, cost, affect_peak FROM episodes ORDER BY ep_id").fetchall()
    qres = {}
    for kind, q, _ in QUERIES:
        qres[q] = [ep.ep_id for ep, *_ in st.search(q, top=K)]
    st.con.close()

    # 与原地结果比对
    st2 = MemoryStore(DB)
    rows_b = st2.con.execute(
        "SELECT ep_id, raw_text, cost, affect_peak FROM episodes ORDER BY ep_id").fetchall()
    qres_b = {}
    for kind, q, _ in QUERIES:
        qres_b[q] = [ep.ep_id for ep, *_ in st2.search(q, top=K)]
    st2.con.close()

    return {
        "rows_equal": rows == rows_b,
        "queries_equal": qres == qres_b,
        "consistent": (rows == rows_b and qres == qres_b),
    }


def test_r2_no_delete():
    """R2：不遗忘 —— 所有记录仍在，失效只打标记"""
    from store_v2 import MemoryStore
    st = MemoryStore(DB)
    total = st.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    valid = st.con.execute(
        "SELECT COUNT(*) FROM episodes WHERE invalid_at IS NULL").fetchone()[0]
    invalid = total - valid
    vec_total = st.con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
    st.con.close()
    return {"total": total, "valid": valid, "invalid": invalid,
            "vectors": vec_total,
            "no_deletion": total == len(MEMORIES) and vec_total == total}


def test_r3_rebuild():
    """R3：索引可确定性重建 —— 重建前后检索一致"""
    from store_v2 import MemoryStore
    st = MemoryStore(DB)

    before = {}
    for kind, q, _ in QUERIES:
        before[q] = [ep.ep_id for ep, *_ in st.search(q, top=K)]

    n = st.rebuild_vectors()
    st._vec = None
    st.con.close()

    st2 = MemoryStore(DB)
    after = {}
    for kind, q, _ in QUERIES:
        after[q] = [ep.ep_id for ep, *_ in st2.search(q, top=K)]
    st2.con.close()

    diff = sum(1 for q in before if before[q] != after[q])
    return {"rebuilt": n, "query_mismatch": diff,
            "deterministic": diff == 0}


def test_cache_freshness():
    """
    回归测试：E-126 的缓存 bug 不能重现。

    那个 bug 的表现：常驻进程里写入新记忆后，_load_vectors 命中旧缓存，
    新记忆进不了候选包 → 矛盾判定看不到目标 → 只能判 none。

    本测试在同一个 store 实例上连续写入，验证新写入立即可见。
    """
    from store_v2 import MemoryStore
    from store_v1 import Embedder

    db = os.path.join(_HERE, "e124_cache.db")
    fresh(db)
    st = MemoryStore(db)
    emb = Embedder.get()

    # 先写一条，触发缓存建立
    st.add("我家的 WiFi 密码是 home2024", resolve_conflict=False)

    # 确认缓存已建立
    st._load_vectors(include_invalid=False)
    cache_before = len(st._vec_ids)

    # 再写一条矛盾记忆（不调模型，只建包）
    ep2 = st.add("我把 WiFi 密码改成 newwifi888 了", resolve_conflict=False)

    # 关键：不清缓存的情况下，_load_vectors 应该能看到新写入
    st._load_vectors(include_invalid=False)
    cache_after = len(st._vec_ids)

    # 新记忆是否在向量矩阵里
    visible = ep2.ep_id in st._vec_ids

    # 用 prepare_candidates 验证目标能进候选包
    import conflict as _conflict
    pack = _conflict.prepare_candidates(st, emb, "我把 WiFi 密码改成 newwifi888 了")
    target_in_pack = any("home2024" in c.text for c in pack.candidates)

    st.con.close()

    return {
        "cache_before_write": cache_before,
        "cache_after_write": cache_after,
        "new_visible_immediately": visible and cache_after == cache_before + 1,
        "cache_cleared": visible,
        "target_in_pack": target_in_pack,
        "passed": bool(visible and target_in_pack
                       and cache_after == cache_before + 1),
    }


def main():
    print("=" * 92)
    print("E-124 四条红线复验（改动存储层之后）")
    print("=" * 92)

    print(f"\n测试集：{len(MEMORIES)} 条记忆，{len(QUERIES)} 条查询，召回@{K}")

    # ---------- 写入 ----------
    ids = build_and_write()
    print(f"写入完成：{len(ids)} 条")

    # ---------- ① 性能 + 召回 ----------
    print("\n" + "-" * 92)
    print("① 召回（对比基线 0.881）")
    print("-" * 92)
    overall, by_kind, details = measure_recall()
    print(f"{'查询类型':<14}{'召回率':<12}{'样本数'}")
    print("-" * 92)
    for k in sorted(by_kind):
        n = sum(1 for d in details if d["kind"] == k)
        print(f"{k:<14}{by_kind[k]:<12.4f}{n}")
    print("-" * 92)
    print(f"{'总体':<14}{overall:<12.4f}{len(QUERIES)}")
    print(f"{'基线 E-39':<14}{0.881:<12.4f}")
    delta = overall - 0.881
    print(f"{'差值':<14}{delta:+.4f}")

    # ---------- ② 迁移 ----------
    print("\n" + "-" * 92)
    print("② ★ R1 无损迁移")
    print("-" * 92)
    m1 = test_migration_v1()
    print(f"  原地重载：记录 {m1['records']} 条，"
          f"字段差异 {m1['row_mismatch']}，向量差异 {m1['vector_mismatch']}，"
          f"检索差异 {m1['query_mismatch']}")
    print(f"  → {'✅ 一致' if m1['consistent'] else '❌ 不一致'}")

    m2 = test_migration_v2()
    print(f"  异库重载：行相等={m2['rows_equal']}，检索相等={m2['queries_equal']}")
    print(f"  → {'✅ 一致' if m2['consistent'] else '❌ 不一致'}")

    # ---------- ③ R2 ----------
    print("\n" + "-" * 92)
    print("③ ★ R2 不遗忘")
    print("-" * 92)
    r2 = test_r2_no_delete()
    print(f"  记录总数 {r2['total']}（应 {len(MEMORIES)}）  "
          f"有效 {r2['valid']}  失效 {r2['invalid']}  向量 {r2['vectors']}")
    print(f"  → {'✅ 无删除' if r2['no_deletion'] else '❌ 有删除'}")

    # ---------- ④ R3 ----------
    print("\n" + "-" * 92)
    print("④ ★ R3 索引可重建")
    print("-" * 92)
    r3 = test_r3_rebuild()
    print(f"  重建 {r3['rebuilt']} 条向量，检索差异 {r3['query_mismatch']}")
    print(f"  → {'✅ 确定性' if r3['deterministic'] else '❌ 不确定'}")

    # ---------- ⑤ 缓存正确性（防 E-126 回归）----------
    print("\n" + "-" * 92)
    print("⑤ ★ 缓存正确性回归（E-126 的 bug 不能重现）")
    print("-" * 92)
    r5 = test_cache_freshness()
    print(f"  写入后立即可见: {'✅' if r5['new_visible_immediately'] else '❌ 不可见'}")
    print(f"  缓存命中不返回旧矩阵: {'✅' if r5['cache_cleared'] else '❌ 返回了旧矩阵'}")
    print(f"  → {'✅ 通过' if r5['passed'] else '❌ 失败'}")

    # ---------- 结论 ----------
    print("\n" + "=" * 92)
    print("结论（《34 实验基准》要求四项）")
    print("=" * 92)
    print(f"  1. 性能指标：召回@{K} = {overall:.4f}（基线 0.881，差 {delta:+.4f}）")
    print(f"  2. ★ 无损迁移：{'100% ✅' if m1['consistent'] and m2['consistent'] else '未达标 ❌'}")
    print(f"  3. ★ 召回未退化：{'✅ 未退化' if delta >= -0.02 else '❌ 退化 ' + f'{delta:+.4f}'}")
    print(f"  4. R2 不遗忘：{'✅' if r2['no_deletion'] else '❌'}   "
          f"R3 可重建：{'✅' if r3['deterministic'] else '❌'}   "
          f"缓存正确：{'✅' if r5['passed'] else '❌'}")

    passed = (m1["consistent"] and m2["consistent"]
              and delta >= -0.02 and r2["no_deletion"] and r3["deterministic"]
              and r5["passed"])
    print(f"\n  {'✅ 四条红线全部通过，改动可以保留' if passed else '❌ 有红线未通过，改动需回退'}")

    out = {"experiment": "E-124",
           "recall": round(overall, 4),
           "recall_baseline": 0.881,
           "recall_delta": round(delta, 4),
           "by_kind": {k: round(v, 4) for k, v in by_kind.items()},
           "migration_inplace": m1,
           "migration_crossfile": m2,
           "r2_no_delete": r2,
           "r3_rebuild": r3,
           "cache_freshness": r5,
           "redlines_passed": passed,
           "details": details}
    with open(os.path.join(RESULTS, "e124_redlines.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e124_redlines.json')}")


if __name__ == "__main__":
    main()
