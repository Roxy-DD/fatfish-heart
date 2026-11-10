/**
 * lifecycle.js —— 自主生命周期管理（基于实测验证的 DSH API）
 *
 * ============================================================
 * 【已验证的 API 事实】（全部来自 DSH 实际代码，非文档推测）
 * ============================================================
 *
 * 1. 回合循环（dsh-agent-loop/lib/index.js）：
 *      if (turnEnds && this.inbox.nextStep.length === 0) {
 *          await this.dispatch.serial("agent/turn-stopping", { turn, signal });
 *          signal.throwIfAborted();
 *      }
 *      if (turnEnds && this.inbox.nextStep.length === 0) break; // 只有仍空才结束
 *      target = "next-step";                                     // 否则继续
 *
 *    → 在 turn-stopping 里往 inbox.nextStep 推东西，回合**不会结束**。
 *
 * 2. Agent 的 send/steer（同文件）：
 *      send(message, target, wakeup) {
 *          this.inbox.splice(resolvedTarget, Infinity, 0, [message]);
 *          if (wakeup) this.wakeDriver(...);
 *      }
 *      steer(input) { this.send(input, "next-step", true); }  // 推到 nextStep
 *
 * 3. 事件签名（api-catalog.js）：
 *      'agent/turn-stopping'(payload: { agent: Agent; turn: number; signal: AbortSignal })
 *
 * 4. 消息结构（dsh-llm/lib/index.js 实测）：
 *      createUserMessage(input) => deepFreeze(structuredClone({
 *          ...input, role: "user", id: brandString(randomUUID())
 *      }))
 *
 *    → 只是加了 role 和 id 的普通对象。
 *      **不需要 import dsh-llm**，自己构造等价对象即可，
 *      避免 asar 内部模块无法解析的问题。
 *
 * ============================================================
 * 【安全边界】
 * ============================================================
 * 无限空转是最大风险。强制约束：
 *   · 最大连续续跑次数（默认 20）
 *   · 两次续跑间必须检测到实质进展（工具调用数增加）
 *   · 同一 turn 只 steer 一次（防事件重入）
 *   · 用户发新消息时清零
 *   · 达到上限后放行并记录原因
 *
 * 【设计原则】
 * Supervisor 不决定"做什么"，只确保"还有机会继续"。
 */

const DEFAULT_CONFIG = {
  enabled: true,
  maxConsecutive: 20,
  requireProgress: true,
  // E-356：连续多少个「无工具调用」回合后才真正放行。
  // 原逻辑是「1 次无进展就放行」，导致一轮纯文本总结后永久停止。
  maxNoProgress: 3,
  // E-415/416：每多少轮做一次结构化汇报（0 = 关闭）。
  //
  // 标定依据（E-416 实测，不是拍脑袋）：
  //   普通模板 72 字（每轮注入） / 汇报模板 120 字（低频）
  //   假设 30 轮会话，约束「最长静默 <= 10 轮」（用户体验下限）
  //
  //   N    汇报次数   最长静默   steer 总字符
  //   1       30         1         3600
  //   5        6         5         2448
  //   8        3         8         2304  ← 可行解里最省
  //   10       3        10         2304
  //   15       2        15         2256  ← 超出静默上限
  //
  // N=8 与 N=10 成本相同（2304），选 8 因为静默更短。
  reportEvery: 8,
  message: '',
  debugLog: false,
}

/**
 * 普通轮次的 steer 消息（E-415）
 *
 * ★ 关键设计：这一条会被**重复注入几十次**，
 *   所以必须极短。它只负责三件事：
 *     ① 提醒任务没完成
 *     ② 给出停止判据
 *     ③ 约束输出长度
 *
 * 详细的行为准则放在 system prompt 里（只注入一次），
 * 不在这里重复 —— 否则每轮都在付同样的 token。
 */
const DEFAULT_MESSAGE = [
  '### 继续',
  '',
  '任务未完成。直接做下一步，不要复述计划。',
  '停止条件：无剩余可验证工作，或需要用户信息。',
  '输出：1-2 句说明做了什么，不写过程。',
].join('\n')

/**
 * 周期汇报消息（E-415）
 *
 * 每 N 轮触发一次，要求结构化汇报。
 * 因为它出现频率低（1/5），可以比普通消息长一些。
 */
const REPORT_MESSAGE = [
  '### 周期汇报点',
  '',
  '先按下面四行汇报，然后直接继续，不要等确认：',
  '',
  '```',
  '进度: <已完成的可验证项>',
  '发现: <真结论，无则写"无">',
  '卡点: <阻塞项，无则写"无">',
  '下一步: <一句话>',
  '```',
  '',
  '每行不超过 40 字，不写过程。',
].join('\n')

/** 生成一个消息 id（等价于 DSH 内部的 brandString(randomUUID())） */
function genId() {
  try {
    // Node 22+ 有 crypto.randomUUID
    return globalThis.crypto.randomUUID()
  } catch (e) {
    // 退化实现
    return 'lc-' + Date.now().toString(36) + '-' +
      Math.random().toString(36).slice(2, 10)
  }
}

/**
 * 构造 user 消息（等价于 dsh-llm 的 createUserMessage，但不依赖 import）
 */
function makeUserMessage(text, kind = 'cosmos-mem-lifecycle') {
  const msg = {
    role: 'user',
    id: genId(),
    content: [{ type: 'text', text }],
    source: { kind },
  }
  // 尽量冻结（DSH 内部会 deepFreeze，这里做浅冻结足够）
  try {
    msg.content.forEach((c) => Object.freeze(c))
    Object.freeze(msg.content)
    Object.freeze(msg.source)
    return Object.freeze(msg)
  } catch (e) {
    return msg
  }
}

/**
 * 创建生命周期监督器
 * @param {object} cfg  插件配置
 * @param {function} log (kind, data) => void
 */
export function createLifecycleSupervisor(cfg, log) {
  const conf = { ...DEFAULT_CONFIG, ...(cfg.lifecycle || {}) }
  // 按 agent 隔离（E-254 教训：不用模块级共享状态）
  const states = new WeakMap()

  function dbg(kind, data) {
    if (conf.debugLog) log(kind, data)
  }

  function stateOf(agent) {
    let s = states.get(agent)
    if (!s) {
      s = {
        consecutive: 0,
        lastToolCalls: -1,
        lastSteerTurn: -1,
        totalSteers: 0,
        // E-356：连续无进展次数；达到 maxNoProgress 才真正放行
        noProgressStreak: 0,
        // E-371：steer 是否正在进行中（替代 turn 号防重入）
        steerInFlight: false,
      }
      states.set(agent, s)
    }
    return s
  }

  /** 工具调用计数（用于进展检测） */
  function countToolCalls(agent) {
    try {
      const sess = agent.session || agent.state
      if (!sess) return 0
      // 尝试多种可能的接口
      if (Array.isArray(sess.events)) {
        let n = 0
        for (const e of sess.events) {
          if (e && (e.type === 'tool/call' || e.type === 'tool-call')) n++
        }
        return n
      }
      if (typeof sess.length === 'number') return sess.length
      if (typeof sess.size === 'number') return sess.size
      return 0
    } catch (e) {
      return 0
    }
  }

  function attach(ctx) {
    if (!conf.enabled) {
      log('lifecycle-disabled', {})
      return
    }

    ctx.on('agent/turn-stopping', async (payload) => {
      try {
        const agent = payload && payload.agent
        const turn = payload && payload.turn
        if (!agent || typeof agent.steer !== 'function') {
          dbg('lifecycle-no-steer', { hasAgent: !!agent })
          return
        }

        const s = stateOf(agent)

        // 防重入（E-371 修：原实现会静默吃掉所有后续 steer）
        //
        // 原实现：
        //   if (s.lastSteerTurn === turn) return      // 静默 return，无日志
        //
        // 致命问题（从 DSH 源码 + 实测日志确认）：
        //   turn() 是 while(true) 循环，turn 号在进入循环前定好：
        //       const turn = phase.turn + 1;
        //       phase.turn = turn;
        //       while (true) {
        //           ...
        //           target = "next-step";    // steer 后走这里，**turn 不变**
        //       }
        //
        //   → steer 一次后继续工作，仍在一个 turn 里；
        //     工作完再次结束时，dispatch 的 turn 号还是同一个。
        //   → lastSteerTurn === turn 成立 → 静默 return → 永远不再 steer。
        //
        // 实测证据（E-368/E-371）：
        //   15:19:36  steer(turn=272)，lastSteerTurn=272
        //   15:19:47~15:22:43  工作 23 步（turn 仍=272）
        //   15:22:43  再次结束 → 静默 return → 零日志 → 停止
        //
        // 修法：
        //   允许同一 turn 内多次 steer，靠 consecutive 计数封顶
        //   （maxConsecutive 默认 20，足够安全）。
        //   真正的防重入需求是「同一次 dispatch 内不重复 steer」，
        //   这个用 in-flight 标记处理，而不是用 turn 号。
        if (s.steerInFlight) {
          log('lifecycle-reentry-skip', { turn })
          return
        }

        // 上限
        if (s.consecutive >= conf.maxConsecutive) {
          log('lifecycle-max-reached', {
            turn, consecutive: s.consecutive,
            totalSteers: s.totalSteers,
          })
          return
        }

        // 进展检测（E-356 修）
        //
        // 原实现：
        //   const tc = countToolCalls(agent)
        //   if (requireProgress && s.lastToolCalls >= 0 &&
        //       tc <= s.lastToolCalls) {
        //     log('lifecycle-no-progress', ...); return    // ← 直接放弃
        //   }
        //
        // 实际行为（读日志核对过，不是推测）：
        //   · lastToolCalls 初始 -1，第一次 tc(0) > -1 → 通过，正常 steer
        //     （steer 后 lastToolCalls 被设成 0）
        //   · 第二次纯文本回合 tc(0) <= 0 → 放弃
        //
        // 所以它不是"第一次就失效"，而是：
        //   **纯文本的回合最多只能被续跑一次，之后就永远放行**。
        //
        // 实测证据（E-356）：
        //   06:29:11 lifecycle-steer        {turn:269}  ← 第一次，通过
        //   06:57:22 lifecycle-no-progress  {turn:271, toolCalls:0, last:0}
        //   → 我上一轮说「接下来继续迭代」然后纯文本结束，被放行
        //
        // 根本问题：**"没有工具调用" ≠ "任务完成了"**
        //   纯文字回答（总结、汇报）也可能任务没完就停了。
        //
        // 新语义：
        //   · 有实质进展（工具调用增加）→ 续跑，noProgressStreak 归零
        //   · 没有进展（纯文本）→ **仍然续跑**，但累计 noProgressStreak，
        //     连续达到 maxNoProgress（默认 3）才真正放行
        //   · 这样纯文本回合也能被连续追几次，而不是一次后就永久放行
        const tc = countToolCalls(agent)
        const hasProgress = !(s.lastToolCalls >= 0 && tc <= s.lastToolCalls)

        if (conf.requireProgress && !hasProgress) {
          s.noProgressStreak = (s.noProgressStreak || 0) + 1
          const limit = conf.maxNoProgress ?? 3
          if (s.noProgressStreak >= limit) {
            log('lifecycle-no-progress', {
              turn, toolCalls: tc, last: s.lastToolCalls,
              streak: s.noProgressStreak, limit,
              action: 'give-up',
            })
            return
          }
          log('lifecycle-no-progress', {
            turn, toolCalls: tc, last: s.lastToolCalls,
            streak: s.noProgressStreak, limit,
            action: 'continue-anyway',
          })
        } else {
          s.noProgressStreak = 0
        }

        // steer
        //
        // E-371：用 steerInFlight 做真正的防重入（同步置位、同步释放）。
        // steer() 是同步调用（只是往 inbox 推消息），所以置位后立即释放即可；
        // 它的作用是防止同一次 dispatch 内重入，而不是阻止同一 turn 的多次
        // 合法续跑（turn 号在 steer 后不会变，用 turn 号做防重入会永久锁死）。
        //
        // E-415：周期性插入汇报请求，而不是每轮都要求总结。
        //   普通轮次 → 静默继续（省 token）
        //   每 reportEvery 轮 → 结构化汇报（累积成果）
        const reportEvery = conf.reportEvery ?? 5
        const isReportTurn = reportEvery > 0 &&
          s.consecutive > 0 &&
          s.consecutive % reportEvery === 0

        s.steerInFlight = true
        try {
          const text = isReportTurn
            ? REPORT_MESSAGE
            : (conf.message || DEFAULT_MESSAGE)
          agent.steer(makeUserMessage(text))
          if (isReportTurn) {
            log('lifecycle-report-turn', {
              turn, consecutive: s.consecutive,
              every: reportEvery,
            })
          }
        } finally {
          s.steerInFlight = false
        }

        s.consecutive += 1
        s.totalSteers += 1
        s.lastToolCalls = tc
        s.lastSteerTurn = turn

        log('lifecycle-steer', {
          turn, consecutive: s.consecutive, toolCalls: tc,
        })
      } catch (e) {
        log('lifecycle-error', { error: String(e && e.message) })
      }
    })

    // 用户主动发消息 → 清零（用户优先级最高）
    const reset = (payload) => {
      try {
        const agent = payload && payload.agent
        if (!agent) return
        const s = stateOf(agent)
        if (s.consecutive > 0) {
          log('lifecycle-reset', { was: s.consecutive })
        }
        s.consecutive = 0
        s.lastToolCalls = -1
        s.noProgressStreak = 0
      } catch (e) { /* ignore */ }
    }
    ctx.on('agent/user-message', reset)
    ctx.on('agent/followup', reset)

    log('lifecycle-attached', {
      maxConsecutive: conf.maxConsecutive,
      requireProgress: conf.requireProgress,
    })
  }

  return { attach, conf, stateOf, countToolCalls, makeUserMessage }
}
