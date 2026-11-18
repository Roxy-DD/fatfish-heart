"""test_data.py —— 测试数据的统一判定（FISH-15 / 用户选 B）

════════════════════════════════════════════════════════════
为什么单独一个模块
════════════════════════════════════════════════════════════
2026-11-18 用户选 B：给 199 条测试残留加 is_test_data 标记，
统计时排除。

不同脚本都要用这个判定（doctor.py、run_e508_readme_claims.py、
run_e533_length_dist.py 等），所以抽成共享模块，
避免每处各自实现一套过滤逻辑（那必然漂移）。

════════════════════════════════════════════════════════════
设计
════════════════════════════════════════════════════════════
标记存在 meta.test_data_ep_ids（JSON），由 mark_test_data.py 维护。
本模块只提供读取与过滤，不写数据。

用法：
    from test_data import is_test_data, filtered, stats
    st = MemoryStore(...)
    ids = test_ids(st.con)           # set of ep_id
    n = real_count(st.con)           # 排除测试后的有效记忆数
"""
import json
import os

KEY = "test_data_ep_ids"


def test_ids(con):
    """返回被标记为测试数据的 ep_id 集合

    con 是 sqlite3.Connection。
    若标记不存在，返回空集合（向后兼容）。
    """
    try:
        row = con.execute("SELECT val FROM meta WHERE key=?", (KEY,)).fetchone()
    except Exception:
        return set()
    if not row:
        return set()
    try:
        d = json.loads(row[0])
        return set(d.get("ep_ids") or [])
    except Exception:
        return set()


def is_test_data(con, ep_id):
    """判断单条记忆是否是测试数据"""
    return ep_id in test_ids(con)


def filtered(con, rows, id_index=0):
    """过滤掉测试数据行

    rows: 可迭代的行
    id_index: ep_id 在行里的位置（tuple）或键名（dict）
    """
    ids = test_ids(con)
    out = []
    for r in rows:
        if isinstance(r, dict):
            eid = r.get("ep_id")
        else:
            eid = r[id_index] if len(r) > id_index else None
        if eid in ids:
            continue
        out.append(r)
    return out


def real_count(con, include_invalid=False):
    """排除测试数据后的 episodes 数

    include_invalid=False: 只算有效的（invalid_at IS NULL）
    include_invalid=True:  算全部（含已失效但排除测试数据）
    """
    ids = test_ids(con)
    where = "" if include_invalid else "WHERE invalid_at IS NULL"
    rows = con.execute(f"SELECT ep_id FROM episodes {where}").fetchall()
    return sum(1 for (eid,) in rows if eid not in ids)


def breakdown(con):
    """返回统计明细，便于诊断"""
    ids = test_ids(con)
    total = con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    valid = con.execute(
        "SELECT COUNT(*) FROM episodes WHERE invalid_at IS NULL"
    ).fetchone()[0]
    invalid = total - valid
    marked_total = sum(
        1 for (eid,) in con.execute("SELECT ep_id FROM episodes")
        if eid in ids
    )
    marked_invalid = sum(
        1 for (eid,) in con.execute(
            "SELECT ep_id FROM episodes WHERE invalid_at IS NOT NULL"
        )
        if eid in ids
    )
    return {
        "total": total,
        "valid": valid,
        "invalid": invalid,
        "marked_total": marked_total,
        "marked_invalid": marked_invalid,
        "real_total": total - marked_total,
        "real_valid": valid - (marked_total - marked_invalid),
        "real_invalid": invalid - marked_invalid,
    }


if __name__ == "__main__":
    import sqlite3
    import sys

    here = os.path.dirname(os.path.abspath(__file__))
    db = os.path.join(here, "cosmos_mem", "dsh_mem.db")
    con = sqlite3.connect(db)
    b = breakdown(con)
    print("=" * 60)
    print("统计数据（含/排除测试数据）")
    print("=" * 60)
    for k, v in b.items():
        print(f"  {k:16s}: {v}")
    con.close()
