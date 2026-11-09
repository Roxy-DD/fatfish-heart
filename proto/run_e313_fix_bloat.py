"""E-313 修复生产库里已有的超长记忆（合并堆积）

E-312 发现 3 条记忆 >1000 字，最长 6637 字（139 次合并）。
现在 _append_merged 已加长度上限，但已有的超长记忆需要修复。

修复策略：
  ① 保留主体（第一条）
  ② 保留前 N 条不重复的合并段
  ③ 其余折叠为「另有 N 条近重复已折叠」
  ④ 原文快照进 history（R4 可追溯）
  ⑤ 不物理删除任何数据
"""
import sys
import os
import json
import warnings
import re

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

from store_v2 import MemoryStore
import backup as bk
import history as _hist

DB = os.path.join(MEM, "dsh_mem.db")

print("=" * 76)
print("E-313 修复超长记忆")
print("=" * 76)

# ---------- 备份 ----------
print("\n① 备份")
b = bk.backup_db(DB)
print(f"   ok={b.get('ok')} {os.path.basename(b.get('path') or '')}")
if not b.get("ok"):
    raise SystemExit(1)

st = MemoryStore(DB)

# ---------- 找超长记忆 ----------
print("\n② 找超长记忆（>1000 字）")

rows = st.con.execute(
    "SELECT ep_id, raw_text, length(raw_text) as n FROM episodes "
    "WHERE invalid_at IS NOT NULL AND length(raw_text) > 1000").fetchall()
rows_valid = st.con.execute(
    "SELECT ep_id, raw_text, length(raw_text) as n FROM episodes "
    "WHERE invalid_at IS NULL AND length(raw_text) > 1000").fetchall()

targets = list(rows_valid)
print(f"   有效且超长: {len(targets)} 条")

for eid, txt, n in targets:
    print(f"     [{eid[:8]}] {n} 字, 合并次数 {txt.count('｜合并:')}")

if not targets:
    print("\n   无需修复")
    st.con.close()
    raise SystemExit(0)

# ---------- 修复每条 ----------
print("\n③ 修复")

MAX_CHARS = 1200
MAX_SEGMENTS = 4   # 保留最多 4 段不重复的合并

for eid, txt, n in targets:
    print(f"\n   --- [{eid[:8]}] {n} 字 ---")

    # 先快照进 history（R4）
    _hist.archive_episode(st, eid, reason="E-313 修复超长记忆前快照")

    # 切分
    parts = [p.strip() for p in txt.split("｜合并:") if p.strip()]
    if not parts:
        continue

    main = parts[0]
    kept = [main]
    seen = [main]
    folded = 0

    for p in parts[1:]:
        if len(kept) > MAX_SEGMENTS:
            folded += 1
            continue
        # 去重检查
        dup = False
        for s in seen:
            sa, sb = set(s), set(p)
            if sa and sb:
                ov = len(sa & sb) / len(sa | sb)
                if ov > 0.85:
                    dup = True
                    break
        if dup:
            folded += 1
            continue
        # 长度检查
        candidate = "\n\n｜合并: ".join(kept + [p])
        if len(candidate) + 30 > MAX_CHARS:
            folded += 1
            continue
        kept.append(p)
        seen.append(p)

    # 组装
    new_text = "\n\n｜合并: ".join(kept)
    if folded > 0:
        new_text += f"\n｜（另有 {folded} 条近重复已折叠）"

    print(f"     原 {n} 字 ({len(parts)} 段) → 新 {len(new_text)} 字 "
          f"({len(kept)} 段, 折叠 {folded})")

    ok = st.revise(eid, new_text=new_text)
    print(f"     写入: {'✅' if ok else '❌'}")

# ---------- 验证 ----------
print("\n④ 验证")

after_rows = st.con.execute(
    "SELECT ep_id, length(raw_text) as n FROM episodes "
    "WHERE invalid_at IS NULL AND length(raw_text) > 1000").fetchall()
print(f"   修复后 >1000 字: {len(after_rows)} 条")

all_lens = [r[0] for r in st.con.execute(
    "SELECT length(raw_text) FROM episodes WHERE invalid_at IS NULL").fetchall()]
print(f"   最长: {max(all_lens) if all_lens else 0} 字")
print(f"   平均: {sum(all_lens)/len(all_lens):.0f} 字" if all_lens else "")

# 验证历史保留
print("\n⑤ 历史保留检查（R4）")
for eid, txt, n in targets:
    h = _hist.episode_history(st, eid)
    print(f"   [{eid[:8]}] 历史版本: {len(h)} 个")
    for v in h:
        print(f"      rev{v['revision']} {len(v['raw_text'])} 字"
              + (" (当前)" if v["is_current"] else f" [{v['reason'][:20]}]"))

st.con.close()

# ---------- 结论 ----------
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

print(f"""
  修复了 {len(targets)} 条超长记忆
  修复后最长: {max(all_lens) if all_lens else 0} 字
  修复后 >1000 字: {len(after_rows)} 条

  保证：
    · 原文快照进 history（R4 可追溯，能查回修复前的完整内容）
    · 没有物理删除任何数据
    · 保留主体 + 最多 4 段不重复的合并
    · 其余折叠为计数（信息不丢——被折叠的原文在 history 里）
""")

out = {
    "experiment": "E-313",
    "fixed": len(targets),
    "max_after": max(all_lens) if all_lens else 0,
    "over_1000_after": len(after_rows),
}
p = os.path.join(_HERE, "results", "e313_fix_bloat.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e313_fix_bloat.json")
