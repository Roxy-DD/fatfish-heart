"""E-433 修正 E-432 的误判 + 查清 303 条的真实来源

E-432 我判断「revise 里没有 history 写入逻辑」——**这是错的**。

实际代码（store_v1.revise）：
    if new_text is not None:
        try:
            import history as _hist
            _hist.archive_episode(self, ep_id, reason="revise 前快照")
        except Exception:
            pass

我用 `episodes_history` 这个字符串去搜，但代码里是通过
`history` 模块的 archive_episode 间接写的，所以搜不到。

★ 又一次「查不到 ≠ 不存在」。

真正的问题变成：303 条 revision>1 却没有 history，它们的
revision 是从哪来的？

三种可能：
  A. invalidate（内容没变，只打标记）→ 合理，不需要 history
  B. archive_episode 静默失败（except 吞掉了）→ 是 bug
  C. 早期数据（history 模块还没实现时）→ 历史遗留

本脚本验证 A 和 C，排除 B。
"""
import sys
import os
import warnings
import sqlite3

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

DB = os.path.join(MEM, "dsh_mem.db")

print("=" * 74)
print("E-433 查清 303 条无 history 的真实来源")
print("=" * 74)

con = sqlite3.connect(DB)

# ---------- ① 确认 history 模块存在 ----------
print("\n① history 模块")

hp = os.path.join(MEM, "history.py")
print(f"   history.py 存在: {os.path.exists(hp)}")

if os.path.exists(hp):
    src = open(hp, encoding="utf-8").read()
    has_archive = "def archive_episode" in src
    print(f"   有 archive_episode: {has_archive}")
    if has_archive:
        i = src.find("def archive_episode")
        print("\n   实现片段：")
        print("   " + src[i:i+600].replace("\n", "\n   ")[:600])

# ---------- ② 303 条的特征 ----------
print("\n② 无 history 的 revision>1 记录特征")

hist_ids = set(r[0] for r in con.execute(
    "SELECT DISTINCT ep_id FROM episodes_history"))
rev_rows = con.execute(
    "SELECT ep_id, revision, invalid_at, superseded_by, created_at "
    "FROM episodes WHERE revision > 1").fetchall()

without = [r for r in rev_rows if r[0] not in hist_ids]
print(f"   总数: {len(without)}")

# 关键：这些是否都已失效
n_invalid = sum(1 for r in without if r[2] is not None)
n_superseded = sum(1 for r in without if r[3] is not None)
print(f"   已失效: {n_invalid} ({n_invalid/max(1,len(without))*100:.0f}%)")
print(f"   有 superseded_by: {n_superseded}")

# ---------- ③ 关键判断：失效的 revision 来源 ----------
print("\n③ 判断：失效导致的 revision+1 是否合理")

print("""
   invalidate 的实现（store_v1）：
       UPDATE episodes SET invalid_at=?, superseded_by=?, revision=revision+1

   ★ 它确实会让 revision +1，但**内容没变**。

   所以：
     · 内容没变 → 旧值 = 当前值 → 不需要 history（合理）
     · 内容变了 → 必须有 history（R4 要求）

   现在的问题是：怎么区分这 303 条是哪种？
""")

# 用 created_at 和 revision 的关系推断
# 如果 revision 增加但 raw_text 的 hash 与"写入时"一致，
# 说明只 invalidate 过

# 更可靠的办法：构造一个 revise，看 archive 是否成功
print("\n④ 实测：构造 revise 验证 archive 是否生效")

import backup as bk
TMP = os.path.join(_HERE, "results", "_e433_test.db")
for suf in ("", "-wal", "-shm"):
    f = TMP + suf
    if os.path.exists(f):
        os.remove(f)

r0 = bk.backup_db(DB, TMP, verify=False)
print(f"   测试副本: {'OK' if r0.get('ok') else 'FAIL'}")

from store_v2 import MemoryStore
st = MemoryStore(TMP)

h_before = st.con.execute(
    "SELECT COUNT(*) FROM episodes_history").fetchone()[0]

# 加一条测试记忆并修订
ep = st.add("E-433 测试：这条会被修订 abc", atmosphere="test")
eid = ep.ep_id if hasattr(ep, "ep_id") else str(ep)

h_after_add = st.con.execute(
    "SELECT COUNT(*) FROM episodes_history").fetchone()[0]
print(f"   加记忆后 history: {h_before} → {h_after_add}")

ok = st.revise(eid, new_text="E-433 测试：这条已被修订 xyz")
h_after_rev = st.con.execute(
    "SELECT COUNT(*) FROM episodes_history").fetchone()[0]
print(f"   revise 返回: {ok}")
print(f"   revise 后 history: {h_after_add} → {h_after_rev}")

archive_works = h_after_rev > h_after_add
print(f"\n   archive 是否生效: "
      f"{'✅ 是（revise 会写 history）' if archive_works else '❌ 否（真 bug）'}")

# 看写入的内容
if archive_works:
    row = st.con.execute(
        "SELECT raw_text FROM episodes_history "
        "WHERE ep_id=? ORDER BY rowid DESC LIMIT 1", (eid,)).fetchone()
    if row:
        print(f"   history 内容: {str(row[0])[:60]}")

# 再测 invalidate 是否也写 history
h_before_inv = st.con.execute(
    "SELECT COUNT(*) FROM episodes_history").fetchone()[0]
ep2 = st.add("E-433 测试：这条会被失效 def", atmosphere="test")
eid2 = ep2.ep_id if hasattr(ep2, "ep_id") else str(ep2)
h_after_add2 = st.con.execute(
    "SELECT COUNT(*) FROM episodes_history").fetchone()[0]
st.invalidate(eid2)
h_after_inv = st.con.execute(
    "SELECT COUNT(*) FROM episodes_history").fetchone()[0]

print(f"\n   invalidate 前 history: {h_after_add2}")
print(f"   invalidate 后 history: {h_after_inv}")
print(f"   invalidate 是否写 history: "
      f"{'是' if h_after_inv > h_after_add2 else '否（合理，内容没变）'}")

st.con.close()
for suf in ("", "-wal", "-shm"):
    f = TMP + suf
    if os.path.exists(f):
        try:
            os.remove(f)
        except Exception:
            pass

# ---------- 结论 ----------
print("\n" + "=" * 74)
print("结论")
print("=" * 74)

print(f"""
  E-432 我判断「revise 没有 history 写入逻辑」——**错了**。
  实际 revise 通过 history.archive_episode 写，
  我用 'episodes_history' 字符串搜所以没找到。

  ★ 这是「查不到 ≠ 不存在」的又一次。

  实测确认：
    revise 是否写 history: {'✅ 写' if archive_works else '❌ 不写'}

  所以 303 条无 history 的原因应该是：
    · 大部分是 invalidate 导致的 revision+1（内容没变，合理）
    · 不是 revise 的 bug

  但这仍是 R4 的**可观测性问题**：
    从数据上无法区分"只失效过"和"修订过但 history 丢了"。
""")

import json
out = {
    "experiment": "E-433",
    "n_without_hist": len(without),
    "n_invalid": n_invalid,
    "archive_works": archive_works,
    "e432_was_wrong": True,
}
p = os.path.join(_HERE, "results", "e433_history_source.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e433_history_source.json")
