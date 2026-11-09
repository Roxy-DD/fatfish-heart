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
  message: '',
  debugLog: false,
}

const DEFAULT_MESSAGE = [
  '### 继续自主迭代',
  '',
  '当前回合已结束，但任务尚未完成。请继续执行。',
  '',
  '判断依据：',
  '- 上一轮是否真的完成了某个**可验证**的目标？"提交+总结"不算完成',
  '- 是否还有可验证的下一步？有就直接做，不要写"下一步我会做X"',
  '- 只有在真正无剩余工作、或必须用户提供信息时才停止',
  '',
  '如果确实没有值得继续的工作，请明确说明并结束。',
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

        // 防重入：同一 turn 只 steer 一次
        if (s.lastSteerTurn === turn) return

        // 上限
        if (s.consecutive >= conf.maxConsecutive) {
          log('lifecycle-max-reached', {
            turn, consecutive: s.consecutive,
            totalSteers: s.totalSteers,
          })
          return
        }

        // 进展检测
        const tc = countToolCalls(agent)
        if (conf.requireProgress && s.lastToolCalls >= 0 &&
            tc <= s.lastToolCalls) {
          log('lifecycle-no-progress', {
            turn, toolCalls: tc, last: s.lastToolCalls,
          })
          return
        }

        // steer
        const text = conf.message || DEFAULT_MESSAGE
        agent.steer(makeUserMessage(text))

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
