"""E-276 查清：note 审计有 2 条，但 notes 表 0 条——矛盾"""
import os
import sys
import warnings
import json

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proto", "cosmos_mem"))

from store_v2 import MemoryStore

st = MemoryStore(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proto", "cosmos_mem", "dsh_mem.db"))

print("=" * 72)
print("E-276 note 审计与 notes 表不一致")
print("=" * 72)

rows = st.con.execute(
    "SELECT seq, at, ep_id, msg, extra FROM audit_log "
    "WHERE kind='note' ORDER BY seq").fetchall()

print(f"\n审计里 note 记录: {len(rows)} 条")
for seq, at, ep_id, msg, extra in rows:
    print(f"\n  seq={seq}")
    print(f"  msg: {msg}")
    try:
        d = json.loads(extra) if extra else {}
        print(f"  extra: {json.dumps(d, ensure_ascii=False)[:200]}")
    except Exception:
        print(f"  extra: {extra}")

# 检查 notes 表
print("\n" + "=" * 72)
print("notes 表状态")
print("=" * 72)
all_notes = st.con.execute(
    "SELECT note_id, title, tier, invalid_at FROM notes").fetchall()
print(f"\nnotes 表总行数（含失效）: {len(all_notes)}")
for nid, title, tier, inv in all_notes:
    status = "有效" if inv is None else "失效"
    print(f"  [{status}] [{tier}] {title}")

# 从审计 extra 里找 note_id
print("\n" + "=" * 72)
print("交叉验证")
print("=" * 72)
audit_note_ids = set()
for seq, at, ep_id, msg, extra in rows:
    try:
        d = json.loads(extra) if extra else {}
        if d.get("note_id"):
            audit_note_ids.add(d["note_id"])
    except Exception:
        pass

table_ids = {n[0] for n in all_notes}
print(f"\n审计提到的 note_id: {audit_note_ids}")
print(f"表里存在的 note_id: {table_ids}")

if audit_note_ids - table_ids:
    print(f"⚠ 审计提到但表里没有: {audit_note_ids - table_ids}")
    print("  → 可能是测试后清理了数据，但审计是追加的（正常，审计只增）")
else:
    print("✅ 一致")

st.con.close()
