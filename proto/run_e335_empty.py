"""E-335 分析：recentCount 绝大多数为 0 是正常的还是设计缺陷？

E-334 数据：
  主会话最后 20 次: [0×18, 1, 0]
  只有用户真正发消息时才 recent=1

背景：
  assemble 在**每次**模型调用前都会触发（包括纯工具调用轮次）。
  但用户消息只在真正的对话轮次才有。
  所以 recentCount 大部分为 0 是**正常的**。

真正的问题：
  当 recentCount=0 时，latest='' 空字符串
  那么用来检索的 query 是什么？空 query？

  如果空 query → 检索没有意义，浪费 token 和时间。
  应该跳过检索。

检验：
  ① 空 query 时 search 返回什么
  ② 插件是否有跳过逻辑
"""
import sys
import os
import json
import io
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

from store_v2 import MemoryStore

DB = os.path.join(MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 76)
print("E-335 空 query 的行为")
print("=" * 76)

# ============ ① 空 query 时 search 返回什么 ============
print("\n① 空 query 调 search")

for q in ["", " ", None]:
    try:
        if q is None:
            res = st.search(q if q is not None else "", top=5)
        else:
            res = st.search(q, top=5)
        normal = [(ep, sc, fl) for ep, sc, fl in res if fl != 2]
        print(f"   query={q!r}")
        print(f"     返回 {len(res)} 条（普通 {len(normal)}）")
        for ep, sc, fl in normal[:3]:
            print(f"       cos={sc:.2f} {ep.raw_text[:45]}")
    except Exception as e:
        print(f"   query={q!r} → 异常: {e}")

# ============ ② 检查插件是否有跳过逻辑 ============
print("\n② 插件是否有空 query 跳过逻辑")

plugin = os.path.join(os.path.dirname(_HERE), "dsh-plugin", "index.js")
with open(plugin, "r", encoding="utf-8") as f:
    src = f.read()

# 找 assemble 里对 latest 的判断
import re
idx = src.find("const latest = recent.length")
if idx > 0:
    seg = src[idx:idx + 2500]
    # 找是否有 if (!latest) 或类似的跳过
    has_skip = False
    for line in seg.split("\n"):
        s = line.strip()
        if ("!latest" in s or "latest.trim()" in s or
                "latest.length" in s):
            print(f"   {s[:90]}")
            has_skip = True
    if not has_skip:
        print("   ⚠ 未找到空 query 的跳过逻辑")

# ============ ③ 看日志里空 query 时的实际行为 ============
print("\n③ 日志里空 query 时发生了什么")

log_path = os.path.join(MEM, "plugin-debug.jsonl")
records = []
with io.open(log_path, "r", encoding="utf-8", errors="replace") as f:
    f.seek(0, os.SEEK_END)
    size = f.tell()
    f.seek(max(0, size - 600000))
    for line in f:
        try:
            e = json.loads(line)
            if e.get("kind") in ("assemble", "assemble-skip",
                                 "assemble-empty"):
                records.append(e)
        except Exception:
            pass

print(f"   相关记录: {len(records)}")

# 统计空 query 的次数
empty_q = sum(1 for r in records if not str(r.get("query", "")).strip())
nonempty_q = len(records) - empty_q
print(f"   空 query: {empty_q} 次")
print(f"   非空 query: {nonempty_q} 次")

# ============ ④ 检查耗时 ============
print("\n④ 空 query 时的检索耗时")

times = [r.get("msSearch", 0) for r in records
         if not str(r.get("query", "")).strip()]
if times:
    print(f"   空 query 检索次数: {len(times)}")
    print(f"   耗时: {times[:15]}")
    print(f"   平均: {sum(times)/len(times):.1f}ms")
    print(f"   总计浪费: {sum(times):.0f}ms")
else:
    print("   （没有空 query 的检索记录）")

# ============ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

print(f"""
  空 query 检索: {empty_q} 次
  非空 query 检索: {nonempty_q} 次

  如果空 query 也走了完整检索流程，那就是浪费。
  需要加跳过逻辑。
""")

st.con.close()

out = {
    "experiment": "E-335",
    "empty_queries": empty_q,
    "nonempty_queries": nonempty_q,
    "wasted_ms": sum(times) if times else 0,
}
p = os.path.join(_HERE, "results", "e335_empty_query.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e335_empty_query.json")
