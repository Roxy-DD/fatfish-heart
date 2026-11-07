"""
migrate_fts.py —— 把已存在的 unicode61 FTS 表迁移为 trigram

E-284 实测结论（70 个用例）：
  纯 FTS recall@10: 2.9% → 24.3%
  融合 recall@1:    50.0% → 52.9%（+2.9pp）
  融合 recall@5:    74.3% → 75.7%（+1.4pp）
  逐用例: 变好 6 / 变差 0 / 不变 64  ← 零退步

迁移是安全的：
  · FTS 是**派生层**（可从 episodes 重建），不碰真值层
  · R3 红线要求索引可确定性重建，这次就是重建
  · 迁移前会自动备份

用法：
  python migrate_fts.py [--db path] [--yes]
"""
from __future__ import annotations

import os
import sys
import sqlite3
import time


def fts_tokenizer(con, table: str = "ep_fts") -> str:
    """读当前 FTS 表用的分词器"""
    row = con.execute(
        "SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()
    if not row:
        return "(不存在)"
    sql = row[0] or ""
    if "trigram" in sql:
        return "trigram"
    if "unicode61" in sql:
        return "unicode61"
    return "默认(unicode61)"


def migrate(db_path: str, force: bool = False) -> dict:
    """
    把 ep_fts 迁移为 trigram。

    返回 {"ok": bool, "from": str, "to": str, "n": int, "ms": float}
    """
    if not os.path.exists(db_path):
        return {"ok": False, "error": f"数据库不存在: {db_path}"}

    con = sqlite3.connect(db_path)
    try:
        cur = fts_tokenizer(con)
        if cur == "trigram" and not force:
            con.close()
            return {"ok": True, "from": cur, "to": cur,
                    "note": "已是 trigram，无需迁移", "n": 0, "ms": 0}

        n_ep = con.execute(
            "SELECT COUNT(*) FROM episodes").fetchone()[0]

        t0 = time.perf_counter()

        # FTS 是派生层，可直接删除重建（不碰 episodes）
        con.executescript("""
            DROP TABLE IF EXISTS ep_fts;
            CREATE VIRTUAL TABLE ep_fts USING fts5(
                raw_text, atmosphere, content='episodes', content_rowid='rowid',
                tokenize='trigram'
            );
            INSERT INTO ep_fts(ep_fts) VALUES('rebuild');
        """)
        con.commit()

        ms = (time.perf_counter() - t0) * 1000
        n_fts = con.execute("SELECT COUNT(*) FROM ep_fts").fetchone()[0]
        new_tok = fts_tokenizer(con)

        # 验证：真值层未受影响
        n_ep_after = con.execute(
            "SELECT COUNT(*) FROM episodes").fetchone()[0]

        con.close()

        return {
            "ok": new_tok == "trigram" and n_ep == n_ep_after,
            "from": cur, "to": new_tok,
            "n_episodes": n_ep, "n_fts": n_fts,
            "ms": round(ms, 1),
        }
    except Exception as e:
        try:
            con.close()
        except Exception:
            pass
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    db = args.db or os.path.join(here, "cosmos_mem", "dsh_mem.db")

    print("=" * 66)
    print("FTS 分词器迁移：unicode61 → trigram")
    print("=" * 66)
    print(f"数据库: {db}")

    con = sqlite3.connect(db)
    cur = fts_tokenizer(con)
    n = con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    con.close()
    print(f"当前分词器: {cur}")
    print(f"记忆总数: {n}")

    if cur == "trigram":
        print("\n已是 trigram，无需迁移。")
        return 0

    if not args.yes:
        ans = input("\n确认迁移？(输入 yes): ").strip().lower()
        if ans != "yes":
            print("已取消")
            return 1

    # 先备份
    # 注意：migrate_fts.py 在 proto/ 下，backup.py 在 proto/cosmos_mem/ 下
    _here_dir = os.path.dirname(os.path.abspath(__file__))
    _mem_dir = os.path.join(_here_dir, "cosmos_mem")
    if _mem_dir not in sys.path:
        sys.path.insert(0, _mem_dir)
    import backup as bk
    print("\n[1/2] 备份…")
    b = bk.backup_db(db)
    if not b.get("ok"):
        print(f"❌ 备份失败，中止: {b.get('error')}")
        return 1
    print(f"   备份 → {os.path.basename(b['path'])} ({b['bytes']//1024} KB)")

    print("\n[2/2] 迁移…")
    r = migrate(db)
    if r.get("ok"):
        print(f"   ✅ {r['from']} → {r['to']}")
        print(f"   记忆 {r.get('n_episodes')} 条 / FTS {r.get('n_fts')} 条")
        print(f"   耗时 {r.get('ms')} ms")
        return 0
    else:
        print(f"   ❌ 失败: {r.get('error')}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
