"""E-359 验证 E-348/350 注入修复在真实会话中生效

E-348 修：首轮判断顺序（isFirstTurn）
E-350 修：search_scoped 透传 flag + L0/L1 不占 top 名额

关键验证：**新的 inject 记录里 protected_count 应该不再为 0**

历史数据（E-349）：
  28 次注入，protected_count 全部 = 0

如果修复生效，新记录里 protected_count 应该 = 11（L0/L1 数量）。
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

print("=" * 76)
print("E-359 验证注入修复在真实会话生效")
print("=" * 76)

con = sqlite3.connect(DB)
rows = con.execute(
    "SELECT seq, at, msg, extra FROM audit_log "
    "WHERE kind='inject' ORDER BY at").fetchall()
con.close()

print(f"\n① inject 记录总数: {len(rows)}")

# ---------- ② 解析 protected_count ----------
parsed = []
for seq, at, msg, extra in rows:
    ex = {}
    try:
        ex = json.loads(extra or "{}")
    except Exception:
        pass
    parsed.append({
        "seq": seq, "at": at,
        "protected": ex.get("protected_count"),
        "chars": ex.get("content_chars"),
        "query": str(ex.get("query") or "")[:32],
    })

with_pc = [p for p in parsed if isinstance(p["protected"], int)]
print(f"   有 protected_count: {len(with_pc)}")

if with_pc:
    vals = [p["protected"] for p in with_pc]
    from collections import Counter
    dist = Counter(vals)
    print(f"\n   protected_count 分布:")
    for v, n in sorted(dist.items()):
        mark = "  ← 修复前（全 0）" if v == 0 else ("  ← 正常" if v > 0 else "")
        print(f"     {v:>3} 出现 {n:>2} 次{mark}")

    # 按时间看变化
    print(f"\n   全部记录（按时间）:")
    print(f"   {'时间':<18} {'protected':>10} {'chars':>7}  query")
    print("   " + "-" * 70)
    for p in with_pc:
        t = time.strftime("%m-%d %H:%M", time.localtime(p["at"])) if p["at"] else "?"
        mark = "⚠" if p["protected"] == 0 else "✅"
        print(f"   {t:<18} {mark}{p['protected']:>9} "
              f"{str(p['chars']):>7}  {p['query']}")

# ---------- ③ 判定 ----------
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

n_zero = sum(1 for p in with_pc if p["protected"] == 0)
n_pos = sum(1 for p in with_pc if p["protected"] > 0)

print(f"""
  protected_count = 0 的: {n_zero} 次
  protected_count > 0 的: {n_pos} 次
""")

if n_pos > 0:
    print(f"""
  ✅ 修复生效

  有 {n_pos} 次注入带上了 L0/L1（protected_count > 0）。
  说明 search_scoped → cmd_search → 插件的 flag 链路已打通。
""")
    verdict = "fixed"
elif n_zero == len(with_pc) and len(with_pc) > 0:
    print("""
  ❌ 仍然是 0

  所有记录 protected_count 都是 0。
  可能：
    · 修复后还没有新的注入发生（旧记录占满）
    · 或者修复没真正生效
""")
    verdict = "still_zero"
else:
    print("   数据不足")
    verdict = "unknown"

# ---------- ④ 检查最后一次注入的时间 ----------
if with_pc:
    last = with_pc[-1]
    last_t = time.strftime("%Y-%m-%d %H:%M:%S",
                           time.localtime(last["at"])) if last["at"] else "?"
    print(f"\n   最后一次注入: {last_t}  protected={last['protected']}")
    print(f"   （E-350 修复提交于 2026-11-10 约 14:30，重载后生效）")

out = {
    "experiment": "E-359",
    "total": len(rows),
    "n_zero": n_zero,
    "n_pos": n_pos,
    "verdict": verdict,
}
p = os.path.join(_HERE, "results", "e359_inject_verify.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e359_inject_verify.json")
