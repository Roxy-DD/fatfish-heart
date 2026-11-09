"""E-323 严重问题：assemble 一直用同一条旧查询

E-322 发现：
  8 次 assemble 全部用同一条查询
  '你能不能去找一下能让你自己重启的插件...'
  而这条消息是**很久以前**说的。

这说明 latest 查询的提取有问题。
可能原因：
  A. recentUserMessages 提取逻辑只看第一条
  B. 日志里的 query 字段记录的是旧值（缓存）
  C. lifecycle 的 steer 消息干扰了消息提取
  D. 消息序号去重导致新消息被跳过
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

print("=" * 76)
print("E-323 查 assemble 查询不更新的原因")
print("=" * 76)

# ============ ① 看日志时间序列 ============
print("\n① assemble 记录的时间序列")

log_path = os.path.join(MEM, "plugin-debug.jsonl")
assemble_records = []
with io.open(log_path, "r", encoding="utf-8", errors="replace") as f:
    for line in f.readlines()[-5000:]:
        try:
            e = json.loads(line)
            if e.get("kind") == "assemble":
                assemble_records.append(e)
        except Exception:
            pass

print(f"   最近 assemble 记录: {len(assemble_records)} 条")

# 打印时间 + 查询（去重显示）
seen = set()
for r in assemble_records[-15:]:
    t = r.get("t", "")[:19]
    q = str(r.get("query", ""))[:35]
    key = (t, q)
    if key in seen:
        continue
    seen.add(key)
    print(f"   {t}  '{q}'")

# ============ ② 看 scan-debug 的记录 ============
print("\n② scan-debug 记录（消息提取诊断）")

scan_records = []
with io.open(log_path, "r", encoding="utf-8", errors="replace") as f:
    for line in f.readlines()[-3000:]:
        try:
            e = json.loads(line)
            if e.get("kind") == "scan-debug":
                scan_records.append(e)
        except Exception:
            pass

print(f"   scan-debug: {len(scan_records)} 条")
for r in scan_records[-3:]:
    t = r.get("t", "")[:19]
    print(f"   {t}  foundUserMessage={r.get('foundUserMessage')} "
          f"nth={r.get('nth')}")
    samples = r.get("samples") or []
    for s in samples[:2]:
        shape = str(s.get("dataShape", ""))[:120]
        print(f"      样本文本: {shape}")

# ============ ③ 看 assemble-scan 记录 ============
print("\n③ assemble-scan 记录（recent 数量）")

scan2 = []
with io.open(log_path, "r", encoding="utf-8", errors="replace") as f:
    for line in f.readlines()[-3000:]:
        try:
            e = json.loads(line)
            if e.get("kind") == "assemble-scan":
                scan2.append(e)
        except Exception:
            pass

print(f"   assemble-scan: {len(scan2)} 条")
for r in scan2[-8:]:
    t = r.get("t", "")[:19]
    print(f"   {t}  recentCount={r.get('recentCount')} "
          f"latest='{str(r.get('latest',''))[:30]}' logLen={r.get('logLen')}")

# ============ ④ 分析 ============
print("\n④ 分析")

if scan2:
    counts = [r.get("recentCount", 0) for r in scan2[-20:]]
    print(f"   最近 20 次 recentCount: {counts}")
    if all(c == 0 for c in counts):
        print(f"   ⚠ 全部为 0 —— recentUserMessages 完全失效")
    elif counts[-1] == 0:
        print(f"   ⚠ 最新一次为 0")
    else:
        print(f"   recentCount 有值，说明提取在工作")

# 检查 query 是否变化
queries = [str(r.get("query", "")) for r in assemble_records[-20:]]
unique_q = set(queries)
print(f"\n   最近 20 次 assemble 的不同查询数: {len(unique_q)}")
if len(unique_q) == 1:
    print(f"   ❌ 只有 1 个查询，严重异常")
    print(f"      内容: '{list(unique_q)[0][:60]}'")
else:
    print(f"   ✅ 有 {len(unique_q)} 个不同查询")

# ============ ⑤ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

issues = []
if len(unique_q) == 1:
    issues.append("assemble 一直用同一查询")
if scan2 and all(r.get("recentCount", 0) == 0 for r in scan2[-10:]):
    issues.append("recentUserMessages 返回空")

print(f"""
  最近 20 次 assemble 的不同查询: {len(unique_q)}
  最近 scan-debug foundUserMessage: {scan_records[-1].get('foundUserMessage') if scan_records else 'N/A'}
""")

if issues:
    print("  问题：")
    for i in issues:
        print(f"    · {i}")
    print("""
  这个 bug 的严重性：
    如果查询不更新，记忆检索就一直是基于**旧话题**做的，
    检索结果与当前对话无关 → 注入的记忆全都没用。

  这可能是"耗 token 却没效果"的另一个原因：
    注入了 5 条记忆，但都是基于旧查询找的，与当前话题无关。
""")
else:
    print("  ✅ 查询更新正常")

out = {
    "experiment": "E-323",
    "n_assemble": len(assemble_records),
    "unique_queries": len(unique_q),
    "issues": issues,
}
p = os.path.join(_HERE, "results", "e323_stale_query.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e323_stale_query.json")
