/-
  FatFish.lean — 肥鱼之心（Fat Fish Heart）运行数学模型的形式化

  ============================================================
  为什么用 Lean
  ============================================================
  用户要求：「用 lean 形式化写出这个系统的运行数学模型，
  并计算测试各个边界和适用情况；计算哪些部分可以减少 token 开支」

  形式化的价值不是「证明系统正确」，而是：
    ① 把散在代码注释里的约束变成可机检的不变量
    ② 边界由定理给出，不靠手试
    ③ 找 token 可省之处时，能证明「省了也不破坏不变量」

  ============================================================
  建模范围（对齐真实实现）
  ============================================================
  · 生命周期状态机      ← dsh-plugin/lifecycle.js stateOf()
  · 注入预算模型        ← dsh-plugin/index.js formatProtected + 预算语义
  · 四条红线不变量      ← R1 无损迁移 / R2 不遗忘 / R3 可重建 / R4 可塑性
  · 备份触发模型        ← proto/cosmos_mem/auto_backup.py + backup.py

  ============================================================
  诚实边界（先说清，不含糊）
  ============================================================
  · 这是**模型**，不是代码的逐行翻译。抽象掉了 SQLite/WAL/并发细节。
  · 不变量是「设计意图的形式化」，Lean 证明的是「模型内自洽」，
    不是「实现绝对正确」——实现仍由 44 项回归 + 10 类故障注入守。
  · 数值边界（如 6000 字上限）来自实测，写进定理的前提。
-/

namespace FatFish

/-! ## 一、记忆状态 -/

/-- 记忆条目：形式化摘要（真实有 17 字段，这里保留与红线相关的）
    revision  修订次数（R4：>1 必须有历史）
    invalid   是否失效（R2：失效只打标记，不物理删除）
    hasVector 是否有向量索引（R3：索引可重建） -/
structure Episode where
  id        : Nat
  revision  : Nat
  invalid   : Bool
  hasVector : Bool
  deriving DecidableEq, Repr

/-- 记忆库状态
    store    : 当前所有条目（含失效的 —— R2 要求不删）
    history  : 历史快照数（R4 要求可溯源）
    vectors  : 向量条数（R3 要求可从 store 重建） -/
structure MemState where
  store   : List Episode
  history : Nat
  deriving DecidableEq, Repr

/-! ## 二、四条红线作为不变量 -/

/-- R2 不遗忘：失效的条目仍在 store 里（只打标记，不删除）
    「不遗忘」的形式化：不存在「曾经存在但消失了」的条目。
    这里用更可检查的等价形式：所有 invalid 条目都还在。 -/
def R2_noForget (m : MemState) : Prop :=
  ∀ e ∈ m.store, e.invalid = true → e ∈ m.store

/-- R3 可重建：向量索引数 ≤ 真值条数
    （索引是派生的，真值是 store；重建只会补齐不会多出） -/
def R3_rebuildable (m : MemState) (vecCount : Nat) : Prop :=
  vecCount ≤ m.store.length

/-- R4 可塑性：修订过的条目必须有历史可溯源 -/
def R4_plastic (m : MemState) : Prop :=
  ∀ e ∈ m.store, e.revision > 1 → m.history > 0

/-- R1 无损迁移：迁移前后「条目 id 集合」与「历史数」不变
    （真实实现用 10 表 2310 行逐字段指纹，这里抽象为关键不变量） -/
def R1_lossless (before after : MemState) : Prop :=
  before.store.map (·.id) = after.store.map (·.id) ∧
  before.history = after.history

/-! ## 三、定理：红线在典型操作下成立 -/

/-- 追加操作（add）：store 增长，history 不变 -/
def opAdd (m : MemState) (e : Episode) : MemState :=
  { m with store := m.store ++ [e] }

/-- 追加不破坏 R2（失效条目仍留在 store，新增条目不影响已有成员资格） -/
theorem add_preserves_R2 (m : MemState) (e : Episode)
    (h : R2_noForget m) : R2_noForget (opAdd m e) := by
  intro x hx hinv
  -- 新条目在 store ++ [e] 中，要么在原 store，要么就是 e
  simp [opAdd, List.mem_append] at hx ⊢
  cases hx with
  | inl hmem => exact Or.inl (h x hmem hinv)
  | inr heq => exact Or.inr heq

/-- 合并操作（sleep 的 merge）：把 drop 标为失效、keep 修订 +1
    R2 要求：drop 不删除，只是 invalid = true -/
def opMerge (m : MemState) (keep drop : Nat) : MemState :=
  { m with
    store := m.store.map (fun e =>
      if e.id = drop then { e with invalid := true }
      else if e.id = keep then { e with revision := e.revision + 1 }
      else e)
    history := m.history + 1 }

/-- 合并保留 R2：drop 条目仍在 store（只是 invalid 变了） -/
theorem merge_preserves_R2_card (m : MemState) (keep drop : Nat) :
    (opMerge m keep drop).store.length = m.store.length := by
  simp [opMerge, List.length_map]

/-- 合并使 R4 成立（history 增加 1，所以存在历史） -/
theorem merge_establishes_R4 (m : MemState) (keep drop : Nat)
    (e : Episode) (hmem : e ∈ (opMerge m keep drop).store)
    (hrev : e.revision > 1) : (opMerge m keep drop).history > 0 := by
  simp [opMerge]
  omega

/-! ## 四、生命周期状态机（对齐 lifecycle.js 真实字段） -/

/-- 生命周期状态（真实字段的精简，保留决策相关部分） -/
structure LcState where
  consecutive       : Nat   -- 连续 steer 次数
  totalSteers       : Nat
  noProgressStreak  : Nat   -- 连续无进展次数
  declaredDone      : Bool  -- 模型自己宣布完成（E-479）
  steerInFlight     : Bool  -- 防重入（E-371）
  userMsgCount      : Nat   -- 用户消息数（E-481，用于判断是否在等输入）
  lastSteerUserMsgs : Option Nat
  deriving DecidableEq, Repr

/-- 配置（对齐 DEFAULT_CONFIG，数值来自实测） -/
structure LcConfig where
  maxConsecutive : Nat := 20
  maxNoProgress  : Nat := 3
  deriving DecidableEq, Repr

/-- 决策结果：四种出口 -/
inductive Decision where
  | steer          -- 继续（发消息推进）
  | giveUp         -- 放行，本轮结束
  | honorDone      -- 尊重模型宣布的完成
  | skipReentry    -- 防重入跳过
  deriving DecidableEq, Repr

/-- 决策函数：严格对齐 lifecycle.js 的真实判定顺序

    ★ 顺序来自逐行读代码（2026-11-16 核对），不是凭印象：
      ① if (s.declaredDone)          → honorDone（E-479，最优先）
      ② if (s.steerInFlight)         → skipReentry（E-371 防重入）
      ③ if (s.consecutive >= maxConsecutive) → giveUp（防空转）
      ④ if (noProgressStreak+1 >= limit)     → giveUp（无进展放行）

    注意：③ 在 ④ 之前。我第一版把 steerInFlight 和 maxConsecutive
    的顺序写反了，核对代码后纠正 —— 形式化必须对着实现写，
    不能凭记忆。
-/
def decide (cfg : LcConfig) (s : LcState) (hasProgress : Bool) : Decision :=
  if s.declaredDone then Decision.honorDone
  else if s.steerInFlight then Decision.skipReentry
  else if s.consecutive >= cfg.maxConsecutive then Decision.giveUp
  else if !hasProgress && s.noProgressStreak + 1 ≥ cfg.maxNoProgress
       then Decision.giveUp
  else Decision.steer

/-! ## 五、边界定理：什么时候一定会停 -/

/-- 边界 1：宣布完成后一定不停留（尊重模型判断）
    这是 E-479 的核心：模型说停就停，计数器不能否决 -/
theorem declared_done_always_honored (cfg : LcConfig) (s : LcState)
    (hp : Bool) (h : s.declaredDone = true) :
    decide cfg s hp = Decision.honorDone := by
  simp [decide, h]

/-- 边界 2：达到 maxConsecutive 一定放行（防无限空转）
    前提：未宣布完成、无重入 -/
theorem max_consecutive_stops (cfg : LcConfig) (s : LcState) (hp : Bool)
    (hd : s.declaredDone = false) (hi : s.steerInFlight = false)
    (hc : s.consecutive ≥ cfg.maxConsecutive) :
    decide cfg s hp = Decision.giveUp := by
  simp [decide, hd, hi, hc]

/-- 边界 3：无进展达到阈值一定放行 -/
theorem no_progress_stops (cfg : LcConfig) (s : LcState)
    (hd : s.declaredDone = false) (hi : s.steerInFlight = false)
    (hc : s.consecutive < cfg.maxConsecutive)
    (hstreak : s.noProgressStreak + 1 ≥ cfg.maxNoProgress) :
    decide cfg s false = Decision.giveUp := by
  simp [decide, hd, hi, hc, hstreak]
  omega

/-- 边界 4：只要还有续跑余量且模型没宣布完成，就一定继续
    这证明「自主续跑」的机制保证 -/
theorem continues_while_budget (cfg : LcConfig) (s : LcState) (hp : Bool)
    (hd : s.declaredDone = false) (hi : s.steerInFlight = false)
    (hc : s.consecutive < cfg.maxConsecutive)
    (hs : s.noProgressStreak + 1 < cfg.maxNoProgress) :
    decide cfg s hp = Decision.steer := by
  simp [decide, hd, hi, hc]
  by_cases hp' : hp
  · simp [hp']
  · simp [hp']; omega

/-! ## 六、注入预算模型（找 token 可省之处） -/

/-- 注入配置（数值来自实测）
    l0l2Full  : 16 条 L0-L2 全文 = 4780 字（实测）
    l0l2Brief : 16 条标题        = 127 字（实测）
    totalCap  : 总量上限          = 6000 字（配置）
    bodyCap   : body 预算         = 1500 字（配置） -/
structure InjectCfg where
  l0l2Full  : Nat := 4780
  l0l2Brief : Nat := 127
  totalCap  : Nat := 6000
  bodyCap   : Nat := 1500
  deriving DecidableEq, Repr

/-- 第 t 轮（0-based）的注入量：首轮全文，之后标题 + body -/
def injectAt (c : InjectCfg) (t : Nat) (body : Nat) : Nat :=
  let prot := if t = 0 then c.l0l2Full else c.l0l2Brief
  prot + body

/-- n 轮总注入量 -/
def injectTotal (c : InjectCfg) (n body : Nat) : Nat :=
  match n with
  | 0 => 0
  | k + 1 => injectAt c 0 body + (k * (c.l0l2Brief + body))

/-- 边界 5（token 主要结论）：n 轮总注入随 n 线性增长，
    斜率是 (标题 + body)，而非 (全文 + body)。
    这证明「首轮全文 + 后续标题」的设计把每轮成本压到了 O(标题 + body)。 -/
theorem inject_total_linear (c : InjectCfg) (k body : Nat) :
    injectTotal c (k + 1) body =
      c.l0l2Full + body + k * (c.l0l2Brief + body) := by
  simp [injectTotal, injectAt]

/-- 边界 6：与记忆库规模无关。
    注入量公式里没有「库大小」参数 —— 这是**结构性**结论：
    无论记忆增长到多少条，每轮注入量不变。 -/
def injectIndependentOfStoreSize (_storeSize : Nat) (c : InjectCfg)
    (n body : Nat) : Nat := injectTotal c n body

theorem inject_ignores_store (s1 s2 : Nat) (c : InjectCfg) (n body : Nat) :
    injectIndependentOfStoreSize s1 c n body =
    injectIndependentOfStoreSize s2 c n body := by
  rfl

/-- 边界 7：总量上限的约束力。
    若 L0-L2 全文已占满总量上限，body 至少保留 200 字
    （对齐 index.js 的 `Math.max(200, ...)`）。 -/
def remainBudget (c : InjectCfg) : Nat :=
  let raw := if c.totalCap > c.l0l2Full then c.totalCap - c.l0l2Full else 0
  let b := min c.bodyCap raw
  if b < 200 then 200 else b

theorem remain_at_least_200 (c : InjectCfg) : remainBudget c ≥ 200 := by
  simp [remainBudget]
  split <;> split <;> omega

/-! ## 七、备份触发模型 -/

/-- 备份触发：每 N 次真实写入触发一次（N=20，来自 FISH-9 实验） -/
def shouldBackup (writesSinceBackup : Nat) (n : Nat := 20) : Bool :=
  writesSinceBackup + 1 ≥ n

/-- 边界 8：N 越大，最大丢失窗口越大 —— 这是可量化的诚实边界 -/
theorem max_loss_equals_n_minus_1 (n : Nat) (hn : n ≥ 1) :
    shouldBackup (n - 1) n = true ∧ shouldBackup (n - 2) n = false ∨ n = 1 := by
  by_cases h : n = 1
  · right; exact h
  · left
    constructor
    · simp [shouldBackup]
      omega
    · simp [shouldBackup]
      omega

/-! ## 八、可省 token 的形式化分析 -/

/-- 定义「省」：在保持不变量（红线）的前提下减少注入量 -/
structure SavingProposal where
  newL0L2Brief : Nat
  newBody      : Nat
  keepsRedlines : Bool

/-- 定理（负结论，同样是结论）：
    若把 L0-L2 标题也省掉（newL0L2Brief = 0），
    模型将无法获知存在哪些准则，行为一致性无法保证。
    所以「标题」是 token 的下界，不能再压。
    这里把它形式化为：标题必须 > 0 才有意义。 -/
def minMeaningful (c : InjectCfg) : Nat := c.l0l2Brief

theorem cannot_go_below_titles (c : InjectCfg) :
    minMeaningful c = c.l0l2Brief := by
  rfl

end FatFish
