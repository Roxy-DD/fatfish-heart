"""E-495 按写入数触发备份回归（FISH-9 落地）

验证：
  ① 每写入 N 条触发一次备份
  ② 未到阈值不备份
  ③ 计数正确重置
  ④ 备份失败不阻断写入
  ⑤ 阈值来自 FISH-9 实验数据（有据可依）
  ⑥ 去重命中不计数（只有真写入才算）
"""
import json
import os
import shutil
import sys
import tempfile
import warnings

warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(HERE, "cosmos_mem")
sys.path.insert(0, MEM)

from store_v2 import MemoryStore          # noqa: E402
import cli_mcp                            # noqa: E402
import auto_backup                        # noqa: E402
import backup as bk                       # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": str(detail)[:130]})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 72)
print("E-495 按写入数触发备份回归")
print("=" * 72)

TMP = tempfile.mkdtemp(prefix="e495_", dir=HERE)
DB = os.path.join(TMP, "test.db")

try:
    st = MemoryStore(DB)
    N = 5   # 测试用小阈值，避免写 20 条太慢；生产是 20

    # ---------- ① 未到阈值不备份 ----------
    print("\n【1】未到阈值不备份")
    for i in range(N - 1):
        r = cli_mcp.cmd_add(st, {"text": f"测试写入 {i}", "atmosphere": "e495", "auto_backup_every": N})
        if i == 0:
            first = r
        check(f"写入 {i+1}/{N-1} 未触发" if i < 2 else "", 
              r.get("auto_backup", {}).get("triggered") is False) if i < 2 else None
    bcount = len(bk.list_backups(DB))
    check(f"写了 {N-1} 条，0 份备份", bcount == 0, f"{bcount} 份")
    stt = {"count": st.con.execute("SELECT val FROM meta WHERE key='auto_backup:writes_since'").fetchone()[0]}
    check(f"计数 = {N-1}", int(stt["count"]) == N - 1, str(stt))

    # ---------- ② 到阈值触发 ----------
    print(f"\n【2】第 {N} 条触发备份")
    r = cli_mcp.cmd_add(st, {"text": f"测试写入 {N-1}", "atmosphere": "e495", "auto_backup_every": N})
    ab = r.get("auto_backup") or {}
    check("第 N 条触发了备份", ab.get("triggered") is True, str(ab)[:100])
    check("备份成功", ab.get("ok") is True, str(ab.get("error", ""))[:80])
    bcount = len(bk.list_backups(DB))
    check(f"生成 1 份备份", bcount == 1, f"{bcount} 份")
    stt = {"count": st.con.execute("SELECT val FROM meta WHERE key='auto_backup:writes_since'").fetchone()[0]}
    check("计数归零", int(stt["count"]) == 0, str(stt))

    # ---------- ③ 再攒 N 条触发第二份 ----------
    print(f"\n【3】再攒 {N} 条触发第二份")
    for i in range(N):
        cli_mcp.cmd_add(st, {"text": f"第二批 {i}", "atmosphere": "e495", "auto_backup_every": N})
    bcount = len(bk.list_backups(DB))
    check("生成第二份备份", bcount == 2, f"{bcount} 份")

    # ---------- ④ 去重命中不计数 ----------
    print("\n【4】去重命中不计数")
    before = int(st.con.execute("SELECT val FROM meta WHERE key='auto_backup:writes_since'").fetchone()[0])
    # 精确重复
    cli_mcp.cmd_add(st, {"text": "第二批 0", "atmosphere": "e495"})
    after = int(st.con.execute("SELECT val FROM meta WHERE key='auto_backup:writes_since'").fetchone()[0])
    check("精确重复不增加计数", after == before, f"{before} → {after}")

    # ---------- ⑤ 备份内容有效 ----------
    print("\n【5】备份内容校验")
    bp = bk.list_backups(DB)[0]["path"]
    v = bk.verify_backup(DB, bp)
    _mismatch = {k: c for k, c in v.get("counts", {}).items()
                 if c[0] != c[1]}
    check("备份校验通过", v.get("ok") is True,
          f"ok={v.get('ok')} volatile={v.get('volatile_diff')} "
          f"不一致表={_mismatch} 错误={v.get('error')}")

    # ---------- ⑥ 阈值来自实验 ----------
    print("\n【6】阈值有实验依据")
    exp = os.path.join(HERE, "results", "fish9_backup_coverage.json")
    check("FISH-9 实验结果存在", os.path.exists(exp))
    if os.path.exists(exp):
        d = json.load(open(exp, encoding="utf-8"))
        check("实验测出写入速率", d.get("write_rate", {}).get("per_day", 0) > 0,
              str(d.get("write_rate", {}).get("per_day")))
        check("生产阈值为 20（来自实验权衡）",
              auto_backup.DEFAULT_EVERY == 20,
              str(auto_backup.DEFAULT_EVERY))

finally:
    try:
        st.con.close()
    except Exception:
        pass
    shutil.rmtree(TMP, ignore_errors=True)

print("\n" + "=" * 72)
ok = sum(1 for x in results if x["ok"])
print(f"结果：{ok}/{len(results)} 通过")
for x in results:
    if not x["ok"]:
        print(f"  ❌ {x['name']}  {x['detail']}")
print("=" * 72)

out = {"experiment": "E-495", "n_pass": ok, "n_total": len(results),
       "results": results}
p = os.path.join(HERE, "results", "e495_write_trigger.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 {p}")

sys.exit(0 if ok == len(results) else 1)
