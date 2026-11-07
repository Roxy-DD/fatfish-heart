"""执行恢复出厂：清除所有私人记忆，只保留通用种子记忆

用户要求：
  "记忆初始化后清空了，但以下内容应该是需要保留的：
   1. 项目本身的记忆
   2. 相关记忆钩子
   3. 有价值、通用性的记忆（提高系统和模型能力），不要私人具体内容"

R2 承诺：只打失效标记，绝不物理删除。
"""
import sys
import os
import json
import shutil
import time

sys.path.insert(0, r"C:\Users\xuee\Desktop\肥鱼之心\proto\cosmos_mem")
from store_v2 import MemoryStore
import seed as _seed
import hook as _hook

DB = r"C:\Users\xuee\Desktop\肥鱼之心\proto\cosmos_mem\dsh_mem.db"
WORK = r"C:\Users\xuee\Desktop\肥鱼之心\proto\work"

ts = time.strftime("%Y%m%d_%H%M%S")

# ---------- 备份 ----------
backup = os.path.join(WORK, f"dsh_mem_before_factory_{ts}.db")
os.makedirs(WORK, exist_ok=True)
shutil.copy2(DB, backup)
print(f"① 备份 → {backup} ({os.path.getsize(backup)/1e6:.2f} MB)")

st = MemoryStore(DB)

before = st.stats()
print(f"\n② 恢复出厂前：{before['valid']} 条有效")

# ---------- 1. 先装载种子 ----------
print(f"\n③ 装载通用种子记忆")
r = _seed.load_seeds(st, force=True)
print(f"   写入 {r['loaded']} 条，跳过 {r['skipped']} 条")
for t in r["titles"]:
    print(f"     · {t}")

# ---------- 2. 恢复出厂：非种子全部失效 ----------
print(f"\n④ 恢复出厂（非种子全部失效，R2 只打标记）")
r2 = _seed.restore_seeds_only(st)
print(f"   失效 {r2['invalidated']} 条私人/碎片记忆")
print(f"   保留 {r2['seeds_kept']} 条种子记忆")

# ---------- 3. 结果 ----------
after = st.stats()
hstats = _hook.stats(st)
print(f"\n{'='*70}")
print(f"恢复出厂完成")
print(f"  有效记忆: {after['valid']} 条（全部为种子）")
print(f"  失效保留: {after['invalid']} 条（R2：可溯源）")
print(f"  保护层级: {json.dumps(hstats['by_level'], ensure_ascii=False)}")
print("=" * 70)

# ---------- 4. 列出最终种子 ----------
print(f"\n=== 最终保留的种子记忆 ===")
rows = st.con.execute(
    "SELECT ep_id, raw_text FROM episodes WHERE invalid_at IS NULL "
    "ORDER BY created_at ASC").fetchall()
titles = _seed.seed_titles(st)
id2title = {v: k for k, v in titles.items()}

records = []
for eid, txt in rows:
    lv = _hook.level_of(st, eid) or "L3"
    title = id2title.get(eid, "?")
    print(f"\n[{lv}] {title}  ({len(txt)} 字)")
    print(f"    {txt[:110]}...")
    records.append({"ep_id": eid, "level": lv, "title": title,
                    "chars": len(txt), "text": txt})

# ---------- 5. 保存清单 ----------
manifest = {
    "created_at": ts,
    "before_valid": before["valid"],
    "after_valid": after["valid"],
    "invalidated": r2["invalidated"],
    "seeds": records,
}
mp = os.path.join(WORK, f"factory_reset_{ts}.json")
with open(mp, "w", encoding="utf-8") as f:
    json.dump(manifest, f, ensure_ascii=False, indent=1)
print(f"\n清单 → {mp}")

st.con.close()
