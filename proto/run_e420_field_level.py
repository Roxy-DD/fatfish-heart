"""E-420 R1 红线的硬验证：全表逐字段比对（不只是行数）

E-419 修了「校验哪些表」，但校验方式是**只比行数**。
行数相同不等于内容相同 —— 如果恢复后某一列被改了，
行数校验发现不了。

本测试做真正的逐字段比对：
  ① 对每张表算内容指纹（所有行、所有列排序后 hash）
  ② 备份 → 删除源库 → 恢复 → 再算指纹
  ③ 指纹必须完全一致

这是 R1「无损迁移 100%」的直接检验。
"""
import sys
import os
import hashlib
import sqlite3
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

import backup as bk

SRC = os.path.join(_HERE, "results", "_e420_src.db")
BAK = os.path.join(_HERE, "results", "_e420_bak.db")
PROD = os.path.join(MEM, "dsh_mem.db")

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


def table_fingerprint(db_path, table):
    """
    整表内容指纹：所有行、所有列，按稳定顺序序列化后 hash。

    ★ 关键：排序后再 hash，避免行序不同导致误判。
    """
    try:
        con = sqlite3.connect(db_path)
        cols = [c[1] for c in con.execute(
            f"PRAGMA table_info({table})").fetchall()]
        if not cols:
            con.close()
            return None, 0

        col_list = ", ".join(f'"{c}"' for c in cols)
        rows = con.execute(
            f"SELECT {col_list} FROM {table}").fetchall()
        con.close()

        # 每行转成可比较的字符串，排序后拼接
        strs = []
        for r in rows:
            strs.append("|".join("" if v is None else str(v)
                                 for v in r))
        strs.sort()
        payload = f"{table}:{len(cols)}:" + "\n".join(strs)
        return hashlib.md5(payload.encode("utf-8")).hexdigest(), len(rows)
    except Exception as e:
        return f"ERR:{type(e).__name__}", 0


print("=" * 74)
print("E-420 R1 硬验证：全表逐字段比对")
print("=" * 74)

# ---------- 准备：用真实库的副本 ----------
print("\n① 准备源库（生产库的副本）")

for p in (SRC, BAK):
    for suf in ("", "-wal", "-shm"):
        f = p + suf
        if os.path.exists(f):
            os.remove(f)

# 用 backup API 复制（避免 WAL 问题）
r0 = bk.backup_db(PROD, SRC, verify=False)
check("源库副本创建成功", r0.get("ok") is True, str(r0.get("error") or ""))

tables = bk.all_user_tables(SRC)
print(f"   表: {len(tables)} 张")

# ---------- ② 记录恢复前指纹 ----------
print("\n② 记录恢复前指纹")

before = {}
for t in tables:
    h, n = table_fingerprint(SRC, t)
    before[t] = (h, n)
    print(f"   {t:<22} {n:>5} 行  {str(h)[:12]}")

# ---------- ③ 备份 ----------
print("\n③ 备份")

rb = bk.backup_db(SRC, BAK, verify=True)
check("备份成功", rb.get("ok") is True, str(rb.get("error") or ""))

# 备份后指纹应与源一致
mismatch_backup = []
for t in tables:
    h1, n1 = before[t]
    h2, n2 = table_fingerprint(BAK, t)
    if h1 != h2 or n1 != n2:
        mismatch_backup.append((t, n1, n2))
check("备份内容与源一致（逐字段）",
      len(mismatch_backup) == 0,
      f"{len(mismatch_backup)} 张不一致" if mismatch_backup else "10/10 一致")

# ---------- ④ 删源库 → 恢复 ----------
print("\n④ 删除源库 → 从备份恢复")

for suf in ("", "-wal", "-shm"):
    f = SRC + suf
    if os.path.exists(f):
        os.remove(f)
check("源库已删除", not os.path.exists(SRC))

rr = bk.restore_db(BAK, SRC)
check("恢复成功", rr.get("ok") is True, str(rr.get("error") or ""))

# ---------- ⑤ 恢复后指纹比对 ----------
print("\n⑤ 恢复后逐字段比对")

all_same = True
diff_detail = []
for t in tables:
    h1, n1 = before[t]
    h2, n2 = table_fingerprint(SRC, t)
    same = (h1 == h2 and n1 == n2)
    if not same:
        all_same = False
        diff_detail.append(f"{t}: {n1}行/{str(h1)[:8]} → {n2}行/{str(h2)[:8]}")
    mark = "✅" if same else "❌"
    print(f"   {mark} {t:<22} {n1:>5} → {n2:>5}  "
          f"{str(h1)[:8]} → {str(h2)[:8]}")

check("所有表内容完全一致（逐字段指纹）", all_same,
      "；".join(diff_detail) if diff_detail else f"{len(tables)}/{len(tables)}")

# ---------- ⑥ 检索仍可用 ----------
print("\n⑥ 恢复后检索仍可用")

try:
    from store_v2 import MemoryStore
    st2 = MemoryStore(SRC)
    r = st2.search_scoped("测试恢复后的检索", top=3)
    items = r[0] if isinstance(r, tuple) else r
    check("恢复后能检索", len(items) > 0, f"{len(items)} 条")
    st2.con.close()
except Exception as e:
    check("恢复后能检索", False, f"{type(e).__name__}: {e}")

# ---------- 清理 ----------
for p in (SRC, BAK):
    for suf in ("", "-wal", "-shm"):
        f = p + suf
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass

# ---------- 汇总 ----------
print("\n" + "=" * 74)
n_ok = sum(1 for x in results if x["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for x in results:
    if not x["ok"]:
        print(f"  ❌ {x['name']}  {x['detail']}")
print("=" * 74)

total_rows = sum(n for _, n in before.values())
print(f"""
  ★ 这次验证的是**内容**，不是行数：

    E-419 的校验只比行数（COUNT(*)）。
    行数相同不代表内容相同 —— 某列被改也发现不了。

    本测试对每张表算「所有行、所有列排序后 hash」，
    备份前后、恢复前后都必须完全一致。

    覆盖：{len(tables)} 张表，{total_rows} 行

  ★ 这是 R1「无损迁移 100%」的直接证据：
    删掉源库 → 从备份恢复 → 逐字段指纹一致 → 检索可用。
""")

import json
out = {
    "experiment": "E-420",
    "n_pass": n_ok,
    "n_total": len(results),
    "n_tables": len(tables),
    "n_rows": total_rows,
    "fingerprints_match": all_same,
}
p = os.path.join(_HERE, "results", "e420_field_level.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e420_field_level.json")

sys.exit(0 if n_ok == len(results) else 1)
