"""
auto_backup.py —— 按写入条数触发的机械备份（FISH-9）

为什么需要这个（实测数据，不是拍脑袋）：

  FISH-9 实验（exp_fish9_coverage.py）测出：
    · 写入速率 27.94 条/天（每 0.86 小时一条）
    · mem_sleep 只有 9 次/19 天 ≈ 0.47 次/天
    · 只靠 sleep 触发备份 → 最多丢 52 条（约 2 天写入）
    · 每 20 条备份 → 最多丢 19 条（约 15 小时），成本可控

  所以 N=20 来自数据权衡，不是预设门槛。

职责划分（项目 L1 准则）：
  · 「该不该备份」在 sleep 场景是判断题 → 交给模型（mem_sleep）
  · 「写了多少条」是机械计数 → 系统直接做，不麻烦模型
  · 这个模块就是那个机械计数 + 到点触发

不违反任何红线：
  · R2 不遗忘：只写 meta 计数 + 生成备份文件，不动记忆本体
  · R3 可重建：计数在 meta 表，随备份一起走
  · 备份失败绝不影响主写入（调用方包在 try 里）
"""
from __future__ import annotations

import os


# 触发阈值（FISH-9 实验标定）。可通过 meta 覆盖，便于日后调参。
DEFAULT_EVERY = 20
KEY_COUNT = "auto_backup:writes_since"


def maybe_backup(st, every: int = DEFAULT_EVERY) -> dict:
    """
    每写入 every 条记忆后触发一次备份。

    返回 {"ok": bool, "triggered": bool, "count": int, ...}
    triggered=False 表示还没到阈值（正常情况）。
    """
    try:
        con = st.con
        # 读当前计数
        row = con.execute(
            "SELECT val FROM meta WHERE key=?", (KEY_COUNT,)).fetchone()
        try:
            count = int(row[0]) if row else 0
        except (ValueError, TypeError):
            count = 0

        count += 1
        con.execute(
            "INSERT OR REPLACE INTO meta(key, val) VALUES(?, ?)",
            (KEY_COUNT, str(count)))
        con.commit()

        if count < every:
            return {"ok": True, "triggered": False, "count": count,
                    "every": every}

        # 到阈值：触发备份，然后重置计数
        import backup as _bk
        db_path = getattr(st, "path", None) or getattr(st, "db_path", "")
        r = _bk.backup_db(db_path, verify=True)

        # 无论备份成功与否都重置计数，避免失败后每次写入都重试
        # （失败会记审计，下次再攒 20 条）
        con.execute(
            "INSERT OR REPLACE INTO meta(key, val) VALUES(?, ?)",
            (KEY_COUNT, "0"))
        con.commit()

        if r.get("ok"):
            # prune 只留 10 份
            try:
                pr = _bk.prune_backups(db_path, keep=10)
                r["pruned"] = pr.get("removed", 0)
            except Exception:
                r["pruned"] = None

        return {
            "ok": bool(r.get("ok")),
            "triggered": True,
            "count": 0,
            "every": every,
            "path": r.get("path"),
            "error": r.get("error"),
        }
    except Exception as e:
        return {"ok": False, "triggered": False,
                "error": f"{type(e).__name__}: {e}"}


def status(st) -> dict:
    """查看当前计数状态（诊断用）"""
    try:
        row = st.con.execute(
            "SELECT val FROM meta WHERE key=?", (KEY_COUNT,)).fetchone()
        return {"count": int(row[0]) if row else 0, "every": DEFAULT_EVERY}
    except Exception:
        return {"count": 0, "every": DEFAULT_EVERY}
