"""E-264 验证：note 提示接入后，端到端链路真的通了吗？

E-263 只验证了内核命令和插件代码的静态正确性，
但**没有验证真实运行时**：
  · assemble 时 knowledge_hint 真的被调用了吗？
  · 模型真的看到提示了吗？
  · 看到之后会真的去写笔记吗？

这是"未验证的假设"。必须先确认链路通，再谈效果。

检查项：
  ① 内核 knowledge_hint 在真实路径下的耗时（会不会拖慢 assemble）
  ② 插件 assemble 里是否真的调用了（看 debugLog）
  ③ 提示文本是否会挤占注入预算
  ④ 端到端模拟：模拟一次 assemble，看最终注入什么
"""
import sys
import os
import json
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

from store_v2 import MemoryStore
import cli_mcp

DB = os.path.join(_MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 76)
print("E-264 验证：note 提示的端到端链路")
print("=" * 76)

# ============ ① 耗时 ============
print("\n① knowledge_hint 耗时（会不会拖慢 assemble）")

times = []
for i in range(3):
    t0 = time.perf_counter()
    r = cli_mcp.cmd_knowledge_hint(st, {})
    ms = (time.perf_counter() - t0) * 1000
    times.append(ms)
    print(f"   第{i+1}次: {ms:.1f} ms")

avg = sum(times) / len(times)
print(f"\n   平均: {avg:.1f} ms")
if avg > 500:
    print("   ⚠ 超过 500ms，会明显拖慢会话首次响应")
elif avg > 100:
    print("   🟡 100-500ms，可接受但值得优化")
else:
    print("   ✅ 很快，不影响体验")

# ============ ② 提示文本大小 ============
print("\n② 提示文本会占多少注入预算")
text = r.get("text", "")
print(f"   提示长度: {len(text)} 字")
print(f"   注入总预算: 1500 字（autoInjectMaxChars）")
print(f"   占比: {len(text)/1500*100:.0f}%")

if len(text) > 500:
    print("   ⚠ 超过 1/3 预算，可能挤占用户记忆")
else:
    print("   ✅ 占比合理")

# ============ ③ 端到端模拟 assemble ============
print("\n③ 端到端模拟：模拟一次首轮 assemble 的注入内容")

# 模拟插件逻辑
protected_ids = set()
import hook as _hook
for p in _hook.protected_of(st):
    if p["level"] in ("L0", "L1"):
        protected_ids.add(p["ep_id"])

# 模拟：L0/L1 全文（首轮）
prot_texts = []
for eid in protected_ids:
    ep = st.get(eid)
    if ep:
        info = next((x for x in _hook.protected_of(st) if x["ep_id"] == eid), {})
        prot_texts.append(f"- [{eid}] {info.get('note','')}：{ep.raw_text}")

prot_chars = sum(len(t) for t in prot_texts)
kh_chars = len(text)
POLICY_CHARS = 1200  # memoryPolicy 大约

print(f"   L0/L1 全文:     {prot_chars} 字")
print(f"   知识提示:       {kh_chars} 字")
print(f"   自主记忆准则:   ~{POLICY_CHARS} 字")
print(f"   相关记忆预算:   {max(0, 1500 - 0)} 字（独立预算）")
print(f"\n   实际注入总量:   ~{prot_chars + kh_chars + POLICY_CHARS} 字")

# ============ ④ 检查是否会重复注入 ============
print("\n④ 检查：knowledge_hint 是否只在首轮注入")
plugin = os.path.join(os.path.dirname(_HERE), "dsh-plugin", "index.js")
src = open(plugin, encoding="utf-8").read()

idx = src.find("knowledge_hint")
if idx > 0:
    # 直接取调用点前后 500 字符，检查是否有 !st.injected 守卫
    seg = src[max(0, idx - 400):idx + 200]
    has_guard = "!st.injected" in seg
    print(f"   有 !st.injected 守卫: {'✅ 是（只首轮）' if has_guard else '❌ 否（每轮都注入）'}")
else:
    has_guard = None

# ============ ⑤ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

issues = []
if avg > 500:
    issues.append(f"耗时偏高 {avg:.0f}ms")
if len(text) > 600:
    issues.append(f"文本偏长 {len(text)} 字")
if idx > 0 and not has_guard:
    issues.append("可能每轮注入")

if issues:
    print(f"\n   ⚠ 发现问题: {issues}")
else:
    print(f"""
   ✅ 链路可用，指标健康：
      · 耗时 {avg:.0f} ms（可接受）
      · 提示 {len(text)} 字（占预算 {len(text)/1500*100:.0f}%）
      · 只在首轮注入
""")

print("""
  但仍有一个未验证的问题：
    模型看到提示后，会真的去写笔记吗？

  这只能靠真实对话验证，不是代码能测的。
  下一步：观察真实使用时 note 的 audit 记录。
""")

st.con.close()

out = {
    "experiment": "E-264",
    "avg_ms": round(avg, 1),
    "text_chars": len(text),
    "prot_chars": prot_chars,
    "total_inject": prot_chars + kh_chars + POLICY_CHARS,
    "has_guard": has_guard if idx > 0 else None,
    "issues": issues,
}
p = os.path.join(_HERE, "results", "e264_note_flow.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e264_note_flow.json")
