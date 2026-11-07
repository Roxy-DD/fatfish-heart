"""E-249 深入：conflict 到底跑过没有？

E-248 结论含糊：meta 里有 conflict 记录，但 audit 里 sleep/conflict 都是 0。

需要区分：
  · meta 里的 conflict:* 是历史遗留（修订前的版本？）
  · 还是当前代码在跑

查：
  ① meta conflict 记录的创建时间
  ② log_verdict 的调用情况
  ③ 用真实冲突场景测一遍完整链路
"""
import sys
import os
import json
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

from store_v2 import MemoryStore
import conflict as _cf
import cli_mcp

DB = os.path.join(_MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 76)
print("E-249 深入：conflict 真实状态")
print("=" * 76)

# ---------- ① meta conflict 记录详情 ----------
print("\n① meta conflict 记录")
rows = st.con.execute(
    "SELECT key, val FROM meta WHERE key LIKE 'conflict%' "
    "ORDER BY key DESC LIMIT 10").fetchall()
print(f"   总记录: {st.con.execute('SELECT COUNT(*) FROM meta WHERE key LIKE \'conflict%\'').fetchone()[0]}")

for k, v in rows[:5]:
    try:
        d = json.loads(v)
        keys = list(d.keys()) if isinstance(d, dict) else type(d).__name__
        print(f"   {k}: {keys}")
        if isinstance(d, dict) and "relation" in d:
            print(f"      relation={d.get('relation')}, reason={str(d.get('reason'))[:50]}")
    except Exception:
        print(f"   {k}: (无法解析)")

# ---------- ② 实测：造一对真正冲突的记忆 ----------
print("\n② 实测完整冲突链路")
print("   策略：先写一条基准记忆，再写一条明确冲突的，看是否触发判定")

# 写基准
base_text = "测试服务器的登录密码是 8888"
r1 = cli_mcp.cmd_add(st, {"text": base_text})
ep1 = r1.get("ep_id")
print(f"\n   基准记忆: {base_text}")
print(f"   ep_id: {ep1[:8] if ep1 else '?'}")

# 写冲突（几乎相同但值不同）
conflict_text = "测试服务器的登录密码改成了 9999"
r2 = cli_mcp.cmd_add(st, {"text": conflict_text, "judge": True})
ep2 = r2.get("ep_id")
print(f"\n   冲突记忆: {conflict_text}")
print(f"   ep_id: {ep2[:8] if ep2 else '?'}")
print(f"   need_judge: {r2.get('need_judge')}")
if r2.get("judge_skip_reason"):
    print(f"   跳过原因: {r2.get('judge_skip_reason')}")
if r2.get("pack"):
    print(f"   候选包长度: {len(str(r2.get('pack')))}")
    print(f"   候选包预览:\n{str(r2.get('pack'))[:600]}")

# ---------- ③ 如果有 pack，模拟模型判定 ----------
print("\n③ 模拟模型判定")
if r2.get("need_judge") and r2.get("pack"):
    # 构造模型输出：判定为 replace
    raw = json.dumps({
        "target_ep_id": ep1,
        "relation": "replace",
        "reason": "密码已更新",
        "confidence": 0.95,
        "need_expand": False,
    }, ensure_ascii=False)
    r3 = cli_mcp.cmd_judge(st, {"ep_id": ep2, "raw": raw, "top_k": 10})
    print(f"   cmd_judge ok={r3.get('ok')}")
    print(f"   relation={r3.get('relation')}")
    print(f"   actions={r3.get('actions')}")

    # 验证结果
    old = st.get(ep1)
    print(f"\n   旧记忆状态:")
    print(f"     invalid_at: {old.invalid_at}")
    print(f"     superseded_by: {old.superseded_by[:8] if old.superseded_by else None}")
    if old.invalid_at:
        print(f"     ✅ 旧密码已被正确标记失效")
    else:
        print(f"     ⚠ 旧密码仍有效（可能判定未生效）")
else:
    print("   ⚠ 未触发判定，无法测试")

# ---------- ④ 审计 ----------
print("\n④ 审计日志")
import audit as _audit
astats = _audit.stats(st)
print(f"   conflict: {astats['by_kind'].get('conflict', 0)}")
print(f"   remember: {astats['by_kind'].get('remember', 0)}")

# ---------- 清理测试数据 ----------
print("\n⑤ 清理测试写入")
n_del = 0
for eid in [ep1, ep2]:
    if eid:
        st.con.execute("DELETE FROM episodes WHERE ep_id=?", (eid,))
        st.con.execute("DELETE FROM vectors WHERE ep_id=?", (eid,))
        st.con.execute("DELETE FROM meta WHERE key LIKE ?", (f"%{eid}%",))
        n_del += 1
st.con.commit()
st.invalidate_vector_cache()
print(f"   清理 {n_del} 条测试记忆")

st.con.close()

print("\n" + "=" * 76)
print("结论")
print("=" * 76)
print(f"""
  conflict 代码链路: {'✅ 完整可用' if r2.get('need_judge') else '⚠ 未触发（预筛未过）'}

  关键发现：
    · 若 need_judge=True 且 judge 后旧记忆失效 → 机制完全正常，只是平时不触发
    · 若需要更高相似度才会触发 → 可能需要调门槛
""")
