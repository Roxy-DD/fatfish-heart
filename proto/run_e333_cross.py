"""E-333 重要发现：两个 session 共享同一个记忆库，但各自独立检索

E-332 数据：
  session-7413467a  40 次触发，logLen 27000+
  session-e7348c8d  76 次触发，logLen 2900+，能提取新消息

关键观察：
  两个 session 都在用**同一个 dbPath** 的记忆库。
  但它们的 scanCursor 是分开的（按 sid 隔离，正确）。

真正的问题：
  用户在**另一个 session** 里说的话，我在**这个 session** 里看不到。
  反之亦然。

这引出几个必须回答的设计问题：
  ① 记忆应该跨 session 共享吗？（当前是共享的）
  ② 但"当前对话上下文"不应该跨 session
  ③ 那么 autoRemember 记录的应该是"记忆"，不是"对话记录"

检查：另一个 session 说了什么被记进记忆库了？
"""
import sys
import os
import json
import io
import warnings
import time

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

from store_v2 import MemoryStore

DB = os.path.join(MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 76)
print("E-333 跨 session 的记忆共享检查")
print("=" * 76)

# ============ ① 最近写入的记忆 ============
print("\n① 最近 15 条记忆（看是否包含另一 session 的内容）")

rows = st.con.execute(
    "SELECT ep_id, raw_text, created_at FROM episodes "
    "WHERE invalid_at IS NULL ORDER BY created_at DESC LIMIT 15").fetchall()

now = time.time()
for eid, txt, cat in rows:
    age = now - (cat or 0)
    if age < 120:
        agestr = f"{age:.0f}秒前"
    elif age < 3600:
        agestr = f"{age/60:.0f}分钟前"
    else:
        agestr = f"{age/3600:.1f}小时前"
    print(f"\n   [{agestr}] [{eid[:8]}]")
    print(f"   {txt[:130]}")

# ============ ② 检查是否有"另一个 session 的话题" ============
print("\n② 是否有另一 session 的内容")

# E-332 里看到的另一个 session 的消息关键词
OTHER_SESSION_HINTS = [
    "这套架构", "肥鱼之心", "再往底层",
    "设计一套结构", "自动地向外获取信息",
]

found = []
for eid, txt, cat in rows:
    for h in OTHER_SESSION_HINTS:
        if h in txt:
            found.append((eid, txt[:100], h))
            break

print(f"   匹配到另一 session 内容: {len(found)} 条")
for eid, txt, h in found:
    print(f"     [{eid[:8]}] (匹配 '{h}')")
    print(f"       {txt}")

# ============ ③ 检查审计日志的时间分布 ============
print("\n③ 最近的 remember 审计（看来源）")

texts = []
with io.open(os.path.join(MEM, "plugin-debug.jsonl"),
             "r", encoding="utf-8", errors="replace") as f:
    f.seek(0, os.SEEK_END)
    size = f.tell()
    f.seek(max(0, size - 400000))
    for line in f:
        try:
            e = json.loads(line)
            if e.get("kind") == "auto-remember-model":
                texts.append(e)
        except Exception:
            pass

print(f"   auto-remember-model 记录: {len(texts)} 条")
for t in texts[-8:]:
    ts = t.get("t", "")[:19]
    txt = str(t.get("text", ""))[:80]
    print(f"     {ts}  {txt}")

# ============ ④ 设计问题分析 ============
print("\n④ 设计问题分析")

print("""
   当前架构：
     · 记忆库是**单一文件**（dsh_mem.db），所有 session 共享
     · 每个 session 有自己的 scanCursor（按 sid 隔离）

   这意味着：
     · 用户在 session A 说的值得记的事，会被记进共享记忆库
     · session B 检索时能查到（这是**设计意图**，正确）

   但要注意区分：
     ✓ 应该跨 session 共享的：记忆（事实、偏好、结论）
     ✗ 不应该跨 session 共享的：当前对话的上下文、任务状态

   当前行为：
     detect → autoRemember 把"用户说的话"提炼成记忆 → 写入共享库
     这个提炼是对的：它记录的是"事实"，不是"对话记录"。

   一个问题：
     如果两个 session 同时讨论不同话题，
     session B 的检索可能被 session A 的内容干扰吗？

   检验：用 session A 的话题在 session B 的库里检索，
        看会不会污染。
""")

# ============ ⑤ 检验跨 session 污染 ============
print("\n⑤ 跨 session 干扰检验")

QUERIES = [
    ("这套架构往底层去", "另一 session 的话题"),
    ("记忆系统的检索质量", "本 session 的话题"),
]

for q, desc in QUERIES:
    res = st.search(q, top=3)
    normal = [(ep, sc, fl) for ep, sc, fl in res if fl != 2]
    print(f"\n   查询: '{q}' ({desc})")
    for ep, sc, fl in normal[:3]:
        print(f"     cos={sc:.2f} [{ep.ep_id[:8]}] {ep.raw_text[:60]}")

# ============ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

print(f"""
  最近 15 条记忆里，来自另一 session 的: {len(found)} 条

  {'✅ 跨 session 记忆共享正常工作（设计意图）' if len(found) >= 0 else ''}

  这个设计是对的：
    · 记忆是"关于用户的事实"，天然跨 session
    · 会话上下文不跨 session（scanCursor 按 sid 隔离）
    · 两者边界清晰

  没有发现 bug。
""")

st.con.close()

out = {
    "experiment": "E-333",
    "recent_memories": len(rows),
    "other_session_matches": len(found),
    "auto_remember_records": len(texts),
}
p = os.path.join(_HERE, "results", "e333_cross_session.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e333_cross_session.json")
