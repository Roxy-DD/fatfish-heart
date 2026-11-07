"""E-112 验证 cost 规则补充后，工作约定类记忆的重要性是否提升"""
import sys, os
sys.path.insert(0, r"C:\Users\xuee\Desktop\肥鱼之心\proto\cosmos_mem")
from store_v1 import estimate_cost

CASES = [
    # 工作方式约定类（之前 cost=0.0）
    ("用户要求：遇到问题应先自己尝试解决，方法论在之前的笔记中可以查到，实在解决不了再统一提问", "约定类"),
    ("用户强调一切判断以实验数据为准，而非主观推测", "约定类"),
    ("用户约定的记忆工作流程：每次新会话开始时先查看上次记忆", "约定类"),
    ("用户的核心要求：实验是获得真理的唯一正确途径", "约定类"),
    # 对照：本来就该高的
    ("我电脑的登录密码是 8848", "硬事实"),
    ("下个月15号是项目截止日期，逾期不候", "硬事实"),
    # 对照：本来就该低的
    ("你好", "闲聊"),
    ("这个函数的时间复杂度是多少？", "问句"),
    ("帮我查一下今天北京的天气", "一次性操作"),
]

print("=" * 76)
print("E-112 cost 规则补充效果验证")
print("=" * 76)
print(f"{'类型':<10}{'cost':<8}{'内容'}")
print("-" * 76)

groups = {}
for text, kind in CASES:
    c = estimate_cost(text)
    groups.setdefault(kind, []).append(c)
    print(f"{kind:<10}{c:<8.2f}{text[:50]}")

print("-" * 76)
print("\n分组均值：")
for kind, vals in groups.items():
    print(f"  {kind:<12}{sum(vals)/len(vals):.3f}")

conv = groups.get("约定类", [])
print(f"\n关键检验：约定类均值 {sum(conv)/len(conv):.3f}")
print(f"  目标：> 0.5（之前是 0.0）")
print(f"  → {'✅ 达标' if sum(conv)/len(conv) > 0.5 else '❌ 未达标'}")

# 闲聊不该被误抬
noise = groups.get("闲聊", []) + groups.get("问句", []) + groups.get("一次性操作", [])
print(f"\n噪声组均值 {sum(noise)/len(noise):.3f}（应接近 0）")
print(f"  → {'✅ 未误伤' if sum(noise)/len(noise) < 0.2 else '⚠ 有误伤'}")
