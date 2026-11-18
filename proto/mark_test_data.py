"""标记测试数据（FISH-15 / 用户选 B：加 is_test_data 标记，统计时排除）

════════════════════════════════════════════════════════════
为什么这么做
════════════════════════════════════════════════════════════
E-533 发现：11-07 21:00-22:00 有 199 条记忆被批量标记失效，
内容是早期演示数据（密码8848、车牌号等），同期在跑性能测试。

用户选 B：加 is_test_data 标记，统计时排除。

════════════════════════════════════════════════════════════
为什么不改 episodes 表
════════════════════════════════════════════════════════════
1. R2 红线：数据只增不删，失效只打标记 —— 加列会改结构
2. R3 红线：索引可确定性重建 —— 加列会影响重建逻辑
3. meta 表是天然的元数据存放处，val 是 TEXT 无长度限制

做法：把 ep_id 列表存进 meta.test_data_ep_ids（JSON 数组）
这样：
· 数据完全没动（红线安全）
· 统计时读这个 key 排除即可
· 随时可以撤销（删掉 key）

用法：
    python mark_test_data.py apply    # 标记（幂等）
    python mark_test_data.py status   # 看标记状态
    python mark_test_data.py undo     # 撤销标记
"""
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta

DB = "cosmos_mem/dsh_mem.db"
KEY = "test_data_ep_ids"

# 判定条件：11-07 21:00-22:59 之间失效、且无 superseded_by 的
#
# ★ 时区坑：SQLite 的 'localtime' 显示 21 点，但 invalid_at 存的是 UTC epoch。
#   所以查询边界必须用 UTC+8 换算后的 epoch，不能直接用 UTC。
#   第一版用 timezone.utc 构造 → 候选数 0（差 8 小时）。
_TZ_CN = timezone(timedelta(hours=8))
START = datetime(2026, 11, 7, 21, 0, tzinfo=_TZ_CN).timestamp()
END = datetime(2026, 11, 7, 23, 0, tzinfo=_TZ_CN).timestamp()


def get_conn():
    return sqlite3.connect(DB)


def find_candidates(c):
    rows = c.execute(
        "SELECT ep_id, invalid_at FROM episodes "
        "WHERE invalid_at IS NOT NULL AND superseded_by IS NULL "
        "AND invalid_at >= ? AND invalid_at < ?",
        (START, END),
    ).fetchall()
    return [r[0] for r in rows]


def get_marked(c):
    row = c.execute("SELECT val FROM meta WHERE key=?", (KEY,)).fetchone()
    if not row:
        return None
    try:
        d = json.loads(row[0])
        return set(d.get("ep_ids") or [])
    except Exception:
        return None


def cmd_status():
    c = get_conn()
    marked = get_marked(c)
    candidates = set(find_candidates(c))

    print("=" * 66)
    print("测试数据标记状态")
    print("=" * 66)

    total = c.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    invalid = c.execute(
        "SELECT COUNT(*) FROM episodes WHERE invalid_at IS NOT NULL"
    ).fetchone()[0]

    print(f"\n  记忆总数: {total}")
    print(f"  已失效:   {invalid}")
    print(f"  11-07 21-23 点批量失效候选: {len(candidates)} 条")

    if marked is None:
        print(f"\n  ❌ 尚未标记（meta.{KEY} 不存在）")
    else:
        print(f"\n  ✅ 已标记 {len(marked)} 条")
        # 候选与标记的关系
        print(f"     候选∩标记: {len(candidates & marked)}")
        print(f"     候选未标记: {len(candidates - marked)}")
        print(f"     标记非候选: {len(marked - candidates)}")

        # 展示几条内容样本
        print("\n  标记样本（前 3 条）:")
        for eid in list(marked)[:3]:
            r = c.execute(
                "SELECT substr(raw_text,1,50) FROM episodes WHERE ep_id=?",
                (eid,),
            ).fetchone()
            if r:
                print(f"    {eid[:12]}: {r[0]}")

    c.close()


def cmd_apply():
    c = get_conn()
    candidates = find_candidates(c)
    existing = get_marked(c)
    if existing is not None:
        print(f"  已有标记 {len(existing)} 条，跳过（幂等）")
        cmd_status()
        c.close()
        return 0

    if not candidates:
        print("  ❌ 没找到候选，不标记")
        c.close()
        return 1

    payload = {
        "ep_ids": sorted(candidates),
        "marked_at": time.time(),
        "reason": "2026-11-07 21-23点批量失效，同期跑性能测试，内容为早期演示数据",
        "decision": "用户选 B（2026-11-18）",
        "count": len(candidates),
    }
    with c:
        c.execute(
            "INSERT OR REPLACE INTO meta(key, val) VALUES(?, ?)",
            (KEY, json.dumps(payload, ensure_ascii=False)),
        )
    print(f"  ✅ 已标记 {len(candidates)} 条（写入 meta.{KEY}）")
    print("  数据本身未改动，随时可撤销：python mark_test_data.py undo")
    c.close()
    return 0


def cmd_undo():
    c = get_conn()
    row = c.execute("SELECT COUNT(*) FROM meta WHERE key=?", (KEY,)).fetchone()
    if not row or row[0] == 0:
        print("  没有标记，无需撤销")
        c.close()
        return 0
    with c:
        c.execute("DELETE FROM meta WHERE key=?", (KEY,))
    print("  ✅ 已撤销标记（只删 meta 记录，记忆数据未动）")
    c.close()
    return 0


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action == "apply":
        return cmd_apply()
    if action == "undo":
        return cmd_undo()
    if action == "status":
        cmd_status()
        return 0
    print("用法: python mark_test_data.py [apply|status|undo]")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
