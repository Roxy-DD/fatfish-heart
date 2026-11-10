"""E-337 查：为什么 st.injected 被反复重置导致重复注入？

E-336 数据：
  · 自动注入 26 次（设计上应该每会话只 1 次）
  · 全部用同一句旧查询

设计：
  if (st.injected) { 只补常驻指令，不再检索 }
  st.injected = true

如果反复注入，说明 st.injected 被重置了。

可能原因：
  A. agent/disposed 事件频繁触发 → sessionsMap().delete(sid) → 状态丢失
  B. 每次插件重载 → sessions 被清空（但 E-307 已改成 globalThis）
  C. sid 每次不同 → 不同 session 各自注入一次（这可能是正常的）
"""
import sys
import os
import json
import io
import warnings
from collections import Counter

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

log_path = os.path.join(MEM, "plugin-debug.jsonl")

print("=" * 76)
print("E-337 查 injected 反复重置的原因")
print("=" * 76)


def read_all(kinds, tail=1500000):
    out = []
    with io.open(log_path, "r", encoding="utf-8", errors="replace") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - tail))
        for line in f:
            try:
                e = json.loads(line)
                if e.get("kind") in kinds:
                    out.append(e)
            except Exception:
                pass
    return out


# ============ ① inject 事件的 sid 分布 ============
print("\n① inject 事件按 sid 分布")

injects = read_all(["inject"])
print(f"   inject 日志: {len(injects)} 条")

sids = Counter(str(e.get("sid", ""))[:25] for e in injects)
print(f"\n   不同 sid: {len(sids)}")
for sid, cnt in sids.most_common():
    print(f"     {cnt:>4} 次  {sid}")

# ============ ② assemble 事件的 sid 分布 ============
print("\n② assemble 事件按 sid")

assembles = read_all(["assemble"])
asids = Counter(str(e.get("sid", ""))[:25] for e in assembles)
print(f"   assemble 日志: {len(assembles)} 条")
for sid, cnt in asids.most_common():
    print(f"     {cnt:>4} 次  {sid}")

# ============ ③ 检查 agent/disposed ============
print("\n③ agent 生命周期事件")

lifecycle = read_all(["agent-disposed", "lifecycle-attached",
                      "assemble-enter", "assemble-enter-error"])
print(f"   相关事件: {len(lifecycle)} 条")
for e in lifecycle[-10:]:
    print(f"     {e.get('t','')[:19]}  kind={e.get('kind')}")

# ============ ④ 关键：同一 sid 内 inject 次数 ============
print("\n④ 同一 sid 内的 inject 次数")

for sid, cnt in sids.most_common(3):
    print(f"\n   sid={sid}")
    items = [e for e in injects if str(e.get("sid", ""))[:25] == sid]
    print(f"     注入 {len(items)} 次")
    for it in items[:6]:
        print(f"       {it.get('t','')[:19]}  "
              f"query='{str(it.get('query',''))[:40]}'")

# ============ ⑤ 检查是否有 reset 迹象 ============
print("\n⑤ 检查是否有 session 被清除")

# agent/disposed 会导致 sessionsMap().delete(sid)
disposed = read_all(["agent-disposed"])
print(f"   agent-disposed: {len(disposed)} 条")

if disposed:
    print("   最近的 disposed:")
    for e in disposed[-5:]:
        print(f"     {e.get('t','')[:19]}  sid={str(e.get('sid',''))[:30]}")
else:
    print("   没有 disposed 记录（说明这个事件没被记录，或者没触发）")

# ============ ⑥ 判断 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

print(f"""
  inject 总次数: {len(injects)}
  不同 sid: {len(sids)}

  如果同一 sid 注入了多次，说明 st.injected 被重置了。
""")

if len(sids) > 0:
    max_sid, max_cnt = sids.most_common(1)[0]
    if max_cnt > 1:
        print(f"""
  ⚠ 同一 sid（{max_sid}）注入了 {max_cnt} 次

  这说明 st.injected 确实被反复重置。

  最可能的原因：
    · 每次插件重载（我做了很多次 selfreload）
    · sessions 挂在 globalThis 上，重载后应该保留
    · 但 **apply() 被重新执行** 时，可能有其他清空路径

  需要检查：agent/disposed 是否在每次重载时触发
""")
    else:
        print(f"\n  ✅ 每个 sid 只注入 1 次（正常）")

out = {
    "experiment": "E-337",
    "inject_total": len(injects),
    "n_sids": len(sids),
    "max_per_sid": sids.most_common(1)[0][1] if sids else 0,
}
p = os.path.join(_HERE, "results", "e337_injected_reset.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e337_injected_reset.json")
