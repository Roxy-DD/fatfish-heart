"""E-375 澄清：零注入可能不是 bug，是我的判据错了

E-374 发现 2.4 小时零注入，当时判为「确认异常」。
但现在读代码发现：

    if (st.injected || st.injecting) {
        // 已注入过，只补常驻指令，不再检索
        return { ...result, contexts: [...contexts, memoryPolicy] }
    }
    ...
    st.injected = true

**「每个会话只注入一次」是设计意图。**

所以：
  · 当前会话（session-7413467a）早就注入过了
  · 之后 2.4 小时零注入是**完全正常的**
  · 我 E-374 的「确认异常」判断是错的

★ 这是第六次同样的错误：看到「零」就判异常。

正确的判据应该是：
  · 新会话开始时是否注入了一次
  · 那一次注入是否带上了 L0/L1（protected_count > 0）

查：当前会话是什么时候注入的？那次是修复前还是修复后？
"""
import sys
import os
import json
import time
import warnings
import sqlite3

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
DB = os.path.join(MEM, "dsh_mem.db")
LOG = os.path.join(MEM, "plugin-debug.jsonl")

print("=" * 78)
print("E-375 零注入是否正常 —— 按会话边界重新判定")
print("=" * 78)

# ---------- ① 每个会话的注入记录 ----------
print("\n① 每会话注入次数（设计上应各 1 次）")

con = sqlite3.connect(DB)
rows = con.execute(
    "SELECT at, sid, extra FROM audit_log WHERE kind='inject' "
    "ORDER BY at").fetchall()
con.close()

from collections import defaultdict
by_sid = defaultdict(list)
for at, sid, extra in rows:
    by_sid[str(sid or "(空)")].append((at, extra))

print(f"   总注入 {len(rows)} 条，涉及 {len(by_sid)} 个会话")
for sid, items in sorted(by_sid.items(),
                         key=lambda x: -len(x[1])):
    print(f"\n   sid={sid[:30]}  {len(items)} 次")
    for at, extra in items[-3:]:
        t = time.strftime("%m-%d %H:%M", time.localtime(at)) if at else "?"
        d = {}
        try:
            d = json.loads(extra or "{}")
        except Exception:
            pass
        print(f"     {t}  protected={d.get('protected_count')} "
              f"chars={d.get('content_chars')}")

# ---------- ② 当前会话最后一次注入是什么时候 ----------
print("\n② 当前会话的注入历史")

CUR = "session-7413467a"
cur_items = [(at, ex) for sid, items in by_sid.items()
             if CUR in sid for at, ex in items]
cur_items.sort()

if cur_items:
    last_at, last_ex = cur_items[-1]
    d = {}
    try:
        d = json.loads(last_ex or "{}")
    except Exception:
        pass
    print(f"   最后注入: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(last_at))}")
    print(f"   protected_count: {d.get('protected_count')}")
    print(f"   content_chars:   {d.get('content_chars')}")
    print(f"   距今: {(time.time() - last_at)/3600:.1f} 小时")

    FIX_TIME = time.mktime(time.strptime("2026-11-10 14:30:00",
                                         "%Y-%m-%d %H:%M:%S"))
    if last_at < FIX_TIME:
        print(f"\n   ⚠ 这次注入在 E-350 修复**之前**")
        print(f"     所以 protected_count=0 是预期的（修复前就是 0）")
        print(f"     要验证修复，需要**新会话**")
    else:
        print(f"\n   ✅ 这次注入在修复之后")
        print(f"     如果 protected_count>0 → 修复生效")
else:
    print("   未找到当前会话的注入记录")

# ---------- ③ 所有会话的最后注入时间 ----------
print("\n③ 各会话最后注入时间 vs 修复时间")

FIX_TIME = time.mktime(time.strptime("2026-11-10 14:30:00",
                                     "%Y-%m-%d %H:%M:%S"))
for sid, items in by_sid.items():
    last_at = max(at for at, _ in items if at)
    mark = "修复前" if last_at < FIX_TIME else "✅ 修复后"
    print(f"   {time.strftime('%m-%d %H:%M', time.localtime(last_at))}  "
          f"{mark}  sid={sid[:28]}")

# ---------- ④ 结论 ----------
print("\n" + "=" * 78)
print("结论")
print("=" * 78)

n_after_fix = sum(1 for sid, items in by_sid.items()
                  if max(at for at, _ in items if at) > FIX_TIME)

print(f"""
  会话数: {len(by_sid)}
  修复后有过注入的会话: {n_after_fix}

  ★ 关键澄清：

    「2.4 小时零注入」**不是 bug**，是设计意图：
      st.injected 一旦为 true，该会话就不再注入。

    我 E-374 把「零」判为异常，是**第六次同样的错误**
    （看到异常数字就归因，没先确认这个数字的边界语义）。

  ★ 要验证 E-350 修复是否生效，唯一正确的方法是：
    **开一个新会话**，看它第一次注入的 protected_count 是否 > 0。

    当前会话无法验证（它早就注入过了）。
""")

out = {
    "experiment": "E-375",
    "n_sessions": len(by_sid),
    "n_inject": len(rows),
    "n_after_fix": n_after_fix,
    "verdict": "零注入是设计意图，非 bug；需新会话才能验证修复",
}
p = os.path.join(_HERE, "results", "e375_clarify.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e375_clarify.json")
