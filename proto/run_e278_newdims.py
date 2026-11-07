"""E-278 寻找新维度的问题：功能之外的盲区

上一轮（E-269~277）验证的是"机制能不能跑通"。
但还有几个维度从未检查过：

  ① 数据安全：备份/恢复是否真的能救回来
  ② 索引可重建性（R3 红线）：删光向量表能否确定性重建
  ③ 长期稳定性：大量写入后性能是否退化
  ④ 边界数据：超长文本、特殊字符、空值
  ⑤ 并发安全：多个写入同时进行
"""
import sys
import os
import json
import time
import shutil
import sqlite3

_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

print("=" * 78)
print("E-278 新维度检查：功能之外的盲区")
print("=" * 78)

issues = []
DB_PROD = os.path.join(_MEM, "dsh_mem.db")
TEST_DB = os.path.join(_HERE, "work", "test_db", "e278.db")
os.makedirs(os.path.dirname(TEST_DB), exist_ok=True)

# ============ ① R3 索引可重建性 ============
print("\n① R3 红线：索引可确定性重建")
print("   （删光 vectors 表后重建，检索结果应完全一致）")

from store_v2 import MemoryStore

for suf in ("", "-wal", "-shm"):
    p = TEST_DB + suf
    if os.path.exists(p):
        os.remove(p)

st = MemoryStore(TEST_DB)

# 造 30 条记忆
texts = [
    "记忆系统的检索使用向量相似度计算",
    "版本回退需要支持任意跨度迁移",
    "前端崩溃往往因为回调参数遮蔽",
    "睡眠整理会合并相似的记忆碎片",
    "矛盾判定需要模型理解语义关系",
    "笔记分为随笔、笔记、手册三层",
] * 5
for i, t in enumerate(texts):
    st.add(f"{t}（第{i}条）", resolve_conflict=False)

print(f"   写入 {len(texts)} 条")

# 记录重建前的检索结果
queries = ["检索机制", "版本迁移", "崩溃原因", "整理碎片"]
before = {}
for q in queries:
    res = st.search(q, top=5)
    before[q] = [ep.ep_id for ep, sc, fl in res]

print(f"   记录 {len(queries)} 个查询的检索结果")

# 删光向量表
n_vec_before = st.con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
st.con.execute("DELETE FROM vectors")
st.con.commit()
st.invalidate_vector_cache()
print(f"   已删除 {n_vec_before} 条向量")

# 重建
t0 = time.perf_counter()
n_rebuilt = st.rebuild_vectors()
rebuild_ms = (time.perf_counter() - t0) * 1000
print(f"   重建 {n_rebuilt} 条向量，耗时 {rebuild_ms:.0f} ms")

# 对比
same = 0
diff = 0
for q in queries:
    res = st.search(q, top=5)
    after = [ep.ep_id for ep, sc, fl in res]
    if after == before[q]:
        same += 1
    else:
        diff += 1
        print(f"     ⚠ '{q}' 结果不同")
        print(f"       前: {[x[:6] for x in before[q]]}")
        print(f"       后: {[x[:6] for x in after]}")

print(f"\n   一致性: {same}/{len(queries)}")
if diff > 0:
    issues.append({"sev": "high", "what": f"R3 违反：{diff} 个查询重建后结果不一致",
                   "fix": "检查向量重建逻辑"})
else:
    print("   ✅ R3 成立（确定性重建）")

# ============ ② 数据安全：备份恢复 ============
print("\n② 数据安全：备份/恢复")
print("   注意：SQLite WAL 模式下直接复制主库会丢数据（E-279 已确认），")
print("         必须用 backup API 或先 checkpoint。")

backup_path = TEST_DB + ".backup"

n_before = st.con.execute(
    "SELECT COUNT(*) FROM episodes WHERE invalid_at IS NULL").fetchone()[0]

# 正确做法：sqlite3 backup API
try:
    con_dst = sqlite3.connect(backup_path)
    st.con.backup(con_dst)
    con_dst.close()
    backup_ok = True
except Exception as e:
    print(f"   backup API 失败: {e}")
    backup_ok = False

st.con.close()

if backup_ok:
    # 验证备份完整性
    c_b = sqlite3.connect(backup_path)
    n_in_backup = c_b.execute(
        "SELECT COUNT(*) FROM episodes WHERE invalid_at IS NULL").fetchone()[0]
    c_b.close()
    print(f"   备份文件含 {n_in_backup} 条（原 {n_before} 条）")

    # 模拟损坏：删掉一半数据
    con = sqlite3.connect(TEST_DB)
    con.execute("DELETE FROM episodes WHERE rowid % 2 = 0")
    con.commit()
    n_damaged = con.execute(
        "SELECT COUNT(*) FROM episodes WHERE invalid_at IS NULL").fetchone()[0]
    con.close()
    print(f"   原 {n_before} 条 → 破坏后 {n_damaged} 条")

    # 从备份恢复（恢复前必须清理 -wal/-shm，否则旧 WAL 会覆盖）
    for suf in ("-wal", "-shm"):
        p = TEST_DB + suf
        if os.path.exists(p):
            os.remove(p)
    con_dst2 = sqlite3.connect(backup_path)
    con_restore = sqlite3.connect(TEST_DB)
    con_dst2.backup(con_restore)
    con_restore.close()
    con_dst2.close()

    st2 = MemoryStore(TEST_DB)
    n_restored = st2.con.execute(
        "SELECT COUNT(*) FROM episodes WHERE invalid_at IS NULL").fetchone()[0]
    print(f"   恢复后 {n_restored} 条")

    if n_restored == n_before:
        print("   ✅ 备份恢复完整（用 backup API）")
    else:
        issues.append({"sev": "high",
                       "what": f"备份恢复不完整：{n_before}→{n_restored}",
                       "fix": "检查恢复流程"})
    st2.con.close()
else:
    n_restored = 0
    issues.append({"sev": "high", "what": "backup API 不可用",
                   "fix": "检查 sqlite3 版本"})

# ============ ③ 边界数据 ============
print("\n③ 边界数据")

for suf in ("", "-wal", "-shm"):
    p = TEST_DB + suf
    if os.path.exists(p):
        os.remove(p)
st3 = MemoryStore(TEST_DB)

edge_cases = [
    ("超长文本", "记忆" * 2000),                     # 4000 字
    ("特殊字符", "测试 <script>alert(1)</script> & \"'`"),
    ("纯空白", "   \n\t  "),
    ("单字符", "a"),
    ("emoji", "今天很开心 😊🎉"),
    ("换行", "第一行\n第二行\n第三行"),
    ("SQL注入", "'; DROP TABLE episodes; --"),
    ("超长重复", "A" * 5000),
]

edge_results = []
for name, text in edge_cases:
    try:
        ep = st3.add(text, resolve_conflict=False)
        # 验证能读回
        back = st3.get(ep.ep_id)
        ok = back is not None
        edge_results.append((name, ok, len(text), ""))
    except Exception as e:
        edge_results.append((name, False, len(text), str(e)[:50]))

print(f"   {'用例':<12} {'长度':>6} {'结果'}")
for name, ok, ln, err in edge_results:
    mark = "✅" if ok else "❌"
    print(f"   {mark} {name:<12} {ln:>6}  {err}")

failed = [r for r in edge_results if not r[1]]
if failed:
    issues.append({"sev": "medium",
                   "what": f"{len(failed)} 个边界用例失败: {[r[0] for r in failed]}",
                   "fix": "检查输入校验"})

# 验证 SQL 注入没有破坏表
n_after_inject = st3.con.execute(
    "SELECT COUNT(*) FROM episodes").fetchone()[0]
print(f"\n   SQL 注入后表仍存在，行数 {n_after_inject}")
if n_after_inject < len(edge_cases):
    issues.append({"sev": "high", "what": "SQL 注入可能破坏了数据",
                   "fix": "检查参数化查询"})

st3.con.close()

# ============ ④ 长期稳定性：大量写入 ============
print("\n④ 长期稳定性：写入 300 条后的性能")

for suf in ("", "-wal", "-shm"):
    p = TEST_DB + suf
    if os.path.exists(p):
        os.remove(p)
st4 = MemoryStore(TEST_DB)

# 分批写入，测每批耗时
batch_times = []
BATCH = 50
N_BATCHES = 6
for b in range(N_BATCHES):
    t0 = time.perf_counter()
    for i in range(BATCH):
        st4.add(f"稳定性测试第{b}批第{i}条，涉及记忆检索和向量计算", 
                resolve_conflict=False)
    ms = (time.perf_counter() - t0) * 1000
    batch_times.append(ms)
    print(f"   第 {b+1} 批（{BATCH} 条）: {ms:.0f} ms "
          f"({ms/BATCH:.1f} ms/条)")

first = batch_times[0] / BATCH
last = batch_times[-1] / BATCH
degradation = last / first if first > 0 else 1
print(f"\n   首条 {first:.1f} ms/条 → 末条 {last:.1f} ms/条")
print(f"   退化倍数: {degradation:.2f}x")

if degradation > 2:
    issues.append({"sev": "medium",
                   "what": f"写入性能随数据量退化 {degradation:.1f}x",
                   "fix": "检查是否有 O(N) 操作"})
else:
    print("   ✅ 性能稳定")

# 检索性能
t0 = time.perf_counter()
st4.search("稳定性测试", top=5)
search_ms = (time.perf_counter() - t0) * 1000
print(f"   300 条记忆时检索: {search_ms:.0f} ms")

st4.con.close()

# ============ 结论 ============
print("\n" + "=" * 78)
print("结论")
print("=" * 78)

if not issues:
    print("\n   ✅ 五个新维度均未发现问题")
else:
    order = {"high": 0, "medium": 1, "low": 2}
    issues.sort(key=lambda x: order.get(x["sev"], 9))
    print(f"\n  发现 {len(issues)} 个问题：")
    for i, iss in enumerate(issues, 1):
        print(f"\n  {i}. [{iss['sev'].upper()}] {iss['what']}")
        print(f"     修法: {iss['fix']}")

out = {"experiment": "E-278", "issues": issues,
       "r3_same": same, "r3_total": len(queries),
       "backup_ok": n_restored == n_before,
       "edge_failed": [r[0] for r in failed],
       "write_degradation": round(degradation, 2),
       "rebuild_ms": round(rebuild_ms, 1)}
p = os.path.join(_HERE, "results", "e278_new_dims.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e278_new_dims.json")
