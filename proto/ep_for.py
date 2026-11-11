"""E-430 把「标注关联记忆」变成低摩擦动作

问题：
  link_ratio 只有 6.4%。
  原因不是"我不愿意标"，而是标注要手打 ep_id（16 位十六进制），
  成本高到我会跳过。

  E-417 做了 MESH_EP，但仍需手输 id。

解法：按关键词查 id，直接给可复制的命令。

用法：
  python ep_for.py "查询这轮用了哪些记忆"
  → 输出前 5 条相关记忆的 ep_id + 摘要 + 现成的 MESH_EP 命令

这样标注成本从"找 id"降到"复制一行"。

★ 注意：这个工具只**辅助**标注，不自动标注。
  自动标注 = 模型自己说"这条有用"，违反《环管群》5.2。
  必须我看了摘要后确认"确实用了它"，才复制命令。
"""
import sys
import os
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

from store_v2 import MemoryStore

DB = os.path.join(MEM, "dsh_mem.db")


def main():
    if len(sys.argv) < 2:
        print("用法: python ep_for.py <查询词> [top]")
        print("输出: 相关记忆的 ep_id + 现成的 MESH_EP 命令")
        return 1

    query = sys.argv[1]
    top = int(sys.argv[2]) if len(sys.argv) > 2 else 5

    st = MemoryStore(DB)
    try:
        r = st.search_scoped(query, top=top)
        items = r[0] if isinstance(r, tuple) else r
    except Exception as e:
        print(f"检索失败: {type(e).__name__}: {e}")
        st.con.close()
        return 1

    if not items:
        print("没有相关记忆")
        st.con.close()
        return 0

    print("=" * 74)
    print(f"查询: {query}")
    print("=" * 74)

    picked = []
    for i, it in enumerate(items, 1):
        # 兼容 3 元组和 4 元组
        if len(it) >= 4:
            ep, sc, note, flag = it[0], it[1], it[2], it[3]
        else:
            ep, sc, note = it[0], it[1], it[2]
            flag = 0

        if flag == 2:
            continue          # L0/L1 常驻，不需要每轮标注

        picked.append(ep.ep_id)
        txt = (ep.raw_text or "").replace("\n", " ")
        print(f"\n[{i}] {ep.ep_id}  score={sc:.3f}")
        print(f"    {txt[:110]}")

    if not picked:
        print("（只有 L0/L1 命中，无需标注）")
        st.con.close()
        return 0

    print("\n" + "=" * 74)
    print("如果确认用到了上面某些记忆，复制下面这行去跑验证：")
    print("=" * 74)
    ids = ",".join(picked[:3])
    print(f"\n  MESH_EP={ids} python iterate.py check E-XXX\n")
    print("（确认「确实用了」再复制；不确定就不标，宁可少标不虚构）")

    st.con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
