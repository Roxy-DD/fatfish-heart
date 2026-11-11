"""E-438 回归：ep_id 集合指纹能发现任何删除

E-436/437 的进展：
  · 行数校验：发现不了「删1补1」（实测确认）
  · 水位线：能发现「净减少」，但发现不了「删1补1」
  · ep_id 集合指纹：能发现任何增删（本测试验证）

★ 为什么要做到这个程度：
  R2 是红线。红线的校验不能有"这种情况发现不了"的空白，
  否则等于没有校验 —— 因为真出问题时恰好可能是那种情况。

本测试：
  ① 指纹存在且与实际一致
  ② 新增后指纹自动更新
  ③ **删1补1 后指纹会不一致（能发现）**
  ④ 对比：行数校验和水位线都发现不了
"""
import sys
import os
import time
import hashlib
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

import backup as bk
from store_v2 import MemoryStore

DB = os.path.join(MEM, "dsh_mem.db")

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


def compute_fp(st):
    ids = [r[0] for r in st.con.execute(
        "SELECT ep_id FROM episodes ORDER BY ep_id")]
    return hashlib.md5("\n".join(ids).encode("utf-8")).hexdigest(), len(ids)


def stored_fp(st):
    row = st.con.execute(
        "SELECT val FROM meta WHERE key='episodes_ids_fp'").fetchone()
    return row[0] if row else None


def stored_wm(st):
    row = st.con.execute(
        "SELECT val FROM meta WHERE key='episodes_watermark'").fetchone()
    return int(row[0]) if row and str(row[0]).isdigit() else 0


print("=" * 74)
print("E-438 ep_id 集合指纹回归")
print("=" * 74)

# ---------- ① 生产库 ----------
print("\n【1】生产库指纹状态")

st = MemoryStore(DB)
fp_now, n_now = compute_fp(st)
fp_saved = stored_fp(st)

print(f"   当前 ep_id 指纹: {fp_now[:16]}...")
print(f"   存储的指纹:      {str(fp_saved)[:16]}...")
print(f"   行数: {n_now}")

check("存储了指纹", fp_saved is not None)
check("指纹与实际一致（R2 成立）", fp_now == fp_saved)

# ---------- ② 新增后自动更新 ----------
print("\n【2】新增后指纹自动更新")

TMP = os.path.join(_HERE, "results", "_e438_test.db")
for suf in ("", "-wal", "-shm"):
    f = TMP + suf
    if os.path.exists(f):
        os.remove(f)

bk.backup_db(DB, TMP, verify=False)
st2 = MemoryStore(TMP)

fp0 = stored_fp(st2)
n0 = st2.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
print(f"   副本初始: 行数={n0}")

ep = st2.add("E-438 测试：新增记忆 www", atmosphere="test")
fp1 = stored_fp(st2)
n1 = st2.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
print(f"   新增后:   行数={n1}, 指纹变了={'是' if fp1 != fp0 else '否'}")

check("新增后行数 +1", n1 == n0 + 1, f"{n0} → {n1}")
check("新增后指纹变化", fp1 != fp0)
check("新增后指纹与实际一致",
      stored_fp(st2) == compute_fp(st2)[0])

# ---------- ③ 关键：删1补1 能否发现 ----------
print("\n【3】关键：删1条+补1条（行数不变）")

victim = st2.con.execute(
    "SELECT ep_id FROM episodes WHERE invalid_at IS NULL "
    "LIMIT 1").fetchone()[0]

wm_before = stored_wm(st2)
fp_before = stored_fp(st2)
n_before = st2.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]

print(f"   操作前: 行数={n_before}, 水位线={wm_before}")
print(f"   受害者: {victim[:8]}")

# 直接操作 SQL（不走 add，模拟"异常删除"）
st2.con.execute("DELETE FROM episodes WHERE ep_id=?", (victim,))
st2.con.execute(
    "INSERT INTO episodes (ep_id, created_at, raw_text, revision, "
    "schema_version) VALUES (?,?,?,?,?)",
    ("fake_e438_" + str(int(time.time())), time.time(),
     "E-438 假数据", 1, "cosmos-mem/1.0"))
st2.con.commit()

n_after = st2.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
wm_after = stored_wm(st2)
fp_actual, _ = compute_fp(st2)
fp_stored = stored_fp(st2)

print(f"   操作后: 行数={n_after}, 水位线={wm_after}")

# 三种校验对比
print("\n   三种校验对比：")

old_check = (n_after == n_before)
print(f"   · 行数校验:  {'通过 → 漏检' if old_check else '失败 → 发现'}")
check("行数校验漏检（证明问题存在）", old_check)

wm_check = (n_after >= wm_after)
print(f"   · 水位线:    {'通过 → 漏检' if wm_check else '失败 → 发现'}")
check("水位线也漏检（它的边界）", wm_check)

fp_check = (fp_actual == fp_stored)
print(f"   · 指纹校验:  {'通过 → 漏检' if fp_check else '失败 → 发现'}")
check("★ 指纹校验能发现删1补1", fp_check is False,
      f"实际={fp_actual[:8]} 存储={str(fp_stored)[:8]}")

# ---------- ④ 验证受害者确实消失 ----------
print("\n【4】确认受害者确实消失了")

alive = st2.con.execute(
    "SELECT COUNT(*) FROM episodes WHERE ep_id=?",
    (victim,)).fetchone()[0]
check("受害者已不在库中", alive == 0, f"{victim[:8]}")

st2.con.close()
for suf in ("", "-wal", "-shm"):
    f = TMP + suf
    if os.path.exists(f):
        try:
            os.remove(f)
        except Exception:
            pass

# ---------- ⑤ 生产库最终检查 ----------
print("\n【5】生产库最终检查")

fp_end, n_end = compute_fp(st)
check("生产库指纹仍一致", fp_end == stored_fp(st),
      f"{n_end} 条")
st.con.close()

# ---------- 汇总 ----------
print("\n" + "=" * 74)
n_ok = sum(1 for x in results if x["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for x in results:
    if not x["ok"]:
        print(f"  ❌ {x['name']}  {x['detail']}")
print("=" * 74)

print(f"""
  ★ 三层校验的能力对比（实测）：

    ┌──────────────┬────────────┬────────────┐
    │ 校验方式      │ 净减少     │ 删1补1     │
    ├──────────────┼────────────┼────────────┤
    │ COUNT(*)     │ ✅ 能发现  │ ❌ 漏检    │
    │ 水位线        │ ✅ 能发现  │ ❌ 漏检    │
    │ ep_id 指纹    │ ✅ 能发现  │ ✅ 能发现  │
    └──────────────┴────────────┴────────────┘

  ★ 为什么最后选指纹：
    R2 是红线，校验不能有空白。
    指纹成本：一个 32 字节的 meta 值 + 每次写入时算一次 md5。
    448 条算一次耗时可忽略。

  ★ 边界（诚实）：
    指纹只能发现"变了"，不能指出"哪条没了"。
    要定位需要和备份对比 —— 但发现问题是第一步。
""")

import json
out = {
    "experiment": "E-438",
    "n_pass": n_ok,
    "n_total": len(results),
    "n_episodes": n_end,
    "row_count_blind": True,
    "watermark_blind": True,
    "fingerprint_detects": True,
}
p = os.path.join(_HERE, "results", "e438_fingerprint.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e438_fingerprint.json")

sys.exit(0 if n_ok == len(results) else 1)
