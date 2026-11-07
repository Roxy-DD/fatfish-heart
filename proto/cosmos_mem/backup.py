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


def verify_backup(db_path: str, backup_path: str,
                  tables: tuple = ("episodes", "vectors", "meta")) -> dict:
    """
    校验备份与源库的记录数是否一致。

    返回 {"ok": bool, "counts": {表: (源, 备份)}}
    """
    if not os.path.exists(backup_path):
        return {"ok": False, "error": "备份文件不存在"}

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

    return {"ok": all_ok, "counts": counts}


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
