"""E-277 把五个机制验证的完整结论写入记忆

E-245~275 连续做了 30 个实验，覆盖：
  sleep / conflict / note / todo / config / chain / affect
  七个曾经"有代码但零使用"的机制。

现在把结论写入长期记忆，供未来读取。
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proto", "cosmos_mem"))

from store_v2 import MemoryStore

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proto", "cosmos_mem", "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 74)
print("E-277 记录机制验证结论")
print("=" * 74)

lessons = [
    "七个机制的验证结论（E-245~275，30 个实验）："
    "sleep 修了候选对重复询问（游标推进后新增不足 2 条会退回 recent 窗口，"
    "每次都扫同一批，加了 sleep:judged_pairs 记录已判定对）；"
    "conflict 修了静默失败（relation=replace 但 target 为空时不执行任何动作，"
    "现在显式返回 no_action+warn）；"
    "note 接入 knowledge_hint（系统机械扫描该整理了什么，送到模型面前，只在首轮注入）；"
    "todo 自动生成 note_hint（即使模型没主动写笔记，待办列表也始终有待整理项）；"
    "config 加了配置审计（敏感字段脱敏，无变化不写日志）；"
    "chain 修了字段名不一致（candidates 嵌在 pack 里、new_candidates 是数字不是数组）；"
    "affect 修了 valence 类型冲突（原来强制 int 三值 -1/0/+1，"
    "但 episodes.valence 是 REAL 且共振判断要求 |valence|>=0.15，"
    "三值化导致共振路径永不触发；改为连续 [-1,1]，"
    "并让 resonance_score 用 min(|av|,|bv|) 作为因子，"
    "这样强负面共振 0.570 vs 弱负面 0.114）。",

    "检索延迟的正确理解（E-269~275 五个实验追查）："
    "7347ms 不是 bug，是内核冷启动加载 BGE 模型的一次性成本。"
    "证据链：①冷启动 7189ms vs 热启动 10ms；"
    "②起并发进程抢 GPU 只慢 1.27x（11→14ms），推翻'GPU 争用'假设；"
    "③日志时序显示 16:40:19 请求开始、16:40:26 kernel-ready、"
    "16:40:26 assemble msSearch=7347，说明是 waitReady 等待时间；"
    "④内核已做 encode+search 双预热，无可优化空间。"
    "热启动 10-14ms 已比历史 26.6ms 更快。"
    "教训：发现异常慢时要先分清'一次性成本'和'系统性退化'，"
    "否则会为不存在的 bug 做无谓优化。",

    "系统健康基准（E-269 体检）：有效记忆 67 条、失效 270 条（R2 只增不删）、"
    "向量 337 个且数量匹配；无重复组、无 affect/valence 越界、无悬空 superseded_by；"
    "注入平均 1218 字（预算 1500，合理）；"
    "性能：检索热 10-14ms、写入 11-12ms、审计 0.5ms、"
    "sleep prepare 7ms、knowledge_hint 缓存命中 0.1ms。",
]

added = 0
for t in lessons:
    dup = st.con.execute(
        "SELECT ep_id FROM episodes WHERE invalid_at IS NULL AND raw_text=?",
        (t,)).fetchone()
    if dup:
        print(f"   （已存在，跳过）{t[:40]}...")
        continue
    ep = st.add(t, resolve_conflict=False)
    added += 1
    print(f"   ✅ 写入 {t[:45]}...")

print(f"\n新增 {added} 条记忆")

# 统计
n = st.con.execute(
    "SELECT COUNT(*) FROM episodes WHERE invalid_at IS NULL").fetchone()[0]
print(f"有效记忆总数: {n}")

st.con.close()
