"""E-412 回归：实验执行包装器

E-410 建了 exp_run.py，E-411 验证了成功/失败两条路径。
本测试固化：

  ① 成功执行记录为 exec_ok，含耗时和输出行数
  ② 失败执行记录为 exec_fail，含退出码和报错摘要
  ③ 脚本不存在的记录为 file_missing
  ④ 记录不阻断实验本身（失败照样跑，记录失败不影响返回码）
"""
import sys
import os
import subprocess
import time
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

from store_v2 import MemoryStore
import environment as env

DB = os.path.join(MEM, "dsh_mem.db")
EXP_RUN = os.path.join(_HERE, "exp_run.py")

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


def run_exp(script, timeout=120):
    """用 exp_run 跑一个脚本，返回 (returncode, output)"""
    r = subprocess.run(
        [sys.executable, "-u", EXP_RUN, script],
        cwd=_HERE, capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def events_for(source_kw, limit=2000):
    """按 source 关键字取环境事件

    E-486：limit 从 50/100 提到 2000

    原 limit=100 会**悄悄漏掉**较早的记录：
    生产库已有 123 条环境事件，recent(limit=100) 只取回最近 100 条，
    最早的 23 条永远看不到。

    于是「before/after 计数」在库变大后开始假失败：
      before = 100（窗口外旧记录被丢掉）
      after  = 100（新记录进来，最老的又被挤出窗口）
      -> 计数不变 -> 断言失败

    实测（E-486）：
      总行数 123
      limit=50  -> 取回 50，其中 e408 29 条
      limit=100 -> 取回 100，其中 e408 54 条
      limit=200 -> 取回 121，其中 e408 63 条

    这是「测试假设数据库不会变大」的典型错误。
    """
    st = MemoryStore(DB)
    out = [e for e in env.recent(st, limit=limit)
           if source_kw in str(e.get("source"))]
    st.con.close()
    return out


print("=" * 74)
print("E-412 exp_run 包装器回归")
print("=" * 74)

# ---------- ① 成功路径 ----------
print("\n【1】成功路径")

# 找一个会成功且很快的脚本
OK_SCRIPT = "run_e408_hygiene.py"
before = len(events_for("exp_run.py " + OK_SCRIPT, 2000))

code, out = run_exp(OK_SCRIPT)
check("脚本执行成功", code == 0, f"exit={code}")

time.sleep(0.3)
evs = events_for("exp_run.py " + OK_SCRIPT, 2000)
check("记录了新的环境事件", len(evs) > before,
      f"{before} → {len(evs)}")

if evs:
    latest = evs[0]
    check("kind 是 exec_ok", latest["kind"] == "exec_ok", latest["kind"])
    check("outcome 含 exit=0", "exit=0" in str(latest["outcome"]),
          str(latest["outcome"]))
    check("outcome 含耗时", "s" in str(latest["outcome"]),
          str(latest["outcome"]))
    check("detail 含行数", "行输出" in str(latest["detail"]),
          str(latest["detail"]))

# ---------- ② 失败路径 ----------
print("\n【2】失败路径")

# E-412 修：测试自己创建失败脚本，不依赖外部文件
# 原写法依赖 _test_fail.py，一旦那个文件被清理，回归就失败。
FAIL_SCRIPT = "_tmp_e412_fail.py"
FAIL_PATH = os.path.join(_HERE, FAIL_SCRIPT)
FAIL_CODE = '''import sys

def main():
    print("E-412 临时失败脚本")
    data = {"a": 1}
    print(data["missing_key"])
    return 0

if __name__ == "__main__":
    sys.exit(main())
'''

created_fail = False
try:
    with open(FAIL_PATH, "w", encoding="utf-8") as f:
        f.write(FAIL_CODE)
    created_fail = True
except Exception as e:
    check("能创建失败测试脚本", False, str(e))

if created_fail:
    before_f = len(events_for("exp_run.py " + FAIL_SCRIPT, 2000))
    code_f, out_f = run_exp(FAIL_SCRIPT)
    check("脚本执行失败（预期）", code_f != 0, f"exit={code_f}")

    time.sleep(0.3)
    evs_f = events_for("exp_run.py " + FAIL_SCRIPT, 2000)
    check("记录了失败事件", len(evs_f) > before_f,
          f"{before_f} → {len(evs_f)}")

    if evs_f:
        latest_f = evs_f[0]
        check("kind 是 exec_fail",
              latest_f["kind"] == "exec_fail", latest_f["kind"])
        check("outcome 含退出码",
              "exit=" in str(latest_f["outcome"]),
              str(latest_f["outcome"]))
        check("detail 含报错信息",
              any(k in str(latest_f["detail"])
                  for k in ("Error", "KeyError", "Traceback",
                            "Exception")),
              str(latest_f["detail"])[:60])
else:
    code_f = 1
    check("失败路径测试", False, "无法创建测试脚本")

# ---------- ③ 脚本不存在 ----------
print("\n【3】脚本不存在")

# 注意：source 里写的是完整脚本名 __no_such_script__.py
# （E-412 第一次跑时我把检查关键词写成了 __no_such__，不匹配，误判为失败）
MISS_KW = "__no_such_script__"
before_m = len(events_for(MISS_KW, 2000))
code_m, out_m = run_exp("__no_such_script__.py")
check("返回非 0", code_m != 0, f"exit={code_m}")

time.sleep(0.3)
evs_m = events_for(MISS_KW, 2000)
check("记录了 file_missing 事件", len(evs_m) > before_m,
      f"{before_m} → {len(evs_m)}")
if evs_m:
    check("kind 是 exec_fail",
          evs_m[0]["kind"] == "exec_fail", evs_m[0]["kind"])
    check("outcome 标明 file_missing",
          "file_missing" in str(evs_m[0]["outcome"]),
          str(evs_m[0]["outcome"]))

# ---------- ④ 记录不阻断实验 ----------
print("\n【4】记录不阻断实验")

# 实验的成功/失败返回码应原样传递
check("成功脚本返回 0", code == 0)
check("失败脚本返回非 0（原样传递）", code_f != 0, f"exit={code_f}")

# ---------- 清理测试数据 ----------
print("\n【5】清理测试数据")

st = MemoryStore(DB)
n_before = st.con.execute(
    "SELECT COUNT(*) FROM environment_events").fetchone()[0]
st.con.execute(
    "DELETE FROM environment_events WHERE source LIKE ? "
    "OR source LIKE ?",
    ("%" + FAIL_SCRIPT + "%", "%__no_such_script__%"))
st.con.commit()
n_after = st.con.execute(
    "SELECT COUNT(*) FROM environment_events").fetchone()[0]
print(f"   清理 {n_before - n_after} 条测试记录")
st.con.close()

# 删除临时失败脚本（测试自己清理，不依赖外部）
if created_fail and os.path.exists(FAIL_PATH):
    try:
        os.remove(FAIL_PATH)
        print(f"   已删除临时脚本 {FAIL_SCRIPT}")
    except Exception as e:
        print(f"   ⚠ 删除临时脚本失败: {e}")

# ---------- 汇总 ----------
print("\n" + "=" * 74)
n_ok = sum(1 for x in results if x["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for x in results:
    if not x["ok"]:
        print(f"  ❌ {x['name']}  {x['detail']}")
print("=" * 74)

print(f"""
  ★ 包装器解决了 E-409 发现的缺口：
    之前单跑实验脚本完全不记录，
    现在用 exp_run.py 跑就会自动落环境事件。

  ★ 失败路径的记录最有价值：
    detail 里存了报错摘要（如 KeyError: 'nonexistent_key'），
    以后可以回答「哪类错误反复出现」。
""")

import json
out_data = {
    "experiment": "E-412",
    "n_pass": n_ok,
    "n_total": len(results),
}
p = os.path.join(_HERE, "results", "e412_exp_run_reg.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out_data, f, ensure_ascii=False, indent=1)
print(f"写入 results/e412_exp_run_reg.json")

sys.exit(0 if n_ok == len(results) else 1)
