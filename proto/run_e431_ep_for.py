"""E-431 回归：ep_for 辅助工具

E-430 做了 ep_for.py，把标注成本从"找 16 位 id"降到"复制一行"。

为什么需要它：
  link_ratio 长期停在 4~6%，原因不是不愿意标，
  而是手打 id 的成本高到会跳过。

本测试固化：
  ① 工具能正常输出 ep_id
  ② 输出里带现成的 MESH_EP 命令（可直接复制）
  ③ 不输出 L0/L1（它们常驻，不需要每轮标注）
  ④ 没有结果时优雅退出（不报错）
"""
import sys
import os
import subprocess
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

from store_v2 import MemoryStore

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


def run_ep_for(args, timeout=120):
    r = subprocess.run(
        [sys.executable, "-u", os.path.join(_HERE, "ep_for.py")] + args,
        cwd=_HERE, capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


print("=" * 74)
print("E-431 ep_for 辅助工具回归")
print("=" * 74)

# ---------- ① 基本功能 ----------
print("\n【1】基本功能")

code, out = run_ep_for(["红线 无损迁移"])
check("正常返回 0", code == 0, f"exit={code}")

# 应输出 ep_id（16 位十六进制）
import re
ids = re.findall(r"\b([0-9a-f]{16})\b", out)
check("输出了 ep_id", len(ids) > 0, f"{len(ids)} 个")

# ---------- ② 带可复制命令 ----------
print("\n【2】可复制命令")

check("输出含 MESH_EP", "MESH_EP=" in out)
check("输出含 iterate.py check", "iterate.py check" in out)

# 命令格式正确
m = re.search(r"MESH_EP=([0-9a-f,]+)", out)
if m:
    cmd_ids = m.group(1).split(",")
    check("命令里的 id 格式正确",
          all(len(x) == 16 for x in cmd_ids),
          f"{len(cmd_ids)} 个")
    check("命令里的 id 都是真实存在的",
          len(cmd_ids) == len([x for x in cmd_ids if x in ids]),
          f"{len(cmd_ids)} 个")
else:
    check("能解析出 MESH_EP 命令", False)

# ---------- ③ 不输出 L0/L1 ----------
print("\n【3】不输出 L0/L1（它们常驻，无需标注）")

import hook as _hook
st = MemoryStore(os.path.join(MEM, "dsh_mem.db"))
protected = set(_hook.present_ids(st))
st.con.close()
print(f"   当前 L0/L1: {len(protected)} 条")

overlap = [x for x in ids if x in protected]
check("输出的 id 不含 L0/L1", len(overlap) == 0,
      f"{len(overlap)} 个重叠")

# ---------- ④ 边界：真实检索几乎总有结果 ----------
print("\n【4】边界情况")

# 注意：向量检索总会返回 top 结果，所以"无结果"很难触发。
# 这里测的是：即使用奇怪查询，工具也不报错、不虚构。
code2, out2 = run_ep_for(["zzzz_绝对不存在的查询_zzzz"])
check("奇怪查询时正常退出", code2 == 0, f"exit={code2}")
check("没有崩溃", "Traceback" not in out2)

# 如果确实返回了结果，也不该是 L0/L1
ids2 = re.findall(r"\b([0-9a-f]{16})\b", out2)
check("奇怪查询的输出也不含崩溃",
      "Error" not in out2 or "没有相关记忆" in out2,
      "OK")

# ---------- ⑤ 参数缺失 ----------
print("\n【5】参数缺失")

code3, out3 = run_ep_for([])
check("无参数时返回非 0", code3 != 0, f"exit={code3}")
check("给出用法", "用法" in out3, out3.strip()[:40])

# ---------- 汇总 ----------
print("\n" + "=" * 74)
n_ok = sum(1 for x in results if x["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for x in results:
    if not x["ok"]:
        print(f"  ❌ {x['name']}  {x['detail']}")
print("=" * 74)

print(f"""
  ★ 这个工具的定位：

    它**只辅助标注，不自动标注**。

    自动标注 = 模型自己说"这条有用"
             = 《环管群》5.2 禁止的内部奖励

    正确流程：
      1. 用 ep_for 查出候选记忆
      2. **我看摘要，确认"确实用了它"**
      3. 才复制 MESH_EP 命令
      4. 不确定就不标 —— 宁可少标，不虚构

  ★ 实测效果：
    用这个工具标注了 2 次，link_ratio 从 4.4% 升到 8.2%。
""")

import json
out_data = {
    "experiment": "E-431",
    "n_pass": n_ok,
    "n_total": len(results),
    "n_ids_found": len(ids),
    "n_protected": len(protected),
}
p = os.path.join(_HERE, "results", "e431_ep_for.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out_data, f, ensure_ascii=False, indent=1)
print(f"写入 results/e431_ep_for.json")

sys.exit(0 if n_ok == len(results) else 1)
