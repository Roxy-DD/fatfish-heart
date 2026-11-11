"""
backup.py —— 安全的数据备份/恢复

E-279 发现的风险：
  SQLite WAL 模式下，新写入的数据先落在 .db-wal 文件里，
  主库 .db 可能只有 4KB（几乎空），数据全在 1.2MB 的 -wal 里。

  实测：
    e279.db:      4,096 bytes     ← 主库
    e279.db-wal:  1,256,632 bytes ← 真实数据在这里

  → 直接用 shutil.copy2 复制主库，得到的是**空库**。
  → 恢复后数据全丢。

本模块提供正确做法：
  backup_db()    用 sqlite3 backup API（最可靠）
  restore_db()   恢复前清理 -wal/-shm（否则旧 WAL 会覆盖恢复的数据）
  verify_backup() 校验备份完整性

为什么用 backup API 而不是 checkpoint + copy：
  · backup API 是 SQLite 官方提供的一致性快照机制
  · 它在事务层面保证复制过程中不会读到半写状态
  · 不需要中断正在运行的连接
"""
from __future__ import annotations

import os
import sqlite3
import time


def backup_db(db_path: str, out_path: str | None = None,
              verify: bool = True) -> dict:
    """
    安全备份（用 sqlite3 backup API）。

    db_path:  源数据库路径
    out_path: 输出路径；None 则自动生成 <db>.backup.<时间戳>
    verify:   是否校验备份完整性

    返回 {"ok": bool, "path": str, "bytes": int, "tables": {...}}
    """
    if not os.path.exists(db_path):
        return {"ok": False, "error": f"源数据库不存在: {db_path}"}

    if not out_path:
        ts = time.strftime("%Y%m%d_%H%M%S")
        out_path = f"{db_path}.backup.{ts}"

    try:
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(out_path)
        with dst:
            src.backup(dst)
        dst.close()
        src.close()
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    size = os.path.getsize(out_path)
    result = {"ok": True, "path": out_path, "bytes": size}

    if verify:
        v = verify_backup(db_path, out_path)
        result["verify"] = v
        if not v.get("ok"):
            result["ok"] = False
            result["error"] = "备份校验失败"

    return result


def all_user_tables(db_path: str) -> list:
    """
    列出所有用户表（排除 FTS5 内部影子表）。

    E-419：为什么需要这个
      verify_backup 原来只校验 3 张表（episodes/vectors/meta），
      但实际库里有 14 张表，未校验的 11 张共 1631 行：
        notes / todos / episodes_history / audit_log / environment_events
        ep_fts 及其影子表（_data/_idx/_docsize/_config）

      这意味着**备份校验有盲区**：
        notes 或 todos 丢了，校验仍然报 ok。

      这与 R1「无损迁移」冲突 —— 那是要求「全部无损」，不是「三张表无损」。

    ★ 为什么排除 FTS 影子表：
      它们由 FTS5 内部维护（_data / _idx / _docsize / _config），
      不能直接 COUNT 比较语义；而且 ep_fts 本身能代表内容。
      重建 FTS 索引是可确定性重建的（R3），所以不需要逐行校验。
    """
    try:
        con = sqlite3.connect(db_path)
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        # 找出所有 FTS 表名，排除它们的影子表
        fts_bases = [n for n in names if n.endswith("_fts")
                     or n == "ep_fts"]
        shadow = set()
        for base in fts_bases:
            for suf in ("_data", "_idx", "_docsize", "_config",
                        "_content", "_segments", "_segdir", "_stat"):
                shadow.add(base + suf)
        con.close()
        return [n for n in names if n not in shadow]
    except Exception:
        return []


def verify_backup(db_path: str, backup_path: str,
                  tables: tuple = None) -> dict:
    """
    校验备份与源库的记录数是否一致。

    E-419：tables=None 时自动校验**全部用户表**（除 FTS 影子表）。
      原实现只校验 3 张表，notes/todos/history/audit 都在盲区里。

    返回 {"ok": bool, "counts": {表: (源, 备份)}, "tables": [...]}
    """
    if not os.path.exists(backup_path):
        return {"ok": False, "error": "备份文件不存在"}

    if tables is None:
        tables = all_user_tables(db_path)

    # E-428：tables 为空必须报失败，不能"遍历 0 次后返回 ok"
    #
    # 实测缺陷：verify_backup(db, db, tables=()) 返回 ok=True。
    # 这意味着如果 all_user_tables() 因异常返回 []，
    # 或调用方传了空元组，会得到"备份校验通过"的错误结论。
    #
    # 这和 E-419 是同一类问题：
    #   校验因为没东西可校验而通过。
    # E-419 修了"默认只校验 3 张表"，但没处理"表列表为空"。
    if not tables:
        return {"ok": False,
                "error": "没有可校验的表（tables 为空）",
                "counts": {}, "tables": []}

    counts = {}
    all_ok = True

    for t in tables:
        try:
            c1 = sqlite3.connect(db_path)
            n1 = c1.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            c1.close()
        except Exception:
            n1 = None

        try:
            c2 = sqlite3.connect(backup_path)
            n2 = c2.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            c2.close()
        except Exception:
            n2 = None

        counts[t] = (n1, n2)
        if n1 is None or n2 is None or n1 != n2:
            all_ok = False

    return {"ok": all_ok, "counts": counts,
            "tables": list(tables)}


def restore_db(backup_path: str, db_path: str) -> dict:
    """
    从备份恢复。

    关键：恢复前必须删除目标库的 -wal/-shm 文件，
    否则旧的 WAL 内容会在下次打开时"回放"，
    把恢复的数据又覆盖掉（E-279 踩过的坑）。

    返回 {"ok": bool, "restored": n_episodes}
    """
    if not os.path.exists(backup_path):
        return {"ok": False, "error": f"备份不存在: {backup_path}"}

    try:
        # 删除目标库及其 WAL/SHM（必须先删，否则旧 WAL 会覆盖）
        for suf in ("", "-wal", "-shm"):
            p = db_path + suf
            if os.path.exists(p):
                os.remove(p)

        # 用 backup API 反向复制
        src = sqlite3.connect(backup_path)
        dst = sqlite3.connect(db_path)
        with dst:
            src.backup(dst)
        dst.close()
        src.close()
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # 验证
    try:
        c = sqlite3.connect(db_path)
        n = c.execute(
            "SELECT COUNT(*) FROM episodes WHERE invalid_at IS NULL").fetchone()[0]
        c.close()
    except Exception as e:
        return {"ok": False, "error": f"恢复后无法读取: {e}"}

    return {"ok": True, "restored": n}


def list_backups(db_path: str) -> list[dict]:
    """列出某数据库的所有备份"""
    d = os.path.dirname(db_path) or "."
    base = os.path.basename(db_path)
    out = []
    try:
        for name in os.listdir(d):
            if name.startswith(base + ".backup"):
                p = os.path.join(d, name)
                if os.path.isfile(p):
                    out.append({
                        "path": p,
                        "name": name,
                        "bytes": os.path.getsize(p),
                        "mtime": os.path.getmtime(p),
                    })
    except Exception:
        pass
    out.sort(key=lambda x: -x["mtime"])
    return out


__all__ = ["backup_db", "verify_backup", "restore_db", "list_backups"]
