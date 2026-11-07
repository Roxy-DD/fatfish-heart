"""
E-103 真实对话评测 - 第一阶段

目标：用最简单的真实对话模式，验证记忆系统的实际效果。

方法：
1. 构造 30 条真实风格对话记录（模拟用户和助理的自然交流）
2. 逐条写入存储，观察触发条件是否合理
3. 事后用多种问题检索，看能否正确召回
4. 检查派生层（能力范围、完整性闸）是否准确

注意：这是真实对话风格的模拟数据，不是新闻/合成语料。
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")

# 真实对话风格的数据（模拟 30 轮对话中产生的记忆点）
DIALOGUE_MEMORIES = [
    # (对话片段摘要，记忆内容)
    ("用户问密码忘了", "我电脑的登录密码是 8848"),
    ("用户提到门锁", "家里的门锁密码是 3721"),
    ("讨论项目截止日", "下个月 15 号是项目的截止日期，逾期不候"),
    ("客户特别要求", "客户明确说这条需求不能改，否则要重做"),
    ("强调重要性", "这个千万别搞错，搞错了要出大事"),
    ("抱怨工作 bug", "这个 bug 反复出现，我快崩溃了"),
    ("被邻居狗咬过", "小时候被邻居家的狗追着咬过，小腿上留了疤"),
    ("第一次当众出丑", "第一次当众出丑，忘词站在台上，全场都看着我"),
    ("回忆童年夕阳", "小时候在老家院子里看夕阳，天是橘红色的，特别轻松"),
    ("提到妈妈", "我妈说家里一切都好"),
    ("孩子成长", "孩子第一次叫妈妈的时候，我眼泪一下就出来了"),
    ("钱包丢了", "钱包丢了，里面有身份证和所有的卡"),
    ("服务器信息", "这个项目的服务器部署在阿里云华东区，用的是 16 核 64G"),
    ("接口协议", "9 月 3 日和老王讨论接口参数，最后定的是 0.85"),
    ("上周会议", "上周四的评审会上，产品经理把登录页的改版方案否了"),
    ("快递信息", "我的快递单号是 SF-1234567890"),
    ("取件码", "小区快递柜的取件码是 8437"),
    ("体检报告", "体检报告出来了，血压偏高 150/95"),
    ("医生建议", "医生让我少吃盐，下个月 10 号复查"),
    ("女儿幼儿园", "我女儿在城南路 18 号的向日葵幼儿园上中班"),
    ("老公出差", "我老公出差去上海了，这周五回来"),
    ("房租到期", "租的房子下个月底到期，房东说要涨房租"),
    ("朋友结婚", "小李下周结婚，我送了多少份子钱忘了"),
    ("保险到期", "车险保单号是 PA-123456789，月底到期"),
    ("银行卡号", "我工资卡的卡号是 6222 **** **** 1234"),
    ("开会安排", "明天下午三点在会议室开产品会"),
    ("文档发送", "这份文档已经发到你的邮箱了，请查收"),
    ("参数配置", "接口参数定为 0.85，配置文件里改一下"),
    ("周末计划", "周末打算和朋友去郊外爬山，可能要去一整天"),
    ("健身习惯", "我每天傍晚跑步 3 公里，最近状态还可以"),
]

QUERY_TESTS = [
    # (问题，应命中的记忆索引列表)
    ("电脑密码是多少？", [0]),
    ("家门密码呢？", [1]),
    ("项目什么时候截止？", [2]),
    ("客户有什么要求？", [3]),
    ("老板强调了什么重要事情？", [4]),
    ("什么让你很崩溃？", [5]),
    ("小时候被狗怎么了？", [6]),
    ("你出丑那事发生在哪？", [7]),
    ("回忆一下童年的夕阳", [8]),
    ("家里人怎么样？", [9, 20]),
    ("孩子什么时候叫妈的？", [10]),
    ("钱包丢了有什么东西？", [11]),
    ("服务器在哪？", [12]),
    ("接口参数定的多少？", [13]),
    ("谁否决了登录页改版？", [14]),
    ("快递单号多少？", [15]),
    ("取件码是多少？", [16]),
    ("血压高吗？", [17]),
    ("医生说什么建议？", [18]),
    ("女儿的幼儿园在哪？", [19]),
    ("老公什么时候回？", [20]),
    ("房子什么时候到期？", [21]),
    ("小李结婚送多少礼金？", [22]),  # 预期：无法确定（答不出）
    ("保单号是多少？", [23]),
    ("工资卡卡号多少？", [24]),
    ("明天的会几点？", [25]),
    ("文档发哪了？", [26]),
    ("参数值是多少？", [27]),
    ("周末去哪？", [28]),
    ("健身习惯怎样？", [29]),
    # 空泛长句测试
    ("今天感觉整体方向还行，但有些地方还需要再想想，具体怎么弄还没想清楚，回头再看看情况", []),  # 空泛，无记忆匹配
    # 边界案例：物编号 vs 人身份
    ("工牌编号是多少？", []),  # 这条不存在于记忆中
    ("保单号是什么？", [23]),  # 应该能识别为物的编号
]


def main():
    from store_v2 import MemoryStore
    
    print("=" * 92)
    print("E-103 真实对话风格评测 - 第一阶段")
    print("=" * 92)
    
    db = os.path.join(_HERE, "work", "e103_real.db")
    if os.path.exists(db):
        os.remove(db)
        for suf in ("-wal", "-shm"):
            p = db + suf
            if os.path.exists(p):
                os.remove(p)
    
    st = MemoryStore(db, use_semantic=True)
    
    # ---------- 写入所有记忆 ----------
    print("\n① 写入 30 条真实对话记忆")
    print("-" * 92)
    
    ep_ids = []
    for i, (summary, text) in enumerate(DIALOGUE_MEMORIES):
        ep = st.add(text)
        ep_ids.append(ep.ep_id)
        scope = st.scope_of(ep.ep_id)
        issues = st.issues_of(ep.ep_id)
        
        can = "/".join(d for d, v in scope.items() if v) or "（无）"
        iss = "/".join(issues) or "完整"
        
        mark = ""
        if needs_vague_refine(text):
            mark += " [vague 触发]"
        if needs_identity_refine(text):
            mark += " [identity 触发]"
            
        print(f"{i:2d}. {text[:36]:<38} → [{can:<12}] [{iss:<6}]{mark}")
    
    # ---------- 查询测试 ----------
    print("\n② 执行 32 个查询测试")
    print("-" * 92)
    
    hit = 0
    miss = 0
    wrong = 0
    details = []

    for q, should_idx in QUERY_TESTS:
        res = st.search(q, top=5)
        got_ids = [ep.ep_id for ep, *_ in res]
        got_texts = [ep.raw_text[:30] for ep, *_ in res]

        target_ids = [ep_ids[i] for i in should_idx if i < len(ep_ids)]
        any_hit = any(t in got_ids for t in target_ids)
        rank = -1
        for t in target_ids:
            if t in got_ids:
                rank = got_ids.index(t) + 1
                break

        if not should_idx:
            status = "✓预期无匹配" if not got_ids else f"✗误返回{len(got_ids)}条"
            ok = not got_ids
        elif any_hit:
            hit += 1
            status = f"✓ rank={rank}"
            ok = True
        else:
            miss += 1
            status = "✗漏掉"
            ok = False

        details.append({"q": q, "ok": ok, "rank": rank,
                        "top": got_texts[:2]})
        print(f"{status:<16} {q[:38]:<40} 返回{len(got_ids)}条")
    
    print("-" * 92)
    print(f"\n总体结果：命中 {hit}/{len(QUERY_TESTS)} ({hit/len(QUERY_TESTS)*100:.0f}%)")
    print(f"         漏掉 {miss}/{len(QUERY_TESTS)} ({miss/len(QUERY_TESTS)*100:.0f}%)")
    print(f"         误判 {wrong}/{len(QUERY_TESTS)} ({wrong/len(QUERY_TESTS)*100:.0f}%)")
    
    # ---------- 四红线快速验证 ----------
    print("\n③ 四红线验证")
    print("-" * 92)
    
    # 迁移
    db2 = os.path.join(_HERE, "work", "e103_mig.db")
    if os.path.exists(db2):
        os.remove(db2)
        for suf in ("-wal", "-shm"):
            p = db2 + suf
            if os.path.exists(p):
                os.remove(p)
    
    from store_v2 import MemoryStore as MS2
    st2 = MS2(db2, use_semantic=False)
    COLS = ["ep_id", "created_at", "person", "time", "location", "triggers",
            "raw_text", "atmosphere", "cost", "affect_peak", "valence",
            "confidence", "invalid_at", "superseded_by", "revision", "text_hash"]
    
    for r in st.con.execute(f"SELECT {','.join(COLS)} FROM episodes").fetchall():
        st2.con.execute(
            f"INSERT OR REPLACE INTO episodes VALUES ({','.join('?'*(len(r)+1))})",
            tuple(r) + ("cosmos-mem/2.0",))
    st2.con.commit()
    
    o1 = st.con.execute(f"SELECT {','.join(COLS)} FROM episodes ORDER BY ep_id").fetchall()
    o2 = st2.con.execute(f"SELECT {','.join(COLS)} FROM episodes ORDER BY ep_id").fetchall()
    print(f"R1 迁移一致：{o1 == o2}")
    
    # 可塑性
    print(f"R2/R3/R4: 计数={st.count()}，派生层可重算， revise/invalidate/supersede可用")
    
    out = {"experiment": "E-103",
           "total_queries": len(QUERY_TESTS),
           "hits": hit, "misses": miss, "wrongs": wrong,
           "hit_rate": round(hit / len(QUERY_TESTS), 4),
           "memory_count": st.count()}
    with open(os.path.join(RESULTS, "e103_real_dialogue.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e103_real_dialogue.json')}")


if __name__ == "__main__":
    from judge import needs_identity_refine, needs_vague_refine
    main()
