"""
E-205 融合分析：LLM 精排 + cross-encoder 精排 的理论上限

已有数据：
  LLM 精排（E-196）: 193/244 = 79.1%
  cross-encoder（E-200，int8）: 190/244 = 77.9%

两者分题型互补：
  时间 LLM 75.8% vs CE 87.9%
  对抗 LLM 60.0% vs CE 85.0%
  常识 LLM 93.5% vs CE 85.2%
  单跳 LLM 73.6% vs CE 58.5%

问题：两者是"命中同一批题"还是"互补"？
如果互补，融合（任一命中即算对）能到多少？

方法：读 E-196 和 E-200 的明细，算交集/并集。
"""
from __future__ import annotations
import json
import os

RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")

# 读 LLM 精排命中明细
e196 = json.load(open(os.path.join(RESULTS, "e196_rerank_full.json"), encoding="utf-8"))
# E-196 的 details 是未命中的（前10），hit 是总数，但没存"哪些命中"
# 需要重新算：从 e196e_miss_full.json 拿未命中全集

e196e = json.load(open(os.path.join(RESULTS, "e196e_miss_full.json"), encoding="utf-8"))
# e196e 里是 miss 列表（51条），含 cid
llm_miss = {m["cid"] for m in e196e}

# 读 cross-encoder 结果
e200 = json.load(open(os.path.join(RESULTS, "e200_cross_encoder.json"), encoding="utf-8"))
# e200 的 details 也是未命中的前8，by_cat 有命中数
# 需要完整的 CE 命中/未命中明细，重新从 e200 的 details 拿不到全集
# 从 e200 的 by_cat 反推：总244，hit 190，miss 54

ce_miss_count = e200["n"] - e200["hit"]  # 54

print("=" * 94)
print("E-205 融合分析")
print("=" * 94)
print(f"""
  LLM 精排: 命中 {e196['hit']}/244，未命中 {len(llm_miss)} 题
  CE 精排:  命中 {e200['hit']}/244，未命中 {ce_miss_count} 题

  但缺 CE 的未命中 cid 明细（E-200 只存了前8个），无法算精确交集。
""")

# 需要 CE 完整明细，从 e200 的 details 拿不到，提示重新生成
print("  结论：需要重新生成 CE 的完整命中明细（含 cid）才能算精确融合上限。")
print("  待 fp16 下载完成后，重跑 CE 并输出完整 cid 明细。")
