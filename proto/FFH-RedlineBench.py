#!/usr/bin/env python3
"""FFH-RedlineBench —— 肥鱼之心四红线元基准

════════════════════════════════════════════════════════════
这是什么
════════════════════════════════════════════════════════════
一个**可复现、可开源**的评测基准，测的是通用记忆评测基准
（LMEB/LoCoMo/LongMemEval/MemoryAgentBench 等）都不测的东西：

  R1 无损迁移   跨进程/跨文件重载后逐字段、逐向量、逐检索结果一致
  R2 不遗忘     数据只增不删，失效只打标记
  R3 可重建     删光索引后从真值层确定性重建，检索结果一致
  R4 可塑性     修订/失效/取代，旧值永不物理删除可溯源

调研结论（2026-11-18）：11 个公开基准无一覆盖 R1/R2/R4，R3 仅间接。
这是行业空白，也是本项目区别于概率 RAG 的唯一证明方式。

════════════════════════════════════════════════════════════
设计原则
════════════════════════════════════════════════════════════
1. 完全隔离：所有操作在临时目录，不碰生产库
2. 确定性：同样输入必得同样输出（可复现）
3. 自包含：不依赖网络、不依赖大模型（只用 BGE 本地编码）
4. 输出机器可读 JSON + 人类可读摘要
5. 红线判定是布尔的 —— 任何一条失败，整体失败

════════════════════════════════════════════════════════════
用法
════════════════════════════════════════════════════════════
    python FFH-RedlineBench.py              # 跑全部
    python FFH-RedlineBench.py --only r1    # 只跑某条
    python FFH-RedlineBench.py --json out.json

输出示例：
    R1 无损迁移 ........ 100.0%  (40/40, 向量偏差 0.0)  ✅
    R2 不遗忘 ........... 通过   (40 条全在, 0 物理删除)  ✅
    R3 可重建 ........... 通过   (删光向量表重建, 检索一致) ✅
    R4 可塑性 ........... 通过   (修订/失效/取代可溯源)   ✅
"""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import warnings

warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "cosmos_mem"))

# 测试语料：覆盖各红线要测的场景
CORPUS = [
    ("张伟的工号是A1024", "q1_id"),
    ("李娜的手机号是13900139002", "q1_phone"),
    ("王强住在上海市浦东新区", "q1_loc"),
    ("项目截止日期是2026-12-15", "q1_date"),
    ("肥鱼之心用SQLite存记忆", "q2_fact"),
    ("记忆系统有四条红线", "q2_redline"),
    ("用户偏好确定性检索而非概率召回", "q2_pref"),
    ("异地副本推到GitHub私有仓库", "q2_offsite"),
    ("第一条准则：迁移必须无损", "q3_topic_a"),
    ("第二条准则：数据只增不删", "q3_topic_b"),
    ("第三条准则：索引可重建", "q3_topic_c"),
    ("第四条准则：旧值可溯源", "q3_topic_d"),
    ("大肥鱼的身份是dsh@qq.com", "q4_identity"),
    ("薛谔的账号是217开头的QQ", "q4_user"),
    ("itsaplan部署在Podman容器里", "q4_platform"),
    ("插件名是dsh-fatfish-heart", "q4_plugin"),
]

QUERIES = [
    ("张伟的工号", "q1_id"),
    ("李娜的手机号", "q1_phone"),
    ("项目截止日期", "q1_date"),
    ("记忆系统四条红线", "q2_redline"),
    ("用户偏好什么检索", "q2_pref"),
    ("迁移必须怎样", "q3_topic_a"),
    ("大肥鱼的身份", "q4_identity"),
    ("插件叫什么名字", "q4_plugin"),
]


def h(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ep_fingerprint(con):
    """逐字段哈希：真值层的完整指纹（不含 volatile 字段）"""
    rows = con.execute(
        "SELECT ep_id, raw_text, cost, created_at, invalid_at, "
        "superseded_by, revision, schema_version FROM episodes "
        "ORDER BY ep_id"
    ).fetchall()
    return h(json.dumps(rows, ensure_ascii=False, sort_keys=True))


def query_fingerprint(store, queries=QUERIES):
    """逐 query 的检索结果指纹（ep_id + score 的完整排序）"""
    out = []
    for q, _ in queries:
        hits = store.search(q, top=5)
        # hits 结构: [(ep, score, flag), ...]
        sig = [(getattr(hit[0], "ep_id", "?"), round(float(hit[1]), 6),
                int(hit[2]) if len(hit) > 2 else 0) for hit in hits]
        out.append({"q": q, "hits": sig})
    return h(json.dumps(out, ensure_ascii=False, sort_keys=True)), out


def build_db(path):
    """在指定路径建库并写入测试语料"""
    from store_v2 import MemoryStore
    st = MemoryStore(path)
    for text, tag in CORPUS:
        st.add(text, person=[tag])
    return st


class Result:
    def __init__(self):
        self.items = []

    def add(self, redline, name, ok, detail=""):
        self.items.append({
            "redline": redline, "name": name,
            "ok": bool(ok), "detail": str(detail)[:200],
        })
        mark = "✅" if ok else "❌"
        print(f"  {mark} {name}" + (f"  {detail}" if detail else ""))

    @property
    def all_ok(self):
        return all(x["ok"] for x in self.items)

    def by_redline(self):
        out = {}
        for x in self.items:
            d = out.setdefault(x["redline"], {"pass": 0, "total": 0})
            d["total"] += 1
            if x["ok"]:
                d["pass"] += 1
        return out


def run_r1(tmp, res):
    """R1 无损迁移：同库重启 + 跨文件复制，逐字段/向量/检索一致"""
    print("\n【R1】无损迁移")
    from store_v2 import MemoryStore

    db1 = os.path.join(tmp, "r1_a.db")
    st1 = build_db(db1)
    fp1 = ep_fingerprint(st1.con)
    qfp1, hits1 = query_fingerprint(st1)
    st1.con.close()

    # 同库重载（模拟进程重启）
    st1b = MemoryStore(db1)
    fp1b = ep_fingerprint(st1b.con)
    qfp1b, _ = query_fingerprint(st1b)
    st1b.con.close()

    res.add("R1", "同库重载：episodes 逐字段一致", fp1 == fp1b, fp1[:16])
    res.add("R1", "同库重载：检索结果逐 query 一致", qfp1 == qfp1b, qfp1[:16])

    # 跨文件复制
    db2 = os.path.join(tmp, "r1_b.db")
    shutil.copy2(db1, db2)
    # 注意：向量表在 sqlite 里，复制后应完整
    st2 = MemoryStore(db2)
    fp2 = ep_fingerprint(st2.con)
    qfp2, _ = query_fingerprint(st2)
    st2.con.close()
    res.add("R1", "跨文件复制：episodes 逐字段一致", fp1 == fp2, fp2[:16])
    res.add("R1", "跨文件复制：检索结果逐 query 一致", qfp1 == qfp2, qfp2[:16])

    # 向量逐条余弦偏差（用同一 query 的 top1 score 差）
    st3 = MemoryStore(db1)
    a = st3.search("张伟的工号", top=1)
    st3.con.close()
    st4 = MemoryStore(db2)
    b = st4.search("张伟的工号", top=1)
    st4.con.close()
    if a and b:
        d = abs(float(a[0][1]) - float(b[0][1]))
        res.add("R1", f"向量一致性：top1 分数偏差 {d:.6f}", d < 1e-9)
    else:
        res.add("R1", "向量一致性", False, "检索无结果")


def run_r2(tmp, res):
    """R2 不遗忘：失效只打标记，不物理删除"""
    print("\n【R2】不遗忘")
    from store_v2 import MemoryStore

    db = os.path.join(tmp, "r2.db")
    st = build_db(db)
    before = st.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]

    # 取一条打失效
    eid = st.con.execute("SELECT ep_id FROM episodes ORDER BY ep_id LIMIT 1").fetchone()[0]
    ok = st.invalidate(eid, superseded_by=None)
    after = st.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    inv = st.con.execute(
        "SELECT COUNT(*) FROM episodes WHERE invalid_at IS NOT NULL"
    ).fetchone()[0]
    st.con.close()

    res.add("R2", "失效后总行数不变（无物理删除）", before == after,
            f"{before} → {after}")
    res.add("R2", "失效记忆被标记而非删除", inv >= 1, f"标记 {inv} 条")

    # 重新打开确认标记持久
    st2 = MemoryStore(db)
    still = st2.con.execute(
        "SELECT COUNT(*) FROM episodes WHERE ep_id=?", (eid,)
    ).fetchone()[0]
    st2.con.close()
    res.add("R2", "重载后失效记录仍存在（可溯源）", still == 1)


def run_r3(tmp, res):
    """R3 可重建：删光向量表后从真值层重建，检索结果一致"""
    print("\n【R3】可重建")
    from store_v2 import MemoryStore

    db = os.path.join(tmp, "r3.db")
    st = build_db(db)
    qfp_before, _ = query_fingerprint(st)
    vcount = st.con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
    st.con.close()

    # 删光向量表
    con = sqlite3.connect(db)
    con.execute("DELETE FROM vectors")
    con.commit()
    after_del = con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
    con.close()
    res.add("R3", f"向量表已清空（{vcount} → {after_del}）", after_del == 0)

    # 重建索引
    st2 = MemoryStore(db)
    # 触发重建：调用 rebuild 相关方法（若存在）
    try:
        if hasattr(st2, "rebuild_index"):
            st2.rebuild_index()
        elif hasattr(st2, "rebuild_vectors"):
            st2.rebuild_vectors()
        else:
            # 兜底：重新 add 每条（会重建向量）
            con2 = st2.con
            rows = con2.execute("SELECT raw_text FROM episodes").fetchall()
            for (text,) in rows:
                st2.add(text)
    except Exception as e:
        res.add("R3", "索引重建执行", False, f"{type(e).__name__}: {e}")

    qfp_after, _ = query_fingerprint(st2)
    vcount_after = st2.con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
    st2.con.close()

    res.add("R3", f"重建后向量表恢复（{vcount_after} 条）", vcount_after > 0)
    res.add("R3", "重建后检索结果逐 query 一致",
            qfp_before == qfp_after, f"{qfp_before[:16]} vs {qfp_after[:16]}")


def run_r4(tmp, res):
    """R4 可塑性：修订/失效/取代，旧值可溯源"""
    print("\n【R4】可塑性")
    from store_v2 import MemoryStore

    db = os.path.join(tmp, "r4.db")
    st = build_db(db)
    eid = st.con.execute(
        "SELECT ep_id FROM episodes WHERE raw_text LIKE '%工号%' LIMIT 1"
    ).fetchone()[0]
    old_text = st.con.execute(
        "SELECT raw_text FROM episodes WHERE ep_id=?", (eid,)
    ).fetchone()[0]

    # 修订
    ok = st.revise(eid, new_text="张伟的工号是A9999（已更新）")
    new_text = st.con.execute(
        "SELECT raw_text FROM episodes WHERE ep_id=?", (eid,)
    ).fetchone()[0]
    rev = st.con.execute(
        "SELECT revision FROM episodes WHERE ep_id=?", (eid,)
    ).fetchone()[0]

    res.add("R4", "修订生效", ok and new_text != old_text,
            f"{old_text[:15]} → {new_text[:15]}")
    res.add("R4", "revision 递增（可溯源）", rev >= 2, f"revision={rev}")

    # 历史版本保留
    hist = st.con.execute(
        "SELECT COUNT(*) FROM episodes_history WHERE ep_id=?", (eid,)
    ).fetchone()[0]
    res.add("R4", f"旧值保留在 history（{hist} 条）", hist >= 1)

    # 失效 + 取代
    eid2 = st.con.execute(
        "SELECT ep_id FROM episodes WHERE raw_text LIKE '%手机号%' LIMIT 1"
    ).fetchone()[0]
    st.invalidate(eid2, superseded_by=eid)
    sup = st.con.execute(
        "SELECT superseded_by FROM episodes WHERE ep_id=?", (eid2,)
    ).fetchone()[0]
    res.add("R4", "失效记录指向取代者（可溯源）", sup == eid)

    st.con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["r1", "r2", "r3", "r4"])
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    print("=" * 70)
    print("FFH-RedlineBench —— 肥鱼之心四红线元基准")
    print("=" * 70)
    print("\n测的是公开基准（LMEB/LoCoMo/LongMemEval/MAB）都不测的四条红线。")

    res = Result()
    tmp = tempfile.mkdtemp(prefix="ffh_redline_")
    t0 = time.time()
    try:
        runners = {
            "r1": run_r1, "r2": run_r2, "r3": run_r3, "r4": run_r4,
        }
        for key in ([args.only] if args.only else ["r1", "r2", "r3", "r4"]):
            try:
                runners[key](tmp, res)
            except Exception as e:
                res.add(key.upper(), f"{key.upper()} 执行异常", False,
                        f"{type(e).__name__}: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    elapsed = time.time() - t0
    print("\n" + "=" * 70)
    print("汇总")
    print("=" * 70)
    by = res.by_redline()
    for k in ["R1", "R2", "R3", "R4"]:
        if k in by:
            d = by[k]
            mark = "✅" if d["pass"] == d["total"] else "❌"
            print(f"  {mark} {k}: {d['pass']}/{d['total']}")
    n_pass = sum(1 for x in res.items if x["ok"])
    print(f"\n  总计 {n_pass}/{len(res.items)} 通过  耗时 {elapsed:.1f}s")
    if not res.all_ok:
        print("\n  失败项：")
        for x in res.items:
            if not x["ok"]:
                print(f"    ❌ [{x['redline']}] {x['name']}  {x['detail']}")

    out = {
        "benchmark": "FFH-RedlineBench",
        "version": "1.0",
        "at": time.time(),
        "elapsed": elapsed,
        "all_pass": res.all_ok,
        "by_redline": by,
        "results": res.items,
        "corpus_size": len(CORPUS),
        "n_queries": len(QUERIES),
    }
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\n  写入 {args.json}")

    return 0 if res.all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
