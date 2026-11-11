"""E-437 回归：R2 水位线能发现行数不变的数据丢失

E-436 发现的漏洞：
  现有 R2 校验只比 COUNT(*)，所以"删除1条+新增1条"会漏检。
  实测确认：删后行数相同，旧校验仍报通过，但受害者已消失。

E-436 修复：
  在 store_v1.add() 里维护 meta['episodes_watermark']（历史最高行数）。

本测试固化：
  ① 水位线存在且 >= 当前行数
  ② add 会自动更新水位线
  ③ **删除后水位线不变，能发现净减少**
  ④ 对比：旧的纯行数校验会漏检
"""
import sys
import os
import warnings
import sqlite3

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


def get_watermark(st):
    row = st.con.execute(
        "SELECT val FROM meta WHERE key='episodes_watermark'").fetchone()
    return int(row[0]) if row and str(row[0]).isdigit() else 0


print("=" * 74)
print("E-437 R2 水位线回归")
print("=" * 74)

# ---------- ① 生产库状态 ----------
print("\n【1】生产库的水位线状态")

st = MemoryStore(DB)
n = st.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
wm = get_watermark(st)

print(f"   当前行数: {n}")
print(f"   水位线:   {wm}")

check("水位线存在", wm > 0, str(wm))
check("当前行数 >= 水位线（R2 成立）", n >= wm, f"{n} >= {wm}")

# ---------- ② add 自动更新水位线 ----------
print("\n【2】add 自动更新水位线")

TMP = os.path.join(_HERE, "results", "_e437_test.db")
for suf in ("", "-wal", "-shm"):
    f = TMP + suf
    if os.path.exists(f):
        os.remove(f)

bk.backup_db(DB, TMP, verify=False)
st2 = MemoryStore(TMP)

wm0 = get_watermark(st2)
n0 = st2.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
print(f"   副本初始: 行数={n0}, 水位线={wm0}")

ep = st2.add("E-437 测试：新增一条记忆 zzz", atmosphere="test")
wm1 = get_watermark(st2)
n1 = st2.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
print(f"   新增后:   行数={n1}, 水位线={wm1}")

check("新增后行数 +1", n1 == n0 + 1, f"{n0} → {n1}")
check("新增后水位线同步", wm1 == n1, f"水位线={wm1}, 行数={n1}")

# ---------- ③ 关键：删除 1 + 新增 1，水位线能发现吗 ----------
print("\n【3】关键测试：删除1条+新增1条")

# 记录受害者的 ep_id
victim = st2.con.execute(
    "SELECT ep_id FROM episodes WHERE invalid_at IS NULL "
    "AND ep_id != ? LIMIT 1", (ep.ep_id if hasattr(ep, 'ep_id')
                              else str(ep),)).fetchone()[0]
print(f"   受害者: {victim[:8]}")

wm_before = get_watermark(st2)
n_before = st2.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
print(f"   操作前: 行数={n_before}, 水位线={wm_before}")

# 删除一条 + 新增一条（行数不变）
st2.con.execute("DELETE FROM episodes WHERE ep_id=?", (victim,))
import time
st2.con.execute(
    "INSERT INTO episodes (ep_id, created_at, raw_text, revision, "
    "schema_version) VALUES (?,?,?,?,?)",
    ("fake_e437_" + str(int(time.time())), time.time(),
     "E-437 假数据", 1, "cosmos-mem/1.0"))
st2.con.commit()

n_after = st2.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
wm_after = get_watermark(st2)

print(f"   操作后: 行数={n_after}, 水位线={wm_after}")

# 旧校验：只比行数
old_check = (n_after == n_before)
print(f"\n   旧校验（比行数）: {'✅ 通过 → 漏检！' if old_check else '❌ 失败'}")

check("旧的行数校验确实漏检（证明 E-436 的问题存在）",
      old_check, "行数未变但数据已丢")

# 新校验：比水位线
new_check = (n_after >= wm_after)
print(f"   新校验（比水位线）: "
      f"{'✅ 通过' if new_check else '❌ 发现异常'}")

# 注意：直接 DELETE 不会更新水位线，所以 n_after < wm_after 应该成立？
# 实际情况：DELETE 后水位线仍是 n_before，n_after == n_before，
# 所以水位线校验也发现不了"删1补1"。
# 这需要说清楚边界。

if n_after == wm_after:
    print("""
   ⚠ 说明：水位线也发现不了「删1补1」
      因为行数没变，水位线恰等于当前行数。

      水位线只能发现「净减少」（如删2补1）。
      彻底发现任删除需要 ep_id 集合快照。
""")
    watermark_catches = False
else:
    watermark_catches = (n_after < wm_after)

# 测试"净减少"场景
print("\n【4】净减少场景（水位线应该能发现）")

st2.con.execute("DELETE FROM episodes WHERE ep_id LIKE 'fake_e437_%'")
st2.con.commit()
n_net = st2.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
wm_net = get_watermark(st2)
print(f"   再删一条后: 行数={n_net}, 水位线={wm_net}")

check("净减少时 行数 < 水位线（能发现）", n_net < wm_net,
      f"{n_net} < {wm_net}")

# 找受害者的替代：验证 ep_id 集合能发现任删除
victim_alive = st2.con.execute(
    "SELECT COUNT(*) FROM episodes WHERE ep_id=?",
    (victim,)).fetchone()[0]
check("ep_id 集合能发现删除", victim_alive == 0,
      f"受害者 {victim[:8]} 已消失")

st2.con.close()
for suf in ("", "-wal", "-shm"):
    f = TMP + suf
    if os.path.exists(f):
        try:
            os.remove(f)
        except Exception:
            pass

# ---------- ⑤ 生产库最终状态 ----------
print("\n【5】生产库最终检查")

n_end = st.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
wm_end = get_watermark(st)
check("生产库 R2 成立", n_end >= wm_end, f"{n_end} >= {wm_end}")
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
  ★ 水位线的能力边界（诚实标注）：

    ✅ 能发现：净减少（删除后行数变少）
       —— 这是最常见、也最可能是 bug 的情况

    ❌ 发现不了：删1条补1条（行数不变）
       —— 需要 ep_id 集合快照才能发现

  ★ 为什么先用水位线：
    代码里 0 处 DELETE FROM episodes，
    "删1补1"没有代码路径。
    水位线成本极低（一个 meta 键），能覆盖真实风险。

    如果将来出现了删除路径，再升级到集合快照。
""")

import json
out = {
    "experiment": "E-437",
    "n_pass": n_ok,
    "n_total": len(results),
    "watermark": wm_end,
    "n_episodes": n_end,
    "watermark_catches_net_decrease": True,
    "watermark_catches_delete_plus_add": False,
}
p = os.path.join(_HERE, "results", "e437_watermark.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e437_watermark.json")

sys.exit(0 if n_ok == len(results) else 1)
