"""E-269 全面体检：五个机制都跑通了，现在最该修什么？

不再猜，用数据排序。检查维度：
  ① 真实库的数据健康度（有多少垃圾/重复/异常值）
  ② 各机制的效果指标（不是"能跑"，是"跑得好不好"）
  ③ 用户体验指标（注入预算、响应延迟）
  ④ 数据一致性（有没有互相矛盾的记录）
"""
import sys
import os
import json
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

from store_v2 import MemoryStore
import audit as _audit

DB = os.path.join(_MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 78)
print("E-269 全面体检")
print("=" * 78)

issues = []

# ============ ① 数据健康度 ============
print("\n① 数据健康度")

n_valid = st.con.execute(
    "SELECT COUNT(*) FROM episodes WHERE invalid_at IS NULL").fetchone()[0]
n_invalid = st.con.execute(
    "SELECT COUNT(*) FROM episodes WHERE invalid_at IS NOT NULL").fetchone()[0]
n_vec = st.con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
print(f"   有效记忆: {n_valid} | 失效: {n_invalid} | 向量: {n_vec}")

# 向量数是否匹配
if n_vec != n_valid + n_invalid:
    issues.append({"sev": "high", "what": f"向量数({n_vec}) 与记忆总数({n_valid+n_invalid}) 不匹配",
                   "fix": "重建向量索引"})

# 空/极短记忆
short = st.con.execute(
    "SELECT COUNT(*) FROM episodes WHERE invalid_at IS NULL "
    "AND length(trim(raw_text)) < 5").fetchone()[0]
print(f"   极短记忆(<5字): {short}")
if short > 0:
    issues.append({"sev": "low", "what": f"{short} 条极短记忆",
                   "fix": "可能是垃圾，需人工判断"})

# 重复记忆（相同 text_hash）
dup = st.con.execute(
    "SELECT text_hash, COUNT(*) c FROM episodes WHERE invalid_at IS NULL "
    "GROUP BY text_hash HAVING c > 1").fetchall()
print(f"   重复记忆组: {len(dup)}")
if dup:
    issues.append({"sev": "medium", "what": f"{len(dup)} 组重复记忆",
                   "fix": "应该 merge，但需模型判断"})

# 异常值：affect/valence 越界
bad_affect = st.con.execute(
    "SELECT COUNT(*) FROM episodes WHERE affect_peak < 0 OR affect_peak > 1").fetchone()[0]
bad_val = st.con.execute(
    "SELECT COUNT(*) FROM episodes WHERE valence < -1 OR valence > 1").fetchone()[0]
print(f"   affect 越界: {bad_affect} | valence 越界: {bad_val}")
if bad_affect or bad_val:
    issues.append({"sev": "high", "what": f"affect({bad_affect})/valence({bad_val}) 越界",
                   "fix": "钳制到合法范围"})

# ============ ② 机制效果 ============
print("\n② 机制效果")

astats = _audit.stats(st)
print(f"   审计总数: {astats['total']}")
for k in ["remember", "recall", "inject", "sleep", "conflict",
          "chain", "affect", "config", "note"]:
    n = astats["by_kind"].get(k, 0)
    print(f"     {k:<10} {n}")

# 检查 conflict 的 no_action 比例
conf_rows = st.con.execute(
    "SELECT extra FROM audit_log WHERE kind='conflict'").fetchall()
no_action = 0
for (ex,) in conf_rows:
    try:
        d = json.loads(ex) if ex else {}
        if d.get("no_action"):
            no_action += 1
    except Exception:
        pass
print(f"\n   conflict 中 no_action: {no_action}/{len(conf_rows)}")
if conf_rows and no_action / len(conf_rows) > 0.3:
    issues.append({"sev": "medium",
                   "what": f"conflict {no_action}/{len(conf_rows)} 次未解析出目标",
                   "fix": "模型输出格式需核对，或 prompt 要加强"})

# ============ ③ 用户体验指标 ============
print("\n③ 用户体验")

# 注入历史里的实际字符数
inject_rows = st.con.execute(
    "SELECT extra FROM audit_log WHERE kind='inject' "
    "ORDER BY seq DESC LIMIT 20").fetchall()
chars = []
for (ex,) in inject_rows:
    try:
        d = json.loads(ex) if ex else {}
        c = d.get("content_chars")
        if c:
            chars.append(c)
    except Exception:
        pass
if chars:
    avg = sum(chars) / len(chars)
    print(f"   注入字符数: 平均 {avg:.0f}, 最大 {max(chars)}, 最小 {min(chars)}")
    if avg > 3000:
        issues.append({"sev": "medium",
                       "what": f"注入偏大（平均 {avg:.0f} 字）",
                       "fix": "检查是否有冗余段落"})

# 内核响应时间（从 debug log 看）
debug_log = os.path.join(_MEM, "plugin-debug.jsonl")
if os.path.exists(debug_log):
    import io
    ms_search = []
    with io.open(debug_log, "r", encoding="utf-8", errors="replace") as f:
        for line in f.readlines()[-500:]:
            try:
                e = json.loads(line)
                if e.get("kind") == "assemble":
                    v = e.get("msSearch")
                    if v:
                        ms_search.append(v)
            except Exception:
                continue
    if ms_search:
        print(f"   检索耗时: 平均 {sum(ms_search)/len(ms_search):.0f}ms, "
              f"最大 {max(ms_search)}ms")
        if max(ms_search) > 2000:
            issues.append({"sev": "medium",
                           "what": f"检索最慢 {max(ms_search)}ms",
                           "fix": "需要优化"})

# ============ ④ 数据一致性 ============
print("\n④ 数据一致性")

# superseded_by 指向不存在的记忆
bad_sup = st.con.execute(
    "SELECT COUNT(*) FROM episodes e WHERE e.superseded_by != '' "
    "AND e.superseded_by IS NOT NULL "
    "AND NOT EXISTS (SELECT 1 FROM episodes x WHERE x.ep_id = e.superseded_by)"
).fetchone()[0]
print(f"   superseded_by 指向不存在的记忆: {bad_sup}")
if bad_sup:
    issues.append({"sev": "high", "what": f"{bad_sup} 条悬空 superseded_by",
                   "fix": "清理或修复"})

# 已失效但没 superseded_by 也没原因
orphan = st.con.execute(
    "SELECT COUNT(*) FROM episodes WHERE invalid_at IS NOT NULL "
    "AND (superseded_by IS NULL OR superseded_by = '')").fetchone()[0]
print(f"   失效但无 superseded_by: {orphan}（正常，遗忘不等于取代）")

# L0-L4 分层统计
try:
    import hook as _hook
    hs = _hook.stats(st)
    print(f"   分层: {hs.get('by_level', {})}")
except Exception as e:
    print(f"   分层统计失败: {e}")

# ============ ⑤ 结论 ============
print("\n" + "=" * 78)
print("体检结论")
print("=" * 78)

if not issues:
    print("\n  ✅ 未发现明显问题")
else:
    order = {"high": 0, "medium": 1, "low": 2}
    issues.sort(key=lambda x: order.get(x["sev"], 9))
    print(f"\n  发现 {len(issues)} 个问题：")
    for i, iss in enumerate(issues, 1):
        print(f"\n  {i}. [{iss['sev'].upper()}] {iss['what']}")
        print(f"     修法: {iss['fix']}")

st.con.close()

out = {"experiment": "E-269", "issues": issues,
       "n_valid": n_valid, "n_invalid": n_invalid, "n_vec": n_vec,
       "audit": astats["by_kind"]}
p = os.path.join(_HERE, "results", "e269_checkup.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e269_checkup.json")
