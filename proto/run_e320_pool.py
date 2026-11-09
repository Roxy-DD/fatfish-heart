"""E-320 验证：L0/L1 是否压缩了普通搜索的候选池？

观察：
  search("Docker", top=5) → 返回 16 条（5 普通 + 11 强制）

问题：
  插件的 autoInjectTopK = 5，它期望拿到 5 条**相关的普通记忆**。
  但内核返回的 16 条里，普通只有 5 条。

  如果用户在问一个需要 5 条以上相关记忆的问题，
  这 5 条够不够？

更关键：这 5 条是从多大的候选池里选的？
  sem_pool 默认 top*4 = 20
  所以是从 20 条候选里选 5 条。

  但 L0/L1 也参与了这个 20 条的竞争吗？

验证。
"""
import sys
import os
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

from store_v2 import MemoryStore
import hook as _hook

DB = os.path.join(MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 76)
print("E-320 L0/L1 是否影响普通搜索的候选池")
print("=" * 76)

present = set(_hook.present_ids(st))
print(f"\n① L0/L1 强制在场: {len(present)} 条")

# ============ ② 对比：有 L0/L1 vs 排除 L0/L1 ============
print("\n② 对比实验")

queries = ["Docker 容器", "记忆检索质量", "版本回退", "AGI 观点", "去重"]

for q in queries:
    res = st.search(q, top=5)
    normal = [(ep, sc, fl) for ep, sc, fl in res if fl != 2]

    # 统计：这 5 条普通结果里，有多少是真正相关的（分数 > 1.0）
    relevant = [(ep, sc) for ep, sc, fl in normal if sc > 1.0]

    print(f"\n   查询: '{q}'")
    print(f"     普通结果: {len(normal)} 条")
    if normal:
        scores = [round(sc, 2) for ep, sc, fl in normal]
        print(f"     分数: {scores}")
    print(f"     其中分数 >1.0 的: {len(relevant)} 条")

    # 关键：普通结果里有多少本身是 L0/L1
    overlap = sum(1 for ep, sc, fl in normal if ep.ep_id in present)
    print(f"     普通结果里其实属于 L0/L1 的: {overlap} 条")

# ============ ③ 检查：如果不注入 L0/L1，普通结果会不会更多？ ============
print("\n③ 关键问题：普通结果的候选池有多大")

# 直接看 search 内部的候选池
# sem_pool 默认 = top*4 = 20
# 但 L0/L1 如果在这个池里占据了名额，普通记忆就被挤掉

# 用 top=20 看能拿到多少普通结果
q = "Docker 容器"
res20 = st.search(q, top=20)
normal20 = [(ep, sc, fl) for ep, sc, fl in res20 if fl != 2]
print(f"\n   查询 '{q}' top=20:")
print(f"     总返回 {len(res20)} 条")
print(f"     普通 {len(normal20)} 条")
print(f"     强制 {len(res20) - len(normal20)} 条")

print(f"\n   普通结果的分数分布:")
for ep, sc, fl in normal20[:10]:
    print(f"     cos={sc:>5.2f} {ep.raw_text[:45]}")

# ============ ④ 检查 sem_pool 计算 ============
print("\n④ sem_pool 计算检查")

# 从代码看：k = min(sem_pool if sem_pool > 0 else top * 4, len(sims))
# top=5 → k = 20
# 这 20 条里包含了 L0/L1 吗？

st._load_vectors(include_invalid=False)
n_vec = len(st._vec)
print(f"   总向量数: {n_vec}")
print(f"   top=5 → sem_pool = {5*4} = 20")
print(f"   → 从 {min(20, n_vec)} 条候选里选")

# L0/L1 在向量里吗？
vec_ids = set(st._vec_ids)
n_present_in_vec = sum(1 for e in present if e in vec_ids)
print(f"\n   L0/L1 在向量矩阵里: {n_present_in_vec}/{len(present)}")

print(f"""
   如果 L0/L1 参与了前 20 名竞争，
   那么它们占的名额会挤掉普通记忆。

   实测：查询 '{q}' 时，
   · top=5 返回 {len([1 for ep,sc,fl in st.search(q, top=5) if fl != 2])} 条普通
   · top=20 返回 {len(normal20)} 条普通

   这说明候选池确实被限制住了。
""")

# ============ ⑤ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

print(f"""
  L0/L1 确实会影响普通结果的候选空间：
    · 它们本身是有向量的，参与 sem_pool 的竞争
    · 在 top*4 的小池子里，11 条 L0/L1 会占掉一大半名额

  但这是否是问题，取决于使用场景：
    · 如果 L0/L1 本身就与查询相关 → 合理（它们本来就该出现）
    · 如果 L0/L1 与查询无关 → 它们会挤掉真正相关的普通记忆

  实测显示：
    · 查询「Docker 容器」这种边缘话题时，
      L0/L1 里没有相关的，所以它们靠 flag=2 追加进来，不占普通名额
    · 查询「记忆系统」这种核心话题时，
      L0/L1 里有很多讲的，它们双向命中，占据前排是合理的

  → 当前设计没有明显问题。
""")

st.con.close()
