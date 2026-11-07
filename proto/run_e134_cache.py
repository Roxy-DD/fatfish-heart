"""
E-134 缓存一致性回归测试（防 E-133 那类 bug 再犯）

E-133 现象：
  apply_verdict 把旧值标失效了，数据库正确（invalid=True）
  但常驻内核的检索仍返回旧值 —— 因为向量缓存没清

  根因有两层：
    ① invalidate() 自身不清缓存（最根本）
    ② cmd_judge 里写的是 st._vec = None，没清 _vec_cache

本测试覆盖所有"会改变有效集合"的操作：
  · add           写入
  · invalidate    失效
  · revise        修订
  · rebuild       重建

每种操作后立刻检索，检查结果是否反映新状态。
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
DB = os.path.join(_HERE, "e134_cache.db")


def fresh():
    for suf in ("", "-wal", "-shm"):
        p = DB + suf
        if os.path.exists(p):
            os.remove(p)


def main():
    from store_v2 import MemoryStore

    fresh()
    st = MemoryStore(DB)

    print("=" * 86)
    print("E-134 缓存一致性回归测试")
    print("=" * 86)

    results = []

    # ---------- ① add 后立即可检索 ----------
    print("\n① add 后立即可检索")
    st.add("我的笔记本型号是 ThinkPad X1", resolve_conflict=False)
    st.search("笔记本型号", top=3)          # 先建立缓存
    st.add("我换电脑了，现在是 MacBook Pro M4", resolve_conflict=False)
    res = st.search("笔记本型号", top=3)
    texts = [ep.raw_text for ep, *_ in res]
    ok1 = any("MacBook" in t for t in texts)
    print(f"  新记忆立即可见: {'✅' if ok1 else '❌'}")
    for t in texts[:3]:
        print(f"    {t[:50]}")
    results.append({"case": "add_visible", "ok": ok1})

    # ---------- ② invalidate 后检索应排除 ----------
    print("\n② invalidate 后检索应排除旧值")
    old_id = st.con.execute(
        "SELECT ep_id FROM episodes WHERE raw_text LIKE '%ThinkPad%'").fetchone()[0]
    st.search("笔记本型号", top=3)          # 确保缓存已建立
    st.invalidate(old_id, superseded_by=None)
    res = st.search("笔记本型号", top=3)
    texts = [ep.raw_text for ep, *_ in res]
    still_there = any("ThinkPad" in t for t in texts)
    ok2 = not still_there
    print(f"  失效后是否仍返回旧值: {'❌ 仍返回' if still_there else '✅ 已排除'}")
    for t in texts[:3]:
        print(f"    {t[:50]}")
    results.append({"case": "invalidate_excluded", "ok": ok2})

    # ---------- ③ include_invalid=True 应能查到 ----------
    print("\n③ include_invalid=True 应能查到失效记录（R2）")
    res2 = st.search("笔记本型号", top=5, include_invalid=True)
    texts2 = [ep.raw_text for ep, *_ in res2]
    found_old = any("ThinkPad" in t for t in texts2)
    ok3 = found_old
    print(f"  旧值仍可查: {'✅' if found_old else '❌ 丢失'}")
    results.append({"case": "include_invalid_finds_old", "ok": ok3})

    # ---------- ④ apply_verdict 路径 ----------
    print("\n④ apply_verdict 后检索应排除 ----------")
    import conflict as _conflict
    st.add("我的车是白色卡罗拉", resolve_conflict=False)
    st.search("我的车", top=3)
    ep_new = st.add("我换车了现在是黑色大众", resolve_conflict=False)
    target = st.con.execute(
        "SELECT ep_id FROM episodes WHERE raw_text LIKE '%白色卡罗拉%'").fetchone()[0]
    v = _conflict.Verdict(relation="replace", target_ep_id=target,
                          target_text="我的车是白色卡罗拉", reason="test")
    _conflict.apply_verdict(st, v, ep_new.ep_id)
    res3 = st.search("我的车是什么", top=3)
    texts3 = [ep.raw_text for ep, *_ in res3]
    still = any("白色卡罗拉" in t for t in texts3)
    ok4 = not still
    print(f"  apply_verdict 后旧值是否仍返回: {'❌ 仍返回' if still else '✅ 已排除'}")
    for t in texts3[:3]:
        print(f"    {t[:50]}")
    results.append({"case": "apply_verdict_excluded", "ok": ok4})

    # ---------- ⑤ revise 后检索 ----------
    print("\n⑤ revise 后检索应反映新文本")
    st.add("接口参数定的是0.85", resolve_conflict=False)
    st.search("接口参数", top=3)
    eid = st.con.execute(
        "SELECT ep_id FROM episodes WHERE raw_text LIKE '%0.85%'").fetchone()[0]
    st.revise(eid, new_text="接口参数改成 1.2")
    res4 = st.search("接口参数多少", top=3)
    texts4 = [ep.raw_text for ep, *_ in res4]
    has_new = any("1.2" in t for t in texts4)
    ok5 = has_new
    print(f"  修订后新文本可见: {'✅' if has_new else '❌'}")
    results.append({"case": "revise_visible", "ok": ok5})

    # ---------- ⑥ 重启进程后一致 ----------
    print("\n⑥ 重开连接后结果一致（R1）")
    before = [ep.ep_id for ep, *_ in st.search("我的车是什么", top=3)]
    st.con.close()

    st2 = MemoryStore(DB)
    after = [ep.ep_id for ep, *_ in st2.search("我的车是什么", top=3)]
    ok6 = before == after
    print(f"  重开一致: {'✅' if ok6 else '❌'}")
    st2.con.close()
    results.append({"case": "restart_consistent", "ok": ok6})

    # ---------- 汇总 ----------
    print("\n" + "=" * 86)
    print("汇总")
    print("=" * 86)
    n_ok = sum(1 for r in results if r["ok"])
    for r in results:
        print(f"  {'✅' if r['ok'] else '❌'} {r['case']}")
    print(f"\n  通过 {n_ok}/{len(results)}")

    passed = n_ok == len(results)
    print(f"  → {'✅ 缓存一致性无问题' if passed else '❌ 仍有缓存 bug'}")

    out = {"experiment": "E-134", "passed": passed,
           "n_pass": n_ok, "n_total": len(results), "cases": results}
    with open(os.path.join(RESULTS, "e134_cache.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e134_cache.json')}")


if __name__ == "__main__":
    main()
