"""run_e525_disaster_drill.py — 灾难恢复完整演练（E-525）

════════════════════════════════════════════════════════════
这个测试要回答的问题
════════════════════════════════════════════════════════════
异地副本的意义只有一个：**机器没了，记忆还能回来**。

但"能回来"有三个层次，必须都验证：
  ① 能下载回来      —— 文件传过去了
  ② 能打开          —— 不是损坏的文件
  ③ **能真正使用**  —— 能检索、能写入

多数测试只验证到 ②，那是不够的。
一个能打开但检不出东西的库，等于没恢复。

════════════════════════════════════════════════════════════
安全设计
════════════════════════════════════════════════════════════
全程在临时目录里，绝不碰生产库。
模拟"一台全新机器"：本地什么备份都没有，只有 GitHub 上那份。

════════════════════════════════════════════════════════════
代价说明
════════════════════════════════════════════════════════════
这个测试会真的从 GitHub 下载 8.6MB 并写入临时库，
所以比别的测试慢（约 20-30 秒）。但它验证的是最关键的场景，
值得这个代价。
"""
import os
import shutil
import sys
import tempfile
import warnings

warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "cosmos_mem"))

import offsite                        # noqa: E402
from store_v2 import MemoryStore      # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": str(detail)[:120]})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 72)
print("E-525 灾难恢复完整演练")
print("=" * 72)
print("\n场景：一台全新机器，本地什么都没有，只能从 GitHub 私有仓库恢复")

# ---- 隔离到临时目录，绝不碰生产库 ----
tmp = tempfile.mkdtemp(prefix="e525_")
original_db = offsite.DB_PATH
original_state = offsite.STATE_PATH

try:
    offsite.DB_PATH = os.path.join(tmp, "fresh.db")
    offsite.STATE_PATH = os.path.join(tmp, "state.json")

    # ---------- ① 起点：确认本地是空的 ----------
    print("\n【1】起点：本地没有任何备份")
    check("本地库不存在（模拟新机器）",
          not os.path.exists(offsite.DB_PATH))

    # ---------- ② 从远端恢复 ----------
    print("\n【2】从远端恢复到本地")
    # 网络/令牌可能偶发失败，重试一次（真实演练允许一次重试）
    rc = offsite.cmd_restore(None, force=False)
    if rc != 0:
        print("\n  （首次失败，重试一次 —— 网络偶发）")
        rc = offsite.cmd_restore(None, force=False)
    check("restore 返回 0", rc == 0, f"exit={rc}")
    check("本地库已创建", os.path.exists(offsite.DB_PATH))

    # ---------- ③ 能打开 + 数据条数 ----------
    print("\n【3】恢复后能打开")
    n_open = 0
    try:
        import sqlite3
        _c = sqlite3.connect(offsite.DB_PATH)
        _ic = _c.execute("PRAGMA integrity_check").fetchone()[0]
        n_open = _c.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        _c.close()
        check("完整性检查 ok", _ic == "ok", _ic[:60])
        check("episodes 非空", n_open > 0, f"{n_open} 条")
    except Exception as e:
        check("能打开", False, f"{type(e).__name__}: {e}")

    # ---------- ④ 能真正检索 ----------
    print("\n【4】恢复后能真正检索（不只是能打开）")
    search_ok = False
    hits = 0
    try:
        st = MemoryStore(offsite.DB_PATH)
        # 用记忆里几乎必然出现的词做检索
        for q in ("记忆", "系统", "项目"):
            hits = len(st.search(q, top=5))
            if hits > 0:
                search_ok = True
                break
        check("检索有命中", search_ok, f"命中 {hits} 条")

        # ---------- ⑤ 能真正写入 ----------
        print("\n【5】恢复后能真正写入（可继续使用）")
        ep = st.add("e525 disaster drill write probe", atmosphere="drill")
        n_after = st.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        st.con.close()
        check("写入成功", bool(ep))
        check("写入后条数增加", n_after == n_open + 1,
              f"{n_open} → {n_after}")
    except Exception as e:
        check("检索/写入", False, f"{type(e).__name__}: {e}")

    # ---------- ⑥ 关键：确认用的是临时路径（不是生产库） ----------
    #
    # 这条断言的正确时机是**恢复之前** —— 只要证明整个演练期间
    # offsite.DB_PATH 指向的是临时目录，就不会碰到生产库。
    # 第一版写在 finally 之后比较，那时已经还原了，必然不等。
    print("\n【6】确认操作的是临时路径（未触碰生产库）")
    check("演练期间 DB_PATH 指向临时目录",
          offsite.DB_PATH.startswith(tmp), offsite.DB_PATH[-40:])
    check("生产库路径不是这里", offsite.DB_PATH != original_db)

finally:
    # 无论如何都要恢复原路径并清理
    offsite.DB_PATH = original_db
    offsite.STATE_PATH = original_state
    shutil.rmtree(tmp, ignore_errors=True)

# ---------- 汇总 ----------
print("\n" + "=" * 72)
n_pass = sum(1 for x in results if x["ok"])
print(f"结果：{n_pass}/{len(results)} 通过")
for x in results:
    if not x["ok"]:
        print(f"  ❌ {x['name']}  {x['detail']}")
print("=" * 72)
print("""
★ 为什么这个测试比别的都重要：
  异地副本的全部意义就是「机器没了能回来」。
  但"能回来"有三个层次：
    ① 能下载 ② 能打开 ③ 能真正使用
  多数验证只做到 ②，那不够 ——
  一个能打开但检索不出东西的库，等于没恢复。

  这个测试做到 ③：恢复后真的检索、真的写入。
""")

sys.exit(0 if n_pass == len(results) else 1)
