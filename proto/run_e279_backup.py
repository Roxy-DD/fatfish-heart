"""E-279 查清：备份恢复为什么得到 0 条

E-278 结果：原 30 条 → 破坏后 15 条 → 恢复后 0 条

可能原因：
  a) 备份时 WAL 未刷盘，备份文件不含最新数据
  b) 恢复时旧连接还开着，读到的是缓存
  c) 恢复顺序错误（先复制再关连接）

这是数据安全问题，必须查清。
"""
import sys
import os
import time
import shutil
import sqlite3

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))

from store_v2 import MemoryStore

TEST_DB = os.path.join(_HERE, "work", "test_db", "e279.db")
os.makedirs(os.path.dirname(TEST_DB), exist_ok=True)
for suf in ("", "-wal", "-shm", ".backup"):
    p = TEST_DB + suf
    if os.path.exists(p):
        os.remove(p)

print("=" * 74)
print("E-279 备份恢复问题排查")
print("=" * 74)

# ---------- ① 复现 E-278 的做法 ----------
print("\n① 复现原做法")
st = MemoryStore(TEST_DB)
for i in range(20):
    st.add(f"备份测试第{i}条", resolve_conflict=False)

n1 = st.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
print(f"   写入后: {n1} 条")

# 检查 WAL 文件是否存在
print(f"\n   文件状态:")
for suf in ("", "-wal", "-shm"):
    p = TEST_DB + suf
    exists = os.path.exists(p)
    size = os.path.getsize(p) if exists else 0
    print(f"     {os.path.basename(p) or 'main'}: {'存在' if exists else '不存在'} ({size} bytes)")

# 直接复制（不关连接）
backup = TEST_DB + ".backup"
shutil.copy2(TEST_DB, backup)
print(f"\n   直接复制主库文件")

# 检查备份文件里的数据
con_b = sqlite3.connect(backup)
try:
    n_b = con_b.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    print(f"   备份文件里: {n_b} 条  {'✅ 完整' if n_b == n1 else '❌ 缺失'}")
except Exception as e:
    print(f"   备份文件读取失败: {e}")
    n_b = -1
con_b.close()

# ---------- ② 正确做法：先 checkpoint 再复制 ----------
print("\n② 正确做法：先 WAL checkpoint 再复制")

st.con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
st.con.commit()
print("   已执行 wal_checkpoint(TRUNCATE)")

backup2 = TEST_DB + ".backup2"
shutil.copy2(TEST_DB, backup2)

con_b2 = sqlite3.connect(backup2)
n_b2 = con_b2.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
con_b2.close()
print(f"   备份文件里: {n_b2} 条  {'✅ 完整' if n_b2 == n1 else '❌ 缺失'}")

# ---------- ③ 用 sqlite3 官方 backup API ----------
print("\n③ 官方做法：sqlite3 backup API")

backup3 = TEST_DB + ".backup3"
try:
    con_dst = sqlite3.connect(backup3)
    st.con.backup(con_dst)
    con_dst.close()
    con_b3 = sqlite3.connect(backup3)
    n_b3 = con_b3.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    con_b3.close()
    print(f"   备份文件里: {n_b3} 条  {'✅ 完整' if n_b3 == n1 else '❌ 缺失'}")
except Exception as e:
    print(f"   backup API 失败: {e}")
    n_b3 = -1

st.con.close()

# ---------- ④ 恢复测试 ----------
print("\n④ 三种备份的恢复测试")

con = sqlite3.connect(TEST_DB)
con.execute("DELETE FROM episodes WHERE rowid % 2 = 0")
con.commit()
n_dmg = con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
con.close()
print(f"   破坏后: {n_dmg} 条")

for name, bk, expected in [
    ("直接复制", backup, n1),
    ("checkpoint后复制", backup2, n1),
    ("backup API", backup3, n1),
]:
    if not os.path.exists(bk):
        print(f"   {name}: 文件不存在")
        continue
    # 恢复：先删 -wal/-shm 再复制
    for suf in ("-wal", "-shm"):
        p = TEST_DB + suf
        if os.path.exists(p):
            os.remove(p)
    shutil.copy2(bk, TEST_DB)
    c = sqlite3.connect(TEST_DB)
    n = c.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    c.close()
    ok = "✅" if n == expected else "❌"
    print(f"   {ok} {name}: {n} 条（期望 {expected}）")
    # 恢复到破坏状态以便下次测试
    c = sqlite3.connect(TEST_DB)
    c.execute("DELETE FROM episodes WHERE rowid % 2 = 0")
    c.commit()
    c.close()

# ---------- 结论 ----------
print("\n" + "=" * 74)
print("结论")
print("=" * 74)

print(f"""
  直接复制主库: {n_b} 条
  checkpoint 后复制: {n_b2} 条
  backup API: {n_b3} 条
  原始数据: {n1} 条
""")

if n_b < n1:
    print("""
  【根因】SQLite 的 WAL 模式：
    新写入的数据先落在 .db-wal 文件里，
    主库文件 .db 可能还是旧的。
    直接复制 .db 而不处理 -wal，会丢掉未 checkpoint 的数据。

  【正确做法】
    a) 复制前执行 PRAGMA wal_checkpoint(TRUNCATE)，或
    b) 用 sqlite3 的 backup API（最可靠），或
    c) 同时复制 .db + -wal + -shm 三个文件

  【影响】
    E-278 的测试用了错误做法（直接复制），
    所以"恢复后 0 条"是**测试方法错误**，不一定是系统缺陷。

    但这暴露了一个真实风险：
    如果将来做备份功能，用直接复制会丢数据。
""")

out = {
    "experiment": "E-279",
    "original": n1,
    "direct_copy": n_b,
    "checkpoint_copy": n_b2,
    "backup_api": n_b3,
    "root_cause": "WAL 模式未 checkpoint 就复制主库文件",
}
import json
p = os.path.join(_HERE, "results", "e279_backup.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e279_backup.json")
