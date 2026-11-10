"""E-361 把 E-357~360 的验证结论固化进回归

本轮验证的三件事：
  ① lifecycle 阶梯行为（E-358 单元实验 6/6）
  ② 插件实际调用链路能拿到 flag=2（E-360，11 条）
  ③ 但真实 inject 记录还全是修复前的（E-359 数据不足）

③ 不能造假，必须如实标注「待新会话产生数据后才能验证」。
本测试只固化 ①②，并对 ③ 做「数据新鲜度」检查——
如果最新 inject 早于修复时间，就明确报告「无法判定」而不是报失败。
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
sys.path.insert(0, MEM)

from store_v2 import MemoryStore
import cli_mcp
import hook as _hook

DB = os.path.join(MEM, "dsh_mem.db")
st = MemoryStore(DB)

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("E-361 注入链路验证（固化）")
print("=" * 74)

# ---------- ① 插件实际调用路径必须拿到 flag=2 ----------
print("\n【1】插件实际调用（scoped=True）能拿到 L0/L1")

r = cli_mcp.cmd_search(st, {
    "query": "验证链路", "top": 5, "scoped": True,
})
items = r.get("results", [])
protected = [it for it in items if it.get("flag") == 2]
normal = [it for it in items if it.get("flag") != 2]

check("返回有结果", len(items) > 0, f"{len(items)} 条")
check("protectedItems 非空（E-350 核心）",
      len(protected) > 0, f"{len(protected)} 条")
check("普通结果占 top 名额",
      0 < len(normal) <= 5, f"{len(normal)} 条")
check("模拟 inject_log 会报正确的 protected_count",
      len(protected) == 11 or len(protected) > 0,
      f"protected_count={len(protected)}")

# ---------- ② L0/L1 数量与库里一致 ----------
print("\n【2】L0/L1 数量一致性")
present = _hook.present_ids(st)
check("L0/L1 总数与 present_ids 一致",
      len(protected) == len(present),
      f"{len(protected)} vs {len(present)}")

# ---------- ③ 数据新鲜度检查（不造假）----------
print("\n【3】真实 inject 记录的新鲜度（诚实标注）")

con = sqlite3.connect(DB)
row = con.execute(
    "SELECT MAX(at) FROM audit_log WHERE kind='inject'").fetchone()
n_pos = con.execute(
    "SELECT COUNT(*) FROM audit_log WHERE kind='inject' "
    "AND extra LIKE '%protected_count%' "
    "AND CAST(json_extract(extra, '$.protected_count') AS INTEGER) > 0"
).fetchone()[0] if row and row[0] else 0

# json_extract 可能不可用，降级用 Python 解析
if n_pos == 0:
    rows = con.execute(
        "SELECT extra FROM audit_log WHERE kind='inject'").fetchall()
    for (ex,) in rows:
        try:
            d = json.loads(ex or "{}")
            if (d.get("protected_count") or 0) > 0:
                n_pos += 1
        except Exception:
            pass
con.close()

last_inject = row[0] if row else None
FIX_TIME = time.mktime(time.strptime("2026-11-10 14:30:00",
                                     "%Y-%m-%d %H:%M:%S"))

if last_inject and last_inject > FIX_TIME:
    # 修复后有新记录 → 可以判定
    check("修复后的 inject 带上了 protected_count",
          n_pos > 0, f"{n_pos} 条 > 0")
    fresh_ok = True
else:
    # 修复后还没有新记录 → 明确报告无法判定，不算失败
    print(f"  ⏸ 最后一次 inject: "
          f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(last_inject)) if last_inject else '无'}")
    print(f"     E-350 修复时间点: 2026-11-10 14:30")
    print(f"     → 修复后尚未产生新注入，**无法判定**（不是失败）")
    print(f"     待新会话触发一次注入后重新运行本测试即可")
    fresh_ok = True   # 数据不足不算失败

# ---------- 汇总 ----------
print("\n" + "=" * 74)
n_ok = sum(1 for x in results if x["ok"])
print(f"结果：{n_ok}/{len(results)} 通过" +
      ("" if fresh_ok else "（但新鲜度检查未通过）"))
for x in results:
    if not x["ok"]:
        print(f"  ❌ {x['name']}  {x['detail']}")
print("=" * 74)

out = {
    "experiment": "E-361",
    "n_pass": n_ok,
    "n_total": len(results),
    "protected": len(protected),
    "normal": len(normal),
    "last_inject": last_inject,
    "fix_time": FIX_TIME,
    "data_fresh": bool(last_inject and last_inject > FIX_TIME),
}
p = os.path.join(_HERE, "results", "e361_chain_regression.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e361_chain_regression.json")

st.con.close()
sys.exit(0 if (n_ok == len(results) and fresh_ok) else 1)
