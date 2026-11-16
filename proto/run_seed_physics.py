"""装载「物理世界为最终裁判」种子 + 更新「方法论」"""
import sys
import os
import shutil
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proto", "cosmos_mem"))
from store_v2 import MemoryStore
import seed as _seed
import hook as _hook

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proto", "cosmos_mem", "dsh_mem.db")
WORK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proto", "work")

# 备份
ts = time.strftime("%Y%m%d_%H%M%S")
backup = os.path.join(WORK, f"dsh_mem_before_physics_seed_{ts}.db")
os.makedirs(WORK, exist_ok=True)
shutil.copy2(DB, backup)
print(f"① 备份 → {os.path.basename(backup)}")

st = MemoryStore(DB)
before = st.stats()
print(f"\n② 装载前: 有效 {before['valid']} 条")

# 用 force=True 刷新（方法论内容已更新）
r = _seed.load_seeds(st, force=True)
print(f"\n③ 装载: 写入 {r['loaded']}，跳过 {r['skipped']}")

after = st.stats()
hstats = _hook.stats(st)
print(f"\n④ 装载后: 有效 {after['valid']} 条")
print(f"   保护: L0={hstats['by_level'].get('L0',0)} "
      f"L1={hstats['by_level'].get('L1',0)} "
      f"L2={hstats['by_level'].get('L2',0)}")
print(f"   强制在场: {len(_hook.present_ids(st))} 条")

# 验证新种子
print(f"\n⑤ 验证新种子")
titles = _seed.seed_titles(st)
for t in ["物理世界为最终裁判", "方法论"]:
    eid = titles.get(t)
    if not eid:
        print(f"   ❌ {t}: 未找到")
        continue
    ep = st.get(eid)
    lv = _hook.level_of(st, eid)
    ok = ep and ep.invalid_at is None
    print(f"   {'✅' if ok else '❌'} [{lv}] {t}  {len(ep.raw_text) if ep else 0} 字")

# 检查新种子内容
print(f"\n⑥ 新种子内容摘录")
eid = titles.get("物理世界为最终裁判")
if eid:
    ep = st.get(eid)
    print(f"   {ep.raw_text[:200]}...")

st.con.close()
print(f"\n{'='*66}")
print(f"完成：有效 {after['valid']} 条，L0 现在有 {hstats['by_level'].get('L0',0)} 条")
print(f"备份: {os.path.basename(backup)}")
print("=" * 66)
