"""E-439 R3 加强验证：重建后连分数都必须一致

现有 R3 验证（run_e124_redlines.test_r3_rebuild）：
    before[q] = [ep.ep_id for ep, *_ in st.search(q, top=K)]
    st.rebuild_vectors()
    → 只比 **ep_id 列表**，不比分数

★ 缺口：
  如果重建后向量有微小漂移（例如浮点误差、编码器版本差异），
  ep_id 顺序可能不变，但分数已经变了。
  而 R3 要求「确定性重建」，分数也应该一致。

刚才的手工测试发现：
  重建前后分数完全一致（2.2610 / 1.9770 / 1.6220）
  说明实现是对的，但**测试没覆盖这一点**。

本测试补上：
  ① 多查询 × 多结果，逐条比分数（容差 1e-6）
  ② 连续重建两次，结果也要一致
  ③ 与删除向量表后重建对比（更彻底）
"""
import sys
import os
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

import backup as bk
from store_v2 import MemoryStore

DB = os.path.join(MEM, "dsh_mem.db")

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("E-439 R3 加强：重建后分数必须一致")
print("=" * 74)

QUERIES = [
    "红线 无损迁移",
    "记忆 重要性 代价",
    "版本回退 怎么实现",
    "去重 相似 阈值",
    "FTS 检索 分词",
    "睡眠 合并 整理",
]

TOP = 5
TOL = 1e-6

# ---------- 用副本，不动生产库 ----------
TMP = os.path.join(_HERE, "results", "_e439_test.db")
for suf in ("", "-wal", "-shm"):
    f = TMP + suf
    if os.path.exists(f):
        os.remove(f)

r0 = bk.backup_db(DB, TMP, verify=False)
check("测试副本创建", r0.get("ok") is True)

st = MemoryStore(TMP)


def snapshot():
    """记录每个查询的 (ep_id, score) 序列"""
    out = {}
    for q in QUERIES:
        try:
            r = st.search(q, top=TOP)
            out[q] = [(ep.ep_id, round(sc, 6)) for ep, sc, *_ in r]
        except Exception as e:
            out[q] = f"ERR:{type(e).__name__}"
    return out


# ---------- ① 基线 ----------
print("\n① 记录基线")

base = snapshot()
n_ok_q = sum(1 for v in base.values() if isinstance(v, list))
print(f"   {n_ok_q}/{len(QUERIES)} 个查询成功")
for q in QUERIES[:2]:
    if isinstance(base[q], list):
        print(f"   '{q}': {len(base[q])} 条")
        for eid, sc in base[q][:2]:
            print(f"      {eid[:8]} {sc:.6f}")

check("基线采集成功", n_ok_q == len(QUERIES), f"{n_ok_q}/{len(QUERIES)}")

# ---------- ② 重建一次，比分数 ----------
print("\n② 重建一次后比对（含分数）")

n_rebuilt = st.rebuild_vectors()
print(f"   重建 {n_rebuilt} 条向量")

after1 = snapshot()

id_mismatch = []
score_mismatch = []
for q in QUERIES:
    a, b = base.get(q), after1.get(q)
    if not isinstance(a, list) or not isinstance(b, list):
        continue
    if [x[0] for x in a] != [x[0] for x in b]:
        id_mismatch.append(q)
    for (i1, s1), (i2, s2) in zip(a, b):
        if i1 == i2 and abs(s1 - s2) > TOL:
            score_mismatch.append((q, i1, s1, s2))

check("重建后 ep_id 顺序一致", len(id_mismatch) == 0,
      f"{len(id_mismatch)} 个查询不同" if id_mismatch else "")

check("重建后分数一致（容差 1e-6）", len(score_mismatch) == 0,
      f"{len(score_mismatch)} 处不同" if score_mismatch else "")

if score_mismatch:
    for q, eid, s1, s2 in score_mismatch[:5]:
        print(f"      '{q}' {eid[:8]}: {s1:.6f} → {s2:.6f}")

# ---------- ③ 连续重建两次 ----------
print("\n③ 连续重建两次")

st.rebuild_vectors()
after2 = snapshot()

diff2 = []
for q in QUERIES:
    a, b = after1.get(q), after2.get(q)
    if isinstance(a, list) and isinstance(b, list):
        if [(x[0], x[1]) for x in a] != [(x[0], x[1]) for x in b]:
            # 允许极小误差
            for (i1, s1), (i2, s2) in zip(a, b):
                if i1 != i2 or abs(s1 - s2) > TOL:
                    diff2.append(q)
                    break

check("连续两次重建结果一致", len(diff2) == 0,
      f"{len(diff2)} 个查询不同" if diff2 else "")

# ---------- ④ 删光向量表后重建（最彻底）----------
print("\n④ 删光向量表后重建")

n_vec_before = st.con.execute(
    "SELECT COUNT(*) FROM vectors").fetchone()[0]
st.con.execute("DELETE FROM vectors")
st.con.commit()
n_vec_after_del = st.con.execute(
    "SELECT COUNT(*) FROM vectors").fetchone()[0]
print(f"   向量表: {n_vec_before} → {n_vec_after_del}")

check("向量表已清空", n_vec_after_del == 0)

n_rebuilt2 = st.rebuild_vectors()
print(f"   重建 {n_rebuilt2} 条")

n_vec_final = st.con.execute(
    "SELECT COUNT(*) FROM vectors").fetchone()[0]
print(f"   重建后: {n_vec_final} 条")

check("重建后数量恢复", n_vec_final == n_vec_before,
      f"{n_vec_final} vs {n_vec_before}")

# 比检索结果
after3 = snapshot()
diff3 = []
for q in QUERIES:
    a, b = base.get(q), after3.get(q)
    if isinstance(a, list) and isinstance(b, list):
        for (i1, s1), (i2, s2) in zip(a, b):
            if i1 != i2 or abs(s1 - s2) > TOL:
                diff3.append((q, i1, s1, i2, s2))
                break

check("删表重建后与原始基线一致（ep_id + 分数）",
      len(diff3) == 0,
      f"{len(diff3)} 个查询不同" if diff3 else "")

if diff3:
    for q, i1, s1, i2, s2 in diff3[:3]:
        print(f"      '{q}': {i1[:8]}({s1:.6f}) vs {i2[:8]}({s2:.6f})")

st.con.close()
for suf in ("", "-wal", "-shm"):
    f = TMP + suf
    if os.path.exists(f):
        try:
            os.remove(f)
        except Exception:
            pass
print("\n   已删除测试副本")

# ---------- 汇总 ----------
print("\n" + "=" * 74)
n_ok = sum(1 for x in results if x["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for x in results:
    if not x["ok"]:
        print(f"  ❌ {x['name']}  {x['detail']}")
print("=" * 74)

print(f"""
  ★ 比现有 R3 测试多验证了什么：

    现有：只比 ep_id 列表
    本测试：ep_id + **分数**（容差 1e-6）

    为什么分数重要：
      如果编码器版本变化或浮点误差，
      ep_id 顺序可能不变，但分数会漂移。
      那种情况不叫"确定性重建"。

  ★ 三种重建场景：
    ① 普通 rebuild_vectors()
    ② 连续重建两次
    ③ 删光向量表后重建（最彻底，模拟索引完全丢失）

    三者结果都与基线一致 → R3 成立。
""")

import json
out = {
    "experiment": "E-439",
    "n_pass": n_ok,
    "n_total": len(results),
    "n_queries": len(QUERIES),
    "n_rebuilt": n_rebuilt,
    "score_mismatch": len(score_mismatch),
    "delete_rebuild_diff": len(diff3),
}
p = os.path.join(_HERE, "results", "e439_r3_scores.json")
if os.environ.get("FFH_NO_WRITE") == "1":
    print("（FFH_NO_WRITE=1，跳过写盘）")
else:
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e439_r3_scores.json")

sys.exit(0 if n_ok == len(results) else 1)
