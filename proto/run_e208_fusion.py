"""E-208 融合分析：LLM 精排 ∪ cross-encoder 精排 的并集命中率"""
import json
import os

RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")

# LLM 精排：命中 193，miss 51（从 e196e_miss_full.json 拿 miss cid）
e196e = json.load(open(os.path.join(RESULTS, "e196e_miss_full.json"), encoding="utf-8"))
llm_miss = {m["cid"] for m in e196e}

# CE 精排：命中 cids 从 e207 拿
e207 = json.load(open(os.path.join(RESULTS, "e207_fp16_gpu.json"), encoding="utf-8"))
ce_hit = set(e207["hit_cids"])
ce_miss = set(e207["miss_cids"])

total = 244
llm_hit = set()
# 从 gold 重建：total 244，miss 51，hit = 244-51=193
# 需要 llm_hit 的 cid 集合。用 total 244 减去 miss 51 = 193，但具体 cid 未知。
# 反推：所有 cid = ce_hit ∪ ce_miss（244 个），llm_hit = 全集 - llm_miss
all_cids = ce_hit | ce_miss
llm_hit = all_cids - llm_miss

print("=" * 94)
print("E-208 融合分析（LLM ∪ CE）")
print("=" * 94)

union = llm_hit | ce_hit
inter = llm_hit & ce_hit
only_llm = llm_hit - ce_hit
only_ce = ce_hit - llm_hit
both_miss = llm_miss & ce_miss

print(f"\n  LLM 命中: {len(llm_hit)}")
print(f"  CE 命中:  {len(ce_hit)}")
print(f"\n  并集（任一命中）: {len(union)}/244 = {len(union)/244:.4f}")
print(f"  交集（两者都命中）: {len(inter)}")
print(f"  仅 LLM 命中: {len(only_llm)}")
print(f"  仅 CE 命中:  {len(only_ce)}")
print(f"  两者都未命中: {len(both_miss)}")

print(f"\n  融合收益 vs LLM 单独: {len(union)/244 - 0.7910:+.4f}")
print(f"  融合收益 vs CE 单独:  {len(union)/244 - 0.7828:+.4f}")

out = {"experiment": "E-208",
       "llm_hit": len(llm_hit), "ce_hit": len(ce_hit),
       "union": len(union), "union_rate": round(len(union)/244, 4),
       "inter": len(inter), "only_llm": len(only_llm),
       "only_ce": len(only_ce), "both_miss": len(both_miss),
       "only_ce_cids": sorted(only_ce)}
with open(os.path.join(RESULTS, "e208_fusion.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 {os.path.join(RESULTS, 'e208_fusion.json')}")
