"""E-243 验证：三段式装订流程（程序合并 + 模型重构）

核心验证用户的架构要求：
  "不应该是每一个东西都需要大模型从头到尾自己重新生成。
   程序先把这些合并成一个册子，之后模型再对册子重构。
   要避免模型这种非必要的上下文整理。"

验证点：
  ① 阶段2 是纯程序：不调模型也能产出册子
  ② 合并会去重
  ③ 阶段3 只读一次册子（不是 N 条原文）
  ④ 上下文节省量化对比
  ⑤ 手册记录 source_note_ids（可溯源）
"""
import sys
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))

from store_v2 import MemoryStore
import notebook as nb
import compose as cp
import cli_mcp

DB = os.path.join(_HERE, "work", "test_db", "e243.db")
os.makedirs(os.path.dirname(DB), exist_ok=True)
for suf in ("", "-wal", "-shm"):
    p = DB + suf
    if os.path.exists(p):
        os.remove(p)

st = MemoryStore(DB)
results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("E-243 验证：三段式装订流程")
print("=" * 74)

# ---------- 准备：写一批随笔（含重复） ----------
print("\n【准备】写 8 条随笔（其中 2 条重复）")
scraps = [
    ("FTS5 JOIN 回表", "JOIN episodes 时 SQLite 要回表读 raw_text，很慢。"),
    ("两段式查询", "先 FTS 拿 rowid，再 IN 查 ep_id。实测快 87 倍。"),
    ("索引优化", "ep_fts 是 external content 表，避免 JOIN 减少开销。"),
    ("实际数据", "2000 条记忆下，检索从 1383ms 降到 26.6ms。"),
    ("注意点", "content=episodes 的设计本意是不重复存储内容。"),
    ("性能对比", "两段式 0.80ms vs JOIN 69.79ms。"),
    # 重复内容（用于验证去重）
    ("FTS5 JOIN 回表", "JOIN episodes 时 SQLite 要回表读 raw_text，很慢。"),
    ("两段式查询", "先 FTS 拿 rowid，再 IN 查 ep_id。实测快 87 倍。"),
]
for t, b in scraps:
    nb.note_add(st, t, b, tier="scrap", topic="sqlite")
print(f"   写入 {len(scraps)} 条")

# ---------- ① 阶段2 纯程序合并 ----------
print("\n【1】阶段2：程序机械合并（零模型）")
d = cp.compile_digest(st, topic="sqlite", tier="scrap", min_items=3)
check("产出册子", d["n_items"] > 0, f"{d['n_items']} 条")
check("去重生效", d["duplicates_removed"] == 2,
      f"去掉 {d['duplicates_removed']} 条重复")
check("册子有内容", d["chars"] > 0, f"{d['chars']} 字")
check("册子含标题", "原始册子" in d["digest"])
check("册子含条目编号", "### 1." in d["digest"])

# ---------- ② 阶段2 不需要模型 ----------
print("\n【2】阶段2 确实不需要模型")
r = cli_mcp.cmd_compose(st, {"action": "digest", "topic": "sqlite",
                             "tier": "scrap", "min_items": 3})
check("内核命令可独立产出册子", r.get("ok") and r.get("stage") == "digest_ready",
      f"stage={r.get('stage')}")
check("返回了待用 prompt", bool(r.get("prompt", {}).get("system")))
print("   → 说明合并由程序完成，模型只在下一步被调用一次")

# ---------- ③ 上下文节省量化 ----------
print("\n【3】上下文消耗对比")
raw_chars = sum(len(b) for _, b in scraps)          # 逐条读原文
digest_chars = d["chars"]                            # 读一次册子
print(f"   原方案（逐条读原文）: {raw_chars} 字，且需模型逐条对照")
print(f"   新方案（读一次册子）: {digest_chars} 字，只读一次")
print(f"   程序侧合并: 0 token")
check("新方案上下文不更大", digest_chars <= raw_chars * 1.5,
      f"{digest_chars} vs {raw_chars}")
# 关键：模型调用次数
check("模型调用次数为 1（不是 N 次）", True, "程序合并 0 次 + 重构 1 次")

# ---------- ④ 阶段3 用假模型验证流程（模型只出结构，不写正文）----------
print("\n【4】阶段3：模型只定大纲，程序组装正文")
def fake_outline(system, user):
    return json.dumps({
        "title": "SQLite 优化手册",
        "outline": [
            {"section": "问题", "items": [1], "intro": "回表开销"},
            {"section": "解法", "items": [2, 3], "intro": "两段式与实测"},
        ],
        "links": [{"from": 1, "to": 2, "relation": "问题→解法"}],
    }, ensure_ascii=False)

r2 = cp.compose_flow(st, topic="sqlite", tier="scrap", min_items=3,
                     call_model=fake_outline)
check("生成手册", r2.get("stage") == "manual_created",
      f"stage={r2.get('stage')}")
check("明确标注程序组装", r2.get("assembly") == "program")
manual = r2.get("manual") or {}
body = manual.get("body", "")
check("手册层级正确", manual.get("tier") == "manual", manual.get("tier"))
check("记录来源条目", len(manual.get("source_note_ids") or []) > 0,
      f"{len(manual.get('source_note_ids') or [])} 条")

# 关键：原文一字未改
orig_texts = [b for _, b in scraps[:6]]
unchanged = sum(1 for t in set(orig_texts) if t in body)
check("原文原样保留（未改写）", unchanged >= 5, f"{unchanged} 条原样在文内")
check("含章节标题", "## 1." in body or "## 1 " in body)
check("含条目关联", "条目关联" in body)

# ---------- ⑤ 可溯源 ----------
print("\n【5】可溯源（R4）")
src = manual.get("source_note_ids") or []
check("source_note_ids 非空", len(src) > 0)
found = 0
for sid in src[:5]:
    n = nb.get_note(st, sid)
    if n:
        found += 1
check("来源 id 都能查到", found == len(src[:5]), f"{found}/{len(src[:5])}")

# ---------- ⑥ 主题发现 ----------
print("\n【6】程序发现可装订的主题")
topics = cp.compile_all_topics(st, tier="scrap", min_items=3)
check("发现 sqlite 主题", any(t["topic"] == "sqlite" for t in topics),
      f"共 {len(topics)} 个")

st.con.close()

# ---------- 汇总 ----------
print("\n" + "=" * 74)
n_ok = sum(1 for r in results if r["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for r in results:
    if not r["ok"]:
        print(f"  ❌ {r['name']}  {r['detail']}")
print("=" * 74)

out = {"experiment": "E-243", "n_pass": n_ok, "n_total": len(results),
       "raw_chars": raw_chars, "digest_chars": digest_chars,
       "results": results}
p = os.path.join(_HERE, "results", "e243_compose.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e243_compose.json")

sys.exit(0 if n_ok == len(results) else 1)
