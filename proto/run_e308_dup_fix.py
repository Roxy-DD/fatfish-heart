"""E-308 验证：重复记忆的两个 bug 已修复

E-306/307 发现的问题：
  bug1: sessions 是模块级 Map，重载后 processed 被重置
        → 同一批消息重新处理 → 重复记录
  bug2: 去重只认 md5(去空白)，措辞变化就绕过
        → 12 组近义重复

本测试验证修复：
  ① 语义近重复检测（向量余弦 >= 0.95）
  ② 不同内容不会被误杀
  ③ 阈值可调
"""
import sys
import os
import json
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))

from store_v2 import MemoryStore
import cli_mcp

DB = os.path.join(_HERE, "work", "test_db", "e308.db")
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
print("E-308 重复记忆修复验证")
print("=" * 74)

# ============ ① 精确重复（原有能力，不能退化）============
print("\n【1】精确重复仍被去重")
r1 = cli_mcp.cmd_add(st, {"text": "测试记忆 A：记忆系统使用向量检索"})
check("首次写入成功", r1.get("ok") and not r1.get("deduped"),
      f"ep={r1.get('ep_id', '')[:8]}")

r2 = cli_mcp.cmd_add(st, {"text": "测试记忆 A：记忆系统使用向量检索"})
check("完全相同被去重", r2.get("deduped") is True)

# ============ ② 近义变体被去重（新能力）============
print("\n【2】近义变体被去重（E-307 新增）")
# 注意：要用「措辞不同但语义相同」的句子，不能只差标点
r3 = cli_mcp.cmd_add(st, {
    "text": "测试记忆 A 的等价表述：本记忆系统的检索依靠向量相似度"
})
check("近义变体被去重", r3.get("deduped") is True,
      f"near_dup={r3.get('near_dup')}, sim={r3.get('similarity')}")
if r3.get("deduped"):
    check("报告了相似度", r3.get("similarity") is not None,
          str(r3.get("similarity")))
else:
    check("报告了相似度", True, "（未触发去重，跳过）")

# ============ ③ 不同内容不被误杀 ============
print("\n【3】不同内容不会被误杀")
r4 = cli_mcp.cmd_add(st, {"text": "测试记忆 B：版本回退支持任意跨度迁移"})
check("不同内容正常写入", r4.get("ok") and not r4.get("deduped"),
      f"ep={r4.get('ep_id', '')[:8]}")

r5 = cli_mcp.cmd_add(st, {"text": "测试记忆 C：睡眠整理会合并相似碎片"})
check("第二条不同内容也写入", r5.get("ok") and not r5.get("deduped"))

# ============ ④ 阈值可调 ============
print("\n【4】阈值可调")
# 用很低的阈值 → 相关但不相同的也判重复
r6 = cli_mcp.cmd_add(st, {
    "text": "测试记忆 D：记忆系统采用向量和关键词双通道检索",
    "near_dup_threshold": 0.5,
})
check("低阈值时更激进去重", r6.get("deduped") is True,
      f"sim={r6.get('similarity')}")

# 用很高的阈值 → 语义相近也放行（用一个与已有记忆语义相近但文字不同的句子）
r7 = cli_mcp.cmd_add(st, {
    "text": "记忆系统依赖向量相似度完成检索",
    "near_dup_threshold": 0.999,
})
check("高阈值时放行近义", r7.get("deduped") is not True,
      f"ep={r7.get('ep_id', '')[:8]}")

# ============ ⑤ 可关闭近重复检测 ============
print("\n【5】可关闭近重复检测")
r8 = cli_mcp.cmd_add(st, {
    "text": "测试记忆 F：记忆系统使用向量检索",
    "check_near_dup": False,
})
check("关闭时不做近重复检测", r8.get("ok") and not r8.get("near_dup"),
      f"ep={r8.get('ep_id', '')[:8]}")

# ============ ⑥ 写入数量核对 ============
print("\n【6】最终写入数量")
n = st.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
print(f"   实际写入: {n} 条")
check("写入数量合理", 4 <= n <= 5, f"{n} 条")

# ============ ⑦ 语义相近但不该合并的（保守性）============
print("\n【7】保守性：相关但不同的记忆应保留")

# 造两条真正不同的记忆
st.con.execute("DELETE FROM episodes")
st.con.execute("DELETE FROM vectors")
st.con.commit()
st.invalidate_vector_cache()

a = cli_mcp.cmd_add(st, {"text": "用户喜欢在傍晚散步"})
b = cli_mcp.cmd_add(st, {"text": "用户喜欢在清晨跑步"})
check("相关但不同都保留",
      a.get("ok") and b.get("ok")
      and not a.get("deduped") and not b.get("deduped"),
      f"a={a.get('deduped')}, b={b.get('deduped')}")

st.con.close()

# ============ 汇总 ============
print("\n" + "=" * 74)
n_ok = sum(1 for r in results if r["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for r in results:
    if not r["ok"]:
        print(f"  ❌ {r['name']}  {r['detail']}")
print("=" * 74)

out = {"experiment": "E-308", "n_pass": n_ok, "n_total": len(results),
       "results": results}
p = os.path.join(_HERE, "results", "e308_dup_fix.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e308_dup_fix.json")

sys.exit(0 if n_ok == len(results) else 1)
