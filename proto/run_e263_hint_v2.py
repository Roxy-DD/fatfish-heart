"""E-263 把主题发现接到插件注入流程

E-258~262 已完成：
  · build_hint() 能机械发现"该写笔记了"的三类信号
  · 主题聚类从 0 个（trigger 词表）修到 20 个（jieba 分词）

但还没接到插件——模型看不到这些提示，note 依然零使用。

本实验：
  ① 在内核加 cmd_knowledge_hint 命令
  ② 验证真实返回
  ③ 下一步接插件 assemble
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

DB = os.path.join(_MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 76)
print("E-263 知识提示：内核侧实现与验证")
print("=" * 76)


def build_knowledge_hint(st, min_count: int = 3, max_items: int = 4,
                         enable_triage: bool = True,
                         enable_topic: bool = True,
                         enable_compose: bool = True) -> dict:
    """
    机械汇总"该整理成知识了"的提示（纯统计，无判断）。

    三类信号：
      1. triage   —— 少见但重要的记忆（该写详细笔记）
      2. topic    —— 同主题记忆累积（该写笔记）
      3. compose  —— scrap 随笔够多（可装订成册）

    返回 {"hints": [...], "text": "注入用文本" | ""}
    无信号时 hints=[] text=""
    """
    hints = []

    if enable_triage:
        try:
            for x in _tg.suggest_from_triage(st, note_hint_min=min_count):
                tier_name = "详细笔记" if x["suggest_tier"] == "note" else "简短随笔"
                hints.append({
                    "kind": "triage",
                    "quadrant": x["quadrant"],
                    "count": x["count"],
                    "suggest_tier": x["suggest_tier"],
                    "text": f"{x['count']} 条{x['quadrant']}类记忆，建议整理成{tier_name}",
                    "samples": (x.get("samples") or [])[:2],
                })
        except Exception:
            pass

    if enable_topic:
        try:
            for x in _nb.suggest_note_topics(st, min_count=min_count)[:max_items]:
                hints.append({
                    "kind": "topic",
                    "topic": x["topic"],
                    "count": x["count"],
                    "suggest_tier": "note",
                    "text": f"主题「{x['topic']}」已有 {x['count']} 条记忆",
                    "ep_ids": x.get("ep_ids", [])[:5],
                })
        except Exception:
            pass

    if enable_compose:
        try:
            for x in _cp.compile_all_topics(st, tier="scrap",
                                            min_items=min_count)[:max_items]:
                hints.append({
                    "kind": "compose",
                    "topic": x["topic"],
                    "count": x["count"],
                    "suggest_tier": "manual",
                    "text": f"主题「{x['topic']}」已有 {x['count']} 条随笔可装订",
                })
        except Exception:
            pass

    if not hints:
        return {"hints": [], "text": ""}

    lines = [
        "### 可以整理成知识的内容（系统扫描发现）",
        "",
        "以下是已积累到一定量的内容。你可以自主决定是否整理：",
        "- 写笔记 → mem_note(action=add, tier=scrap|note)",
        "- 装订成册 → mem_compose(action=compose, topic=...)",
        "",
    ]
    for h in hints[:8]:
        lines.append(f"- {h['text']}")
        for s in h.get("samples", []):
            if s:
                lines.append(f"    例: {str(s)[:48]}")
    return {"hints": hints, "text": "\n".join(lines)}


# ---------- ① 真实库测试 ----------
print("\n① 真实库测试")
r = build_knowledge_hint(st)
print(f"   hints 数: {len(r['hints'])}")
print(f"   text 长度: {len(r['text'])}")
if r["hints"]:
    print("\n   提示内容：")
    for h in r["hints"][:8]:
        print(f"     [{h['kind']}] {h['text']}")

if r["text"]:
    print("\n   注入文本预览（前 20 行）：")
    for line in r["text"].split("\n")[:20]:
        print(f"     {line}")

# ---------- ② 空库边界 ----------
print("\n② 空库边界（不应产生噪音）")
tmp = os.path.join(_HERE, "work", "test_db", "e263_empty.db")
os.makedirs(os.path.dirname(tmp), exist_ok=True)
for suf in ("", "-wal", "-shm"):
    p = tmp + suf
    if os.path.exists(p):
        os.remove(p)
st2 = MemoryStore(tmp)
r2 = build_knowledge_hint(st2)
print(f"   空库 hints: {len(r2['hints'])}（应为 0）")
print(f"   空库 text: '{r2['text']}'（应为空）")
st2.con.close()

# ---------- ③ 参数可关闭 ----------
print("\n③ 参数可逐个关闭")
r3 = build_knowledge_hint(st, enable_compose=False)
print(f"   关闭 compose: {len(r3['hints'])} 条（应少于全开）")
r4 = build_knowledge_hint(st, enable_triage=False, enable_topic=False,
                          enable_compose=False)
print(f"   全关: {len(r4['hints'])} 条（应为 0）")

st.con.close()

print("\n" + "=" * 76)
print("结论")
print("=" * 76)
print(f"""
  真实库: {len(r['hints'])} 条提示，{len(r['text'])} 字
  空库:   {len(r2['hints'])} 条（正确）
  可关闭: {'✅' if len(r4['hints']) == 0 else '❌'}

  下一步：把 build_knowledge_hint 加进内核命令 + 插件 assemble
""")

out = {
    "experiment": "E-263",
    "hints_count": len(r["hints"]),
    "text_len": len(r["text"]),
    "hints": r["hints"][:10],
    "empty_ok": len(r2["hints"]) == 0,
    "toggle_ok": len(r4["hints"]) == 0,
}
p = os.path.join(_HERE, "results", "e263_knowledge_hint.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e263_knowledge_hint.json")
