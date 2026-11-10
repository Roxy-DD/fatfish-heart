"""E-410 实验执行包装器：让所有 run_e*.py 自动记录环境事件

E-409 发现的缺口：
  iterate.py 的回归会记录，但单跑实验脚本不会。
  这些天跑了 20+ 个实验，一条环境事件都没落。

解法：写一个包装器 exp_run.py，用它跑实验就会自动记录：
    python exp_run.py run_e411_xxx.py

★ 设计要点：
  1. **不改变实验脚本本身**——它们还是可以直接 python 跑
  2. 包装器负责：记录开始、执行、记录结果（成功/失败/超时）
  3. 记录的 outcome 是客观的（返回码、耗时、输出行数）
  4. 失败时把报错摘要写进 detail（这是最有价值的部分）

用法：
  python exp_run.py <脚本路径> [参数...]
  python exp_run.py --ep EP_ID[,EP_ID] <脚本路径> [参数...]

E-413：新增 --ep 参数，允许**人工标注**这次实验用了哪些记忆。

★ 为什么必须人工标注，不能自动推断：
  自动推断等于让模型自己说"这条记忆有用"，
  那又回到《环管群》主张5 禁止的
  「内部奖励冒充自然环境的因果筛选」。

  只有显式标注，才能区分：
    · "我跑了实验，成功了"          → 操作事实
    · "因为我用了记忆 X 才成功"      → 因果关系（需人工认定）

  前者可自动记录，后者必须人工标注。
"""
import sys
import os
import subprocess
import time
import warnings

warnings.filterwarnings("ignore")

_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")


def record_environment(kind: str, source: str, outcome: str,
                       detail: str = "", task: str = "",
                       ep_ids=None) -> None:
    """记录环境事件（失败不阻断）"""
    try:
        sys.path.insert(0, MEM)
        from store_v2 import MemoryStore
        import environment as env

        db = os.path.join(MEM, "dsh_mem.db")
        if not os.path.exists(db):
            return
        st = MemoryStore(db)
        env.init(st)
        env.record(st, kind, source=source, outcome=outcome,
                   detail=detail, task=task, ep_ids=ep_ids)
        st.con.close()
    except Exception:
        pass


def main():
    args = sys.argv[1:]
    if not args:
        print("用法: python exp_run.py [--ep ID1,ID2] <脚本路径> [参数...]")
        return 1

    # E-413：解析 --ep 参数（人工标注用了哪些记忆）
    ep_ids = []
    if args and args[0] == "--ep":
        if len(args) < 3:
            print("❌ --ep 需要跟一个 ID 列表和一个脚本路径")
            return 1
        ep_ids = [x.strip() for x in args[1].split(",") if x.strip()]
        args = args[2:]

    if not args:
        print("用法: python exp_run.py [--ep ID1,ID2] <脚本路径> [参数...]")
        return 1

    script = args[0]
    extra = args[1:]

    if not os.path.exists(script):
        print(f"❌ 脚本不存在: {script}")
        record_environment("exec_fail", f"exp_run.py {script}",
                           "file_missing", f"脚本不存在: {script}",
                           task="_exp_run", ep_ids=ep_ids)
        return 1

    # 用脚本名作为 task 标识（提取 run_eXXX 里的编号）
    base = os.path.basename(script)
    task = "_exp_run"
    import re
    m = re.search(r"e(\d{2,3})", base)
    if m:
        task = f"E-{m.group(1)}"

    print("=" * 74)
    print(f"实验执行: {base}  (task={task})")
    print("=" * 74)

    t0 = time.time()
    try:
        r = subprocess.run(
            [sys.executable, "-u", script] + extra,
            cwd=_HERE, capture_output=True, text=True,
            timeout=1800, encoding="utf-8", errors="replace")
        elapsed = time.time() - t0
        out = (r.stdout or "") + (r.stderr or "")
        code = r.returncode
    except subprocess.TimeoutExpired:
        elapsed = time.time() - t0
        out = "(超时)"
        code = -1
        record_environment("timeout", f"exp_run.py {base}",
                           f"{elapsed:.0f}s", "执行超时 1800s",
                           task=task, ep_ids=ep_ids)
        print(f"\n❌ 超时（{elapsed:.0f} 秒）")
        return 1
    except Exception as e:
        elapsed = time.time() - t0
        record_environment("exec_fail", f"exp_run.py {base}",
                           "exception", f"{type(e).__name__}: {e}",
                           task=task, ep_ids=ep_ids)
        print(f"\n❌ 异常: {type(e).__name__}: {e}")
        return 1

    # 输出最后 40 行
    lines = out.split("\n")
    print("\n".join(lines[-40:]))

    n_lines = len([l for l in lines if l.strip()])

    if code == 0:
        record_environment("exec_ok", f"exp_run.py {base}",
                           f"exit=0, {elapsed:.1f}s",
                           f"{n_lines} 行输出", task=task,
                           ep_ids=ep_ids)
        print(f"\n✅ 成功（{elapsed:.1f}s，{n_lines} 行输出）")
        if ep_ids:
            print(f"   已关联记忆: {', '.join(e[:8] for e in ep_ids)}")
    else:
        # 失败时提取报错摘要（最有价值的信息）
        err_lines = [l for l in lines
                     if any(k in l for k in
                            ("Error", "error", "Exception", "Traceback",
                             "❌", "FAIL", "失败"))]
        summary = " | ".join(err_lines[:5])[:800] or "无明确报错行"
        record_environment("exec_fail", f"exp_run.py {base}",
                           f"exit={code}, {elapsed:.1f}s",
                           summary, task=task, ep_ids=ep_ids)
        print(f"\n❌ 失败（exit={code}，{elapsed:.1f}s）")
        print(f"   报错摘要: {summary[:200]}")

    return code


if __name__ == "__main__":
    sys.exit(main())
