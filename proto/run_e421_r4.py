"""E-421 补 R4 的直接验证（四条红线里唯一的空白）

发现：
  run_e124_redlines.py 有 test_r1 / test_r2 / test_r3，
  **没有 test_r4**。

  R4 是「可塑性/不物理删除」：
    支持修订、标记失效、取代，旧值永不物理删除可溯源。

  这一直没被单独测过。如果 R4 坏了（比如修订时真删了旧值），
  回归不会报错 —— 因为没人检查它。

本测试补上：
  ① revise 后旧值仍在（只改内容，不删行）
  ② invalidate 后行仍在（只打标记）
  ③ superseded_by 链可追溯
  ④ revision 递增可查
  ⑤ 修订历史进 episodes_history
  ⑥ 任何操作都不减少总行数
"""
import sys
import os
import json
import sqlite3
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

import backup as bk

PROD = os.path.join(MEM, "dsh_mem.db")
TMP = os.path.join(_HERE, "results", "_e421_r4.db")

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("E-421 R4 可塑性直接验证")
print("=" * 74)

# ---------- 准备：用副本，不动生产库 ----------
for suf in ("", "-wal", "-shm"):
    f = TMP + suf
    if os.path.exists(f):
        os.remove(f)

r0 = bk.backup_db(PROD, TMP, verify=False)
check("测试副本创建成功", r0.get("ok") is True)

from store_v2 import MemoryStore
st = MemoryStore(TMP)

# 记录初始状态
n0 = st.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
h0 = st.con.execute(
    "SELECT COUNT(*) FROM episodes_history").fetchone()[0]
print(f"   初始: episodes={n0}, history={h0}")

# ---------- ① revise 不删行 ----------
print("\n【1】revise：改内容不删行")


def add_test_memory(text):
    """加一条测试记忆，返回 ep_id"""
    ep = st.add(text, atmosphere="test")
    return ep.ep_id if hasattr(ep, "ep_id") else str(ep)


old_text = "R4测试：这条会被修订 abc123"
eid = add_test_memory(old_text)
check("测试记忆已创建", bool(eid), str(eid)[:12])

n1 = st.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
check("新增后行数 +1", n1 == n0 + 1, f"{n0} → {n1}")

# 修订
new_text = "R4测试：这条已被修订 xyz789"
st.revise(eid, new_text=new_text)

n2 = st.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
check("revise 后行数不变（没删行）", n2 == n1, f"{n1} → {n2}")

# 内容已变
row = st.con.execute(
    "SELECT raw_text, revision FROM episodes WHERE ep_id=?",
    (eid,)).fetchone()
check("内容已更新", row and new_text in row[0],
      str(row[0])[:40] if row else "")
check("revision 已递增", row and row[1] >= 2, f"revision={row[1] if row else '?'}")

# 旧值进 history
h1 = st.con.execute(
    "SELECT COUNT(*) FROM episodes_history").fetchone()[0]
check("旧值进 episodes_history（可溯源）", h1 > h0, f"{h0} → {h1}")

if h1 > h0:
    hist = st.con.execute(
        "SELECT raw_text FROM episodes_history "
        "ORDER BY rowid DESC LIMIT 1").fetchone()
    if hist:
        check("history 里存的是旧内容",
              old_text in (hist[0] or ""),
              str(hist[0])[:40])

# ---------- ② invalidate 不删行 ----------
print("\n【2】invalidate：打标记不删行")

eid2 = add_test_memory("R4测试：这条会被标记失效 def456")
n3 = st.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
check("第二条已创建", n3 == n2 + 1, f"{n2} → {n3}")

st.invalidate(eid2)
n4 = st.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
check("invalidate 后行数不变", n4 == n3, f"{n3} → {n4}")

row2 = st.con.execute(
    "SELECT invalid_at, revision FROM episodes WHERE ep_id=?",
    (eid2,)).fetchone()
check("invalid_at 已设置", row2 and row2[0] is not None,
      str(row2[0]) if row2 else "")
check("revision 已递增", row2 and row2[1] >= 2,
      f"revision={row2[1] if row2 else '?'}")

# 内容仍在
check("内容仍在（没被删）",
      row2 and "def456" in str(st.con.execute(
          "SELECT raw_text FROM episodes WHERE ep_id=?",
          (eid2,)).fetchone()[0]))

# ---------- ③ superseded_by 链 ----------
print("\n【3】superseded_by：取代关系可追溯")

eid3_old = add_test_memory("R4测试：旧版本 ghi789")
eid3_new = add_test_memory("R4测试：新版本 jkl012")
st.invalidate(eid3_old, superseded_by=eid3_new)

row3 = st.con.execute(
    "SELECT superseded_by, invalid_at FROM episodes WHERE ep_id=?",
    (eid3_old,)).fetchone()
check("superseded_by 已记录",
      row3 and row3[0] == eid3_new, str(row3[0]) if row3 else "")
check("同时标记失效", row3 and row3[1] is not None)

# 能顺链找到新版本
new_row = st.con.execute(
    "SELECT raw_text FROM episodes WHERE ep_id=?",
    (eid3_new,)).fetchone()
check("能通过 superseded_by 找到新版本",
      new_row and "jkl012" in new_row[0],
      str(new_row[0])[:40] if new_row else "")

# ---------- ④ 总行数只增不减 ----------
print("\n【4】核心断言：总行数只增不减")

nf = st.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
check("全程行数只增不减",
      nf >= n0, f"{n0} → {nf}（+{nf-n0}）")

# 所有测试记忆都能找到
test_ids = [eid, eid2, eid3_old, eid3_new]
found = 0
for tid in test_ids:
    r = st.con.execute(
        "SELECT COUNT(*) FROM episodes WHERE ep_id=?",
        (tid,)).fetchone()[0]
    if r == 1:
        found += 1
check("所有测试记忆都还在（含已失效/已取代）",
      found == len(test_ids), f"{found}/{len(test_ids)}")

# ---------- ⑤ 与非测试数据对比 ----------
print("\n【5】确认没影响既有数据")

# 原库的 episodes 数量应保持
orig = st.con.execute(
    "SELECT COUNT(*) FROM episodes WHERE ep_id NOT IN "
    f"({','.join('?'*len(test_ids))})", test_ids).fetchone()[0]
check("既有数据未受影响", orig == n0, f"{orig} vs 原 {n0}")

st.con.close()

# ---------- 清理 ----------
for suf in ("", "-wal", "-shm"):
    f = TMP + suf
    if os.path.exists(f):
        try:
            os.remove(f)
        except Exception:
            pass
print("\n   已删除测试副本（生产库未被触碰）")

# ---------- 汇总 ----------
print("\n" + "=" * 74)
n_ok = sum(1 for x in results if x["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for x in results:
    if not x["ok"]:
        print(f"  ❌ {x['name']}  {x['detail']}")
print("=" * 74)

print(f"""
  ★ 这个测试补的是 R4 的空白：

    之前红线回归有 test_r1 / test_r2 / test_r3，没有 test_r4。
    也就是说「修订时不删旧值」这条从没有被单独验证过。

    如果某次改动让 revise() 变成了 DELETE + INSERT，
    R1/R2/R3 可能都还过，但 R4 已经破了。

  ★ 验证方式：
    用生产库副本跑，不动真库。
    检查行数、revision、history、superseded_by 四个维度。
""")

out = {
    "experiment": "E-421",
    "n_pass": n_ok,
    "n_total": len(results),
    "initial_episodes": n0,
    "final_episodes": nf,
    "history_before": h0,
    "history_after": h1,
}
p = os.path.join(_HERE, "results", "e421_r4.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e421_r4.json")

sys.exit(0 if n_ok == len(results) else 1)
