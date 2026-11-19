# FFH-RedlineBench

> **测什么都没测的东西**：长期记忆系统的四条工程红线。

## 为什么有这个基准

2026-11 我们调研了 11 个公开的记忆评测基准
（LMEB / LoCoMo / LongMemEval / MemoryAgentBench / MemGround / MemBench /
MemoryArena / THUIR-MemoryBench 等），结论是：

| 能力 | 公开基准是否覆盖 |
|---|---|
| 检索排序质量 | ✅ 很多（NDCG@10、Recall@k）|
| 问答准确率 | ✅ 很多（LLM-judge、F1）|
| 知识更新不漂移 | ✅ 部分（MemoryAgentBench CR、LongMemEval knowledge-update）|
| **R1 无损迁移** | ❌ **无** |
| **R2 不遗忘** | ❌ **无** |
| **R3 索引可重建** | ⚠️ 仅间接（重建前后 NDCG 对比）|
| **R4 失效/取代可溯源** | ❌ **无** |

**所有公开基准都只测"检索/回答质效"，没有一个测"数据是否真的没丢、能否原样迁移、能否从真值层重建"。**

这不是学术空缺，是工程刚需：你的记忆系统每天被读写、被备份、被恢复，
出问题时不是"答案差一点"，而是"数据没了/不一致了"。

## 这个基准测什么

四条红线，15 项断言：

### R1 无损迁移（5 项）
- 同库重载：episodes 逐字段哈希一致
- 同库重载：检索结果逐 query 一致（ep_id + 分数 + 排序）
- 跨文件复制：episodes 逐字段一致
- 跨文件复制：检索结果逐 query 一致
- 向量一致性：top1 分数偏差 < 1e-9

### R2 不遗忘（3 项）
- 失效后总行数不变（无物理删除）
- 失效记忆被标记而非删除
- 重载后失效记录仍存在（可溯源）

### R3 可重建（3 项）
- 向量表可被清空
- 重建后向量表恢复
- 重建后检索结果逐 query 一致（**核心断言**）

### R4 可塑性（4 项）
- 修订生效
- revision 递增（可溯源）
- 旧值保留在 history
- 失效记录指向取代者（superseded_by）

## 实测结果（fatfish-heart v1.1）

```
R1 无损迁移  ✅ 5/5   （含向量偏差 0.000000）
R2 不遗忘    ✅ 3/3   （16 → 16，零物理删除）
R3 可重建    ✅ 3/3   （清空后重建，检索指纹完全一致）
R4 可塑性    ✅ 4/4   （修订/revision/历史/取代链路完整）

总计 15/15 通过，耗时 14.9s
```

## 怎么跑

```bash
# 全部
python FFH-RedlineBench.py

# 只测某条
python FFH-RedlineBench.py --only r3

# 输出 JSON
python FFH-RedlineBench.py --json out.json
```

依赖：项目自带的 `cosmos_mem` 模块 + 本地 BGE 编码模型（bge-small-zh）。
不需要网络、不需要调用大模型 API。

## 设计原则

1. **完全隔离**：所有操作在临时目录，不碰生产库
2. **确定性**：同样输入必得同样输出
3. **自包含**：不依赖网络、不依赖 LLM
4. **红线判定是布尔的**：任何一条失败，整体失败（不做加权平均）
5. **用哈希比对，不用"看起来一样"**：逐字段 sha256 + 逐 query 检索指纹

## 怎么用到你自己的记忆系统

如果你在做一个长期记忆系统，可以用这个基准检验：

1. 换掉 `build_db()`，改成你的写入接口
2. 换掉 `query_fingerprint()`，改成你的检索接口
3. 换掉 `ep_fingerprint()`，改成你的真值层字段
4. 保持四条红线的断言逻辑不变

**这四条红线是任何"记忆"系统的最低要求**：
- 记忆不能因为重启/迁移而丢
- 记忆不能因为"更新"而物理消失
- 索引坏了必须能从数据重建
- 旧值必须可追溯（人在说谎时你才能发现）

## 许可

AGPL-3.0（与 fatfish-heart 一致）

## 引用

如果你在论文/文档里用到这个基准：

```
FFH-RedlineBench: A meta-benchmark for the four engineering invariants
of long-term memory systems (lossless migration, no-forgetting,
rebuildable index, traceable plasticity).
https://github.com/Roxy-DD/fatfish-heart
```
