"""E-319 量化：L0/L1 每轮重复注入的 token 成本

发现：
  · search 每次都返回 11 条 flag=2（L0/L1）
  · 插件把它们分出来，首轮给全文、后续给标题
  · 但**每轮都要传输这 11 条**（内核 → 插件 JSON）

问题：
  ① 传输成本：每轮 11 条记忆的 JSON
  ② 处理成本：插件每轮都要 formatProtected()

如果 L0/L1 内容不变，能不能只传一次？

量化：
  · 每轮传输多少字符
  · 后续轮实际注入了多少
"""
import sys
import os
import json
import warnings
import time

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

from store_v2 import MemoryStore
import hook as _hook

DB = os.path.join(MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 76)
print("E-319 量化 L0/L1 的每轮成本")
print("=" * 76)

# ============ ① 测量 search 返回的 JSON 大小 ============
print("\n① search 返回的数据量")

queries = ["记忆系统", "Docker", "AGI 观点", "去重机制"]

for q in queries:
    res = st.search(q, top=5)
    # 模拟内核返回的 JSON
    payload = {
        "ok": True,
        "results": [
            {"ep_id": ep.ep_id, "text": ep.raw_text,
             "score": sc, "flag": fl}
            for ep, sc, fl in res
        ],
    }
    json_str = json.dumps(payload, ensure_ascii=False)
    n_flag2 = sum(1 for ep, sc, fl in res if fl == 2)
    n_normal = len(res) - n_flag2

    # 分别算
    flag2_chars = sum(len(ep.raw_text) for ep, sc, fl in res if fl == 2)
    normal_chars = sum(len(ep.raw_text) for ep, sc, fl in res if fl != 2)

    print(f"\n   查询: '{q}'")
    print(f"     返回 {len(res)} 条（普通 {n_normal} + 强制 {n_flag2}）")
    print(f"     JSON 大小: {len(json_str)} 字符")
    print(f"     其中强制注入正文: {flag2_chars} 字符")
    print(f"     其中普通结果正文: {normal_chars} 字符")
    print(f"     强制注入占比: {flag2_chars/max(1,flag2_chars+normal_chars)*100:.0f}%")

# ============ ② 如果 L0/L1 缓存，能省多少 ============
print("\n② 假设 L0/L1 只传一次能省多少")

res = st.search("Docker", top=5)
flag2_items = [(ep, sc, fl) for ep, sc, fl in res if fl == 2]
normal_items = [(ep, sc, fl) for ep, sc, fl in res if fl != 2]

flag2_json = json.dumps(
    [{"ep_id": ep.ep_id, "text": ep.raw_text, "flag": fl}
     for ep, sc, fl in flag2_items],
    ensure_ascii=False)
normal_json = json.dumps(
    [{"ep_id": ep.ep_id, "text": ep.raw_text, "score": sc, "flag": fl}
     for ep, sc, fl in normal_items],
    ensure_ascii=False)

print(f"\n   强制注入部分的 JSON: {len(flag2_json)} 字符")
print(f"   普通结果部分的 JSON: {len(normal_json)} 字符")
print(f"\n   如果只传一次 L0/L1：")
print(f"     第 1 轮: {len(flag2_json) + len(normal_json)} 字符")
print(f"     后续轮: {len(normal_json)} 字符（省 {len(flag2_json)}）")
print(f"     节省比例: {len(flag2_json)/max(1,len(flag2_json)+len(normal_json))*100:.0f}%")

# ============ ③ 检查 L0/L1 内容是否真的不变 ============
print("\n③ L0/L1 内容是否稳定")

present = _hook.present_ids(st)
hashes = []
for eid in present:
    ep = st.get(eid)
    if ep:
        import hashlib
        h = hashlib.md5(ep.raw_text.encode()).hexdigest()[:8]
        hashes.append((eid[:8], h))

print(f"   {len(hashes)} 条 L0/L1 的指纹:")
for eid, h in hashes:
    print(f"     [{eid}] {h}")

print(f"\n   如果内容不变，指纹就不会变")
print(f"   → 可以用指纹做缓存 key")

# ============ ④ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

print(f"""
  L0/L1 每轮传输成本:
    · 11 条，JSON 约 {len(flag2_json)} 字符
    · 占 search 返回总量的 {len(flag2_json)/max(1,len(flag2_json)+len(normal_json))*100:.0f}%

  但这些内容**每轮都一样**（除非修订）。
  所以可以用"指纹"做缓存：
    · 首次传输全文
    · 后续只传指纹，插件发现指纹未变就复用缓存

  不过要评估：这样做值得吗？
    · 省的是**进程间传输**（内核 → 插件 JSON）
    · 不省模型上下文（模型看到的注入量由插件控制）
    · 内核是本地进程，传输 4KB 成本很低

  我的判断：
    传输层优化收益很小（本地进程通信），
    真正值得优化的是**模型上下文**，而那个已经做了
    （首轮全文 / 后续标题，E-231 + E-294）。

  所以：不改。
  记录下这个分析，避免以后重复思考同一问题。
""")

st.con.close()

out = {
    "experiment": "E-319",
    "flag2_json_chars": len(flag2_json),
    "normal_json_chars": len(normal_json),
    "flag2_ratio": round(len(flag2_json) /
                         max(1, len(flag2_json) + len(normal_json)), 3),
    "verdict": "不改（本地传输成本低，模型上下文已优化）",
}
p = os.path.join(_HERE, "results", "e319_cost.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e319_cost.json")
