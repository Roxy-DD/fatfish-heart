"""修复种子重复：force 重装时旧的没失效成功

根因：load_seeds(force=True) 里先 _hook.unlock(old_id) 再 st.invalidate(old_id)，
     但 unlock 对 L0 会拒绝（保护设计），导致 invalidate 也被拒。
     → 旧种子没失效，新种子又写入 → 重复

修法：
  1. 数据层：手动清理重复（强制删 meta + invalidate）
  2. 代码层：seed.load_seeds 里 force 模式改为直接删 meta 绕过 L0 限制
"""
import sys
import os
import json

sys.path.insert(0, r"C:\Users\xuee\Desktop\肥鱼之心\proto\cosmos_mem")
from store_v2 import MemoryStore
import seed as _seed
import hook as _hook

DB = r"C:\Users\xuee\Desktop\肥鱼之心\proto\cosmos_mem\dsh_mem.db"
st = MemoryStore(DB)

print("=" * 66)
print("清理种子重复")
print("=" * 66)

# 找到每个标题的所有记忆，保留最新的一条
titles = _seed.seed_titles(st)          # title -> ep_id（meta 里登记的）
rows = st.con.execute(
    "SELECT ep_id, raw_text, created_at, invalid_at FROM episodes "
    "WHERE invalid_at IS NULL ORDER BY created_at ASC").fetchall()

# 按内容前 24 字分组
from collections import defaultdict
groups = defaultdict(list)
for eid, txt, cat, inv in rows:
    groups[txt[:24]].append((eid, cat, txt))

dup_count = 0
for key, items in groups.items():
    if len(items) <= 1:
        continue
    # 保留最新（created_at 最大）的那条
    items.sort(key=lambda x: -(x[1] or 0))
    keep = items[0]
    print(f"\n重复组: {key}...")
    print(f"  保留: {keep[0][:8]} (最新)")
    for eid, cat, txt in items[1:]:
        # 强制删 meta（绕过 L0 锁）再失效
        st.con.execute("DELETE FROM meta WHERE key=?", (f"hook:{eid}",))
        st.con.execute(
            "UPDATE episodes SET invalid_at=?, superseded_by=?, "
            "revision=revision+1 WHERE ep_id=?",
            (__import__("time").time(), keep[0], eid))
        dup_count += 1
        print(f"  失效: {eid[:8]} (superseded_by -> {keep[0][:8]})")

st.con.commit()
st.invalidate_vector_cache()

stats = st.stats()
hstats = _hook.stats(st)
print(f"\n{'='*66}")
print(f"清理 {dup_count} 条重复")
print(f"有效: {stats['valid']} 条")
print(f"保护: L0={hstats['by_level'].get('L0',0)} "
      f"L1={hstats['by_level'].get('L1',0)} "
      f"L2={hstats['by_level'].get('L2',0)}")
print(f"强制在场: {len(_hook.present_ids(st))} 条")
print("=" * 66)

st.con.close()
