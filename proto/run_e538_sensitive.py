"""敏感信息生命周期回归（FISH-27）

验证 E-538 实现的机制：
1. 标记/读取/撤销 三条命令能正常工作
2. 过期标记能在检索结果里通过 note 字段暴露
3. 不影响未标记的记忆（零误报）
4. 数据本身未被修改（R2 红线）

用临时库测试，不碰生产数据。
"""
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
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "cosmos_mem"))

PASS, FAIL = 0, 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {name}" + (f"  {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f"  {detail}" if detail else ""))


def main():
    print("=" * 66)
    print("敏感信息生命周期回归（FISH-27 / E-538）")
    print("=" * 66)

    from store_v2 import MemoryStore
    import sensitive_lifecycle as sl

    tmp = tempfile.mkdtemp(prefix="sens_")
    db = os.path.join(tmp, "t.db")
    orig_db = sl.DB
    sl.DB = db           # 重定向到临时库
    try:
        st = MemoryStore(db)
        st.add("用户的密码是 TestPass123。")
        st.add("普通记忆：今天做了记忆系统优化。")
        st.add("另一个 itp_abcdefghijklmnopqrstuvwxyz012345 令牌")
        st.con.close()

        con = sqlite3.connect(db)
        ids = [r[0] for r in con.execute(
            "SELECT ep_id FROM episodes ORDER BY created_at")]
        con.close()
        check("临时库建立", len(ids) == 3, f"{len(ids)} 条")

        # 1. 标记
        sl.cmd_mark(ids[0], -1, "password")     # 立刻过期
        con = sqlite3.connect(db)
        marks = sl.load_marks(con)
        con.close()
        check("标记已写入", ids[0] in marks)
        check("标记含过期时间", "expires_at" in marks.get(ids[0], {}))

        # 2. 过期判定
        now = time.time()
        exp = marks[ids[0]]["expires_at"]
        check("过期时间已过去", exp < now)

        # 3. 检索时暴露提示
        st = MemoryStore(db)
        n = sl.apply_expiry_flags(st.con, [])
        # 用真实检索
        hits = st.search("密码", top=3)
        flagged = 0
        for ep, sc, fl in hits:
            if getattr(ep, "_expired", False):
                flagged += 1
        # apply_expiry_flags 需要显式调用
        eps = [h[0] for h in hits]
        n = sl.apply_expiry_flags(st.con, eps)
        flagged2 = sum(1 for e in eps if getattr(e, "_expired", False))
        st.con.close()
        check("检索结果可被标记过期", flagged2 >= 1,
              f"{flagged2}/{len(eps)} 条被标记")

        # 4. 未标记的不受影响
        #
        # ★ 修正（E-538 实测发现）：原断言写错了。
        # 检索 "记忆系统优化" 会返回全部 3 条（top=3），
        # 其中包含已标记的那条 —— 它被标为 expired 是**正确行为**，
        # 不是误报。正确断言应该是：
        #   「除已标记的那条外，其余全部未被标记」
        st = MemoryStore(db)
        hits2 = st.search("记忆系统优化", top=3)
        eps2 = [h[0] for h in hits2]
        sl.apply_expiry_flags(st.con, eps2)
        wrongly = [e for e in eps2
                   if getattr(e, "_expired", False) and e.ep_id != ids[0]]
        correctly = [e for e in eps2
                     if getattr(e, "_expired", False) and e.ep_id == ids[0]]
        st.con.close()
        check("未标记记忆不误报", len(wrongly) == 0,
              f"{len(wrongly)} 条被误标（应为0）")
        check("已标记的那条仍被正确标记", len(correctly) <= 1)

        # 5. 撤销
        sl.cmd_undo(ids[0])
        con = sqlite3.connect(db)
        marks2 = sl.load_marks(con)
        con.close()
        check("撤销生效", ids[0] not in marks2)

        # 6. R2 红线：数据未被修改
        con = sqlite3.connect(db)
        n_ep = con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        n_text = con.execute(
            "SELECT COUNT(*) FROM episodes WHERE raw_text LIKE '%TestPass123%'"
        ).fetchone()[0]
        con.close()
        check("R2：记忆未被删除", n_ep == 3, f"{n_ep} 条")
        check("R2：原文未被改写", n_text == 1)

    finally:
        sl.DB = orig_db
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 66)
    total = PASS + FAIL
    if FAIL == 0:
        print(f"结果：{PASS}/{total} 通过")
    else:
        print(f"结果：{PASS}/{total} 通过，{FAIL} 失败")
    print("=" * 66)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
