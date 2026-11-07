"""E-259 让 note 从"有代码"变成"真运转"

E-258 诊断：
  note 功能完整（add/promote 实测通过），但零使用。
  原因不是功能坏，而是**没有触发时机**。

我的判断：
  不该靠模型"想起来"去写笔记，而应该让系统在合适时机
  **自动发现**"该写笔记了"，并把提示送到模型面前。

设计（纯机械检测 + 模型决策）：
  每次会话开始时（assemble 首次）：
    ① 系统机械扫描：哪些主题的记忆/随笔攒够了
    ② 若有，注入一段提示："以下主题可以整理成笔记"
    ③ 模型自行决定要不要做

  → 系统负责"发现"，模型负责"决定"，符合职责划分原则。

本实验先验证内核侧的检测逻辑可用，再接到插件。
"""
import sys
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

from store_v2 import MemoryStore
import triage as _tg
import notebook as _nb
import compose as _cp
import cli_mcp

DB = os.path.join(_HERE, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 76)
print("E-259 验证：自动发现『该写笔记了』")
print("=" * 76)

# ============ ① 现有数据能发现什么 ============
print("\n① 用真实库扫描")

s1 = _tg.suggest_from_triage(st, note_hint_min=3)
print(f"\n   分诊建议（记忆 → 笔记）: {len(s1)} 组")
for x in s1[:3]:
    print(f"      {x['quadrant']}: {x['count']} 条 → 写{x['suggest_tier']}")

s2 = _nb.suggest_note_topics(st, min_count=3)
print(f"\n   主题累积建议: {len(s2)} 个")
for x in s2[:3]:
    print(f"      {x['topic']}: {x['count']} 条记忆")

s3 = _cp.compile_all_topics(st, tier="scrap", min_items=3)
print(f"\n   可装订随笔主题: {len(s3)} 个")
for x in s3[:3]:
    print(f"      {x['topic']}: {x['count']} 条随笔")

# ============ ② 设计建议输出格式 ============
print("\n② 建议输出格式（供插件注入）")

def build_hint(st, min_count=3, max_items=3):
    """
    机械汇总"该写笔记了"的提示。
    返回 None 表示没有建议。
    """
    hints = []

    # 1. 分诊：少见但重要的记忆该写详细笔记
    tri = _tg.suggest_from_triage(st, note_hint_min=min_count)
    for x in tri:
        tier_name = "详细笔记" if x["suggest_tier"] == "note" else "简短随笔"
        hints.append({
            "kind": "triage",
            "quadrant": x["quadrant"],
            "count": x["count"],
            "suggest_tier": x["suggest_tier"],
            "text": f"{x['count']} 条{x['quadrant']}类记忆，建议整理成{tier_name}",
            "samples": x.get("samples", [])[:2],
        })

    # 2. 主题累积：同主题记忆够多
    topics = _nb.suggest_note_topics(st, min_count=min_count)
    for x in topics[:max_items]:
        hints.append({
            "kind": "topic",
            "topic": x["topic"],
            "count": x["count"],
            "suggest_tier": "note",
            "text": f"主题「{x['topic']}」已有 {x['count']} 条记忆，建议整理成笔记",
        })

    # 3. 随笔够多了该装订
    scraps = _cp.compile_all_topics(st, tier="scrap", min_items=min_count)
    for x in scraps[:max_items]:
        hints.append({
            "kind": "compose",
            "topic": x["topic"],
            "count": x["count"],
            "suggest_tier": "manual",
            "text": f"主题「{x['topic']}」已有 {x['count']} 条随笔，可以装订成册",
        })

    if not hints:
        return None

    # 拼成一段文本
    lines = ["### 可以整理的知识", "",
             "以下是系统扫描发现的、已积累到一定量的内容。",
             "你可以自主决定是否需要整理（用 mem_note / mem_compose）。", ""]
    for h in hints[:6]:
        lines.append(f"- [{h['kind']}] {h['text']}")
        for s in h.get("samples", []):
            lines.append(f"    例: {s[:50]}")
    return "\n".join(lines)


hint = build_hint(st)
print(f"\n   有建议: {hint is not None}")
if hint:
    print("\n   注入文本预览：")
    for line in hint.split("\n")[:14]:
        print(f"     {line}")

# ============ ③ 无建议时应该返回 None ============
print("\n③ 边界：空库时不应产生噪音")
import os as _os
tmp_db = _os.path.join(_HERE, "work", "test_db", "e259_empty.db")
_os.makedirs(_os.path.dirname(tmp_db), exist_ok=True)
for suf in ("", "-wal", "-shm"):
    p = tmp_db + suf
    if _os.path.exists(p):
        _os.remove(p)

st2 = MemoryStore(tmp_db)
hint_empty = build_hint(st2)
print(f"   空库有建议: {hint_empty is not None}（应为 False）")
st2.con.close()

# ============ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

print(f"""
  真实库: 发现 {len(s1) + len(s2) + len(s3)} 条建议
  空库:   {'正确返回 None' if hint_empty is None else '错误地产生了建议'}

  下一步：把 build_hint 接到插件 assemble，
         在会话首次注入时附带这段提示。
""")

st.con.close()

out = {
    "experiment": "E-259",
    "triage_hints": len(s1),
    "topic_hints": len(s2),
    "compose_hints": len(s3),
    "has_hint": hint is not None,
    "empty_correct": hint_empty is None,
    "hint_preview": (hint or "")[:600],
}
p = os.path.join(_HERE, "results", "e259_hint.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e259_hint.json")
