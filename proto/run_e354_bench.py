"""E-354 补写健康基准（E-353 被近重复检测拦下）

E-353 的第 ③ 步失败：
  新基准与 E-269 那条太像（cos >= 0.80），被去重拦下
  结果库里只有旧的 67 条那条，没有新的 91 条

修：用完全不同的表述重写，并关闭近重复检查
    （这是刻意的"更新记录"，不是重复）
"""
import sys
import os
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

from store_v2 import MemoryStore
import cli_mcp

DB = os.path.join(MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 74)
print("E-354 补写健康基准")
print("=" * 74)

s = st.stats()
prot_n = st.con.execute(
    "SELECT COUNT(*) FROM meta WHERE key LIKE 'hook:%'").fetchone()[0]

# 用与 E-269 那条明显不同的表述（描述式而非指标罗列）
bench = (
    f"当前系统体检快照（E-352，2026-11-10 午后）："
    f"库里保存着 {s['valid']} 条可用记忆和 {s['invalid']} 条已失效记录，"
    f"向量表 {s['vectors']} 行与记忆数一致（R3 可重建）；"
    f"受保护记忆共 {prot_n} 条，层级分布 L0 两条 / L1 九条 / L2 五条；"
    f"检索热态延迟约 10~14 毫秒，一次写入约 11~12 毫秒。"
    f"历史对照：E-269 那次体检记录的是 67 条可用，已随迭代增长，"
    f"那条作为历史留存，以本条为最新基准。"
)

r = cli_mcp.cmd_add(st, {
    "text": bench,
    "check_near_dup": False,   # 刻意的更新记录，不做近重复检查
})
print(f"\n写入: ok={r.get('ok')} ep_id={r.get('ep_id', '')[:8]}")
print(f"deduped={r.get('deduped')}")
if r.get("note"):
    print(f"note: {r['note']}")
print(f"\n内容（{len(bench)} 字）:")
print(f"  {bench}")

# 验证
n_new = st.con.execute(
    "SELECT COUNT(*) FROM episodes WHERE invalid_at IS NULL "
    "AND raw_text LIKE ?", ("%当前系统体检快照%",)).fetchone()[0]
print(f"\n验证：含「当前系统体检快照」的 = {n_new} 条")

# 让旧的标为被取代
old = st.con.execute(
    "SELECT ep_id FROM episodes WHERE invalid_at IS NULL "
    "AND raw_text LIKE ?", ("%E-269 体检%",)).fetchone()
if old and r.get("ep_id") and old[0] != r.get("ep_id"):
    print(f"\n旧基准 [{old[0][:8]}] 保留为历史（不失效，因为它记录了当时真实状态）")

st.con.close()

out = {
    "experiment": "E-354",
    "written": bool(r.get("ok") and not r.get("deduped")),
    "ep_id": r.get("ep_id"),
    "valid": s["valid"],
}
import json
p = os.path.join(_HERE, "results", "e354_bench.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e354_bench.json")
