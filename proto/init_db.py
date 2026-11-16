#!/usr/bin/env python
"""初始化记忆库 —— 外部贡献者跑实验前先执行一次

为什么需要它：
  255 个实验脚本依赖 dsh_mem.db（读 audit_log / episodes 等表）。
  但仓库里不含数据库（那是私人数据，不能提交）。
  脚本直接 sqlite3.connect()，遇到空库会报：
      sqlite3.OperationalError: no such table: audit_log
  ——外人看不懂这句话，也不知道要建什么表。

本脚本做的事：
  1. 用 MemoryStore 建库（它内部有完整的 CREATE TABLE IF NOT EXISTS）
  2. 灌入 12 条种子记忆（L0 红线 / L1 准则 / L2 基石）
  3. 验证表结构和数据量

这样外部贡献者 clone 后跑一次就能开始实验。

用法：
  python proto/init_db.py              # 建库到默认位置
  python proto/init_db.py --path X.db  # 指定位置（测试用）
  python proto/init_db.py --force      # 已有库时强制重建（会备份）
"""
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(HERE, "cosmos_mem")
DEFAULT_DB = os.path.join(MEM, "dsh_mem.db")

sys.path.insert(0, MEM)


def main():
    args = sys.argv[1:]
    db_path = DEFAULT_DB
    force = False

    if "--path" in args:
        i = args.index("--path")
        if i + 1 < len(args):
            db_path = os.path.abspath(args[i + 1])
    if "--force" in args:
        force = True

    print("=" * 66)
    print("初始化记忆库")
    print("=" * 66)
    print(f"目标: {db_path}")

    # ---------- 已有库处理 ----------
    if os.path.exists(db_path) and os.path.getsize(db_path) > 0:
        size_kb = os.path.getsize(db_path) // 1024
        if not force:
            print(f"\n⚠ 库已存在（{size_kb} KB）")
            print("  如果这是你的真实记忆库，**不要**重建。")
            print("  强制重建（会先备份）: python proto/init_db.py --force")
            return 0
        bak = f"{db_path}.before-init-{int(time.time())}"
        shutil.copy2(db_path, bak)
        print(f"\n  已备份到: {os.path.basename(bak)}")

    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    # ---------- 建库 ----------
    print("\n① 建表")
    try:
        os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
        from store_v2 import MemoryStore
        st = MemoryStore(db_path)
    except Exception as e:
        print(f"   ❌ 失败: {type(e).__name__}: {e}")
        print("   提示：先跑 python proto/doctor.py 检查依赖")
        return 1

    tables = [r[0] for r in st.con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    print(f"   ✅ 建成 {len(tables)} 张表")
    for t in tables:
        print(f"      · {t}")

    # ---------- 灌种子 ----------
    print("\n② 灌入种子记忆")

    seed_file = os.path.join(MEM, "seed_memory.json")
    seeded = 0
    if os.path.exists(seed_file):
        try:
            import warnings
            warnings.filterwarnings("ignore")
            import seed as _seed
            r = _seed.load_seeds(st)
            if isinstance(r, dict):
                seeded = (r.get("loaded") or r.get("inserted")
                          or r.get("added") or 0)
            else:
                seeded = int(r or 0)
            print(f"   ✅ 种子: {seeded} 条")
            if isinstance(r, dict) and r.get("skipped"):
                print(f"      （跳过已存在 {r['skipped']} 条）")
        except Exception as e:
            print(f"   ⚠ 种子导入失败（不影响建库）: {e}")
    else:
        print("   ⚠ 找不到 seed_memory.json，跳过")

    # ---------- 验证 ----------
    print("\n③ 验证")

    n_ep = st.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    n_meta = st.con.execute("SELECT COUNT(*) FROM meta").fetchone()[0]
    print(f"   episodes: {n_ep} 条")
    print(f"   meta:     {n_meta} 条")

    # 真实检索一次
    try:
        import warnings
        warnings.filterwarnings("ignore")
        r = st.search("红线", top=3)
        print(f"   检索 '红线': {len(r)} 条")
        ok_search = len(r) > 0
    except Exception as e:
        print(f"   检索失败: {type(e).__name__}: {str(e)[:60]}")
        ok_search = False

    st.con.close()

    print("\n" + "=" * 66)
    if n_ep > 0 and ok_search:
        print("✅ 初始化完成，可以跑实验了")
        print("\n下一步:")
        print("  cd proto")
        print("  python iterate.py check <编号>   # 跑全量回归")
        print("  python run_e377_criteria.py      # 跑单个实验")
        code = 0
    elif n_ep > 0:
        print("⚠ 建库成功但检索异常，请跑 doctor.py 检查模型")
        code = 0
    else:
        print("❌ 建库成功但没有数据，种子导入可能失败")
        code = 1
    print("=" * 66)
    return code


if __name__ == "__main__":
    sys.exit(main())
