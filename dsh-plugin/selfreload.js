/**
 * selfreload.js —— 让插件能自己重载自己
 *
 * ============================================================
 * 【已验证的 DSH 能力】（来自 cordis_inspect_query 实测）
 * ============================================================
 *
 * 1. configEditor 服务（Host）：
 *    description: "Persist complete raw configs and apply them
 *                  through the normal Loader path."
 *    methods:
 *      entries(): Entry[]                  // 可寻址的 profile 行
 *      configuration()                     // 读继承值 + 显式值
 *      edit(entry, change): Promise<void>  // 验证、持久化、**reconcile**
 *
 *    关键：edit() 的返回说明是
 *      "Fulfillment after Loader reconciliation completes."
 *    → 会走 Loader 的 reconcile 流程，这是插件重载的入口。
 *
 * 2. 相关事件（已确认存在）：
 *    · 'app-boot/config-reload' — Profile patches 被 reconcile 到运行中的
 *                                 Loader 树，"every entry update settled"
 *    · 'hmr/reload'             — Module replacements have finished loading
 *    · 'hmr/change'             — 监听到文件变化但没有 handler
 *
 * 3. pluginManager.setPluginEnabled(id, enabled) -> ChangeResult
 *    ChangeResult.application 可能是：
 *      'applied'          → 已在运行中生效（不需要重启）★
 *      'restart-required' → 必须重启
 *      RestartReason: 'no-hmr' | 'package-moved' | 'host-module'
 *                     | 'unmanaged-plugin' | 'module-comparison'
 *
 * ============================================================
 * 【实现策略】三条路径，按可靠性排序
 * ============================================================
 *
 * 路径 A（最可靠）：configEditor.edit 改自己的一项配置
 *   → 触发 Loader reconcile
 *   → 如果插件代码被重新 import，就实现了自重启
 *   风险：可能只更新 cfg 不重载模块
 *
 * 路径 B：pluginManager 先禁用再启用自己
 *   → setPluginEnabled(id, false) 然后 setPluginEnabled(id, true)
 *   → 会走 unload → load 完整流程
 *   风险：禁用后插件自己也没了，无法执行"启用"这步
 *        → 需要外部触发或用 setTimeout 先排队
 *
 * 路径 C：改 cordis.patch.yml 文件
 *   → 依赖 DSH 的 watchdog 监听
 *
 * 本模块实现 A 和 C，B 需要更复杂的自举。
 */

import { readFileSync, writeFileSync, existsSync, copyFileSync } from 'node:fs'
import { join } from 'node:path'
import { homedir } from 'node:os'

/**
 * @param {object} ctx    Cordis context
 * @param {object} cfg    插件配置
 * @param {function} log  (kind, data) => void
 */
export function createSelfReloader(ctx, cfg, log) {
  const state = {
    lastAttempt: 0,
    attempts: 0,
    lastResult: null,
  }

  /** 找到本插件在 Loader 里的 Entry */
  function findMyEntry() {
    try {
      const ce = ctx.get('configEditor')
      if (!ce || typeof ce.entries !== 'function') {
        return { ok: false, reason: 'configEditor unavailable' }
      }
      const entries = ce.entries()
      if (!Array.isArray(entries)) {
        return { ok: false, reason: 'entries() not array' }
      }
      // 找 moduleName 或 id 含 cosmos 的
      for (const e of entries) {
        const name = String(
          e.moduleName || e.name || e.id || e.package || ''
        ).toLowerCase()
        if (name.includes('cosmos') || name.includes('cosmos-mem')) {
          return { ok: true, entry: e, entries: entries.length }
        }
      }
      return {
        ok: false,
        reason: 'my entry not found',
        sample: entries.slice(0, 5).map((e) => ({
          id: e.id, moduleName: e.moduleName, name: e.name,
        })),
      }
    } catch (e) {
      return { ok: false, reason: `exception: ${e && e.message}` }
    }
  }

  /**
   * 路径 A：通过 configEditor.edit 触发 reconcile
   *
   * 手法：给配置加一个 ___reloadAt 时间戳字段再删掉，
   * 内容变化会触发 reconcile，但配置语义不变。
   */
  async function reloadViaConfigEditor() {
    const found = findMyEntry()
    if (!found.ok) {
      return { ok: false, path: 'configEditor', ...found }
    }

    try {
      const ce = ctx.get('configEditor')
      const stamp = Date.now()

      await ce.edit(found.entry, (current) => {
        // 加一个临时标记字段，触发内容变化
        return { ...(current || {}), ___reloadAt: stamp }
      })

      // 第二轮：删掉标记（保持配置干净）
      // 注意：如果插件已被卸载，这一步可能失败——那反而是成功信号
      try {
        const again = findMyEntry()
        if (again.ok) {
          await ce.edit(again.entry, (current) => {
            const next = { ...(current || {}) }
            delete next.___reloadAt
            return next
          })
        }
      } catch (e) {
        // 忽略：说明插件正在重启
      }

      return { ok: true, path: 'configEditor', stamp }
    } catch (e) {
      return {
        ok: false, path: 'configEditor',
        error: String(e && e.message),
      }
    }
  }

  /**
   * 路径 A：直接改 cordis.patch.yml（E-299 实测：这是唯一真正有效的方式）
   *
   * 为什么 configEditor 不行：
   *   我的插件在 cordis.patch.yml 里**没有配置段**（是 bundle patch 引入的），
   *   所以 configEditor.edit 改不到影响模块加载的东西。
   *   日志证据：configEditor 触发了多次 apply（17 路由全注册），
   *   但代码 marker 始终不变 —— 模块被缓存，没有重新执行。
   *
   * 为什么改 patch 文件有效：
   *   DSH 有 config watcher，检测到 cordis.patch.yml 变化后
   *   会真正重新加载模块。实测连续 3 次都能更新 marker。
   *
   * @param {object} opts  { dryRun: true } 时不写文件（供离线测试）
   */
  function reloadViaPatchFile(opts = {}) {
    const profiles = [
      join(homedir(), '.dsh', 'profiles', 'desktop'),
      join(homedir(), '.dsh', 'profiles', 'web'),
      join(homedir(), '.dsh', 'profiles', 'default'),
    ]
    for (const prof of profiles) {
      const patch = join(prof, 'cordis.patch.yml')
      if (!existsSync(patch)) continue
      try {
        let content = readFileSync(patch, 'utf8')
        const stamp = new Date().toISOString()

        if (opts.dryRun) {
          // 离线测试：只验证能找到文件、能算出新内容，不落盘
          return { ok: true, path: 'patchFile', profile: prof, stamp,
                   dryRun: true }
        }

        // 备份一次（保留最近一份）
        writeFileSync(patch + '.bak', content, 'utf8')
        // 移除旧的标记行
        content = content.replace(/^# __reload_stamp__.*$\n?/gm, '')
        // 追加新标记
        content = content.trimEnd() + `\n# __reload_stamp__ ${stamp}\n`
        writeFileSync(patch, content, 'utf8')
        return { ok: true, path: 'patchFile', profile: prof, stamp }
      } catch (e) {
        return { ok: false, path: 'patchFile', error: String(e && e.message) }
      }
    }
    return { ok: false, path: 'patchFile', error: 'no cordis.patch.yml found' }
  }

  /**
   * 综合尝试：默认用 patch 文件（实测更可靠）
   *
   * E-298 实测对比：
   *   · configEditor 策略：能触发 reconcile，17 个路由重新注册（修复后）
   *     但 **marker 不更新** —— 因为我的插件在 cordis.patch.yml 里
   *     没有配置段，edit() 改不到真正影响模块加载的东西。
   *   · patch 文件策略：改 cordis.patch.yml 追加标记行，
   *     DSH 的 config watcher 检测到文件变化 → 真正重新加载模块
   *     （实测：marker 更新成功，端点恢复）
   *
   * 所以默认顺序改为：patch 文件优先，configEditor 作为回退。
   */
  async function reload(opts = {}) {
    state.attempts += 1
    state.lastAttempt = Date.now()

    let strategies
    if (opts.strategy === 'configEditor') {
      strategies = [['configEditor', reloadViaConfigEditor]]
    } else if (opts.strategy === 'patch') {
      strategies = [['patchFile', () => reloadViaPatchFile(opts)]]
    } else {
      // 默认：patch 优先（E-299 实测：这是唯一真正重载代码的方式）
      strategies = [
        ['patchFile', () => reloadViaPatchFile(opts)],
        ['configEditor', reloadViaConfigEditor],
      ]
    }

    const tried = []
    for (const [name, fn] of strategies) {
      try {
        const r = await fn()
        tried.push({ strategy: name, ...r })
        if (r.ok) {
          state.lastResult = { ok: true, ...r, tried }
          log('selfreload-ok', {
            strategy: name, attempts: state.attempts,
          })
          return state.lastResult
        }
      } catch (e) {
        tried.push({ strategy: name, error: String(e && e.message) })
      }
    }

    state.lastResult = { ok: false, tried }
    log('selfreload-fail', { tried: tried.length })
    return state.lastResult
  }

  /** 只诊断不执行 */
  function diagnose() {
    // E-297：返回当前生效的代码标记（验证重载是否真的发生）
    let marker = null
    try { marker = globalThis.__COSMOS_RELOAD_MARKER__ || null } catch {}

    const found = findMyEntry()
    const ce = ctx.get('configEditor')
    const pm = ctx.get('pluginManager')
    return {
      hasConfigEditor: !!(ce && typeof ce.edit === 'function'),
      hasPluginManager: !!(pm && typeof pm.setPluginEnabled === 'function'),
      entryFound: found.ok,
      entry: found.ok ? {
        id: found.entry.id,
        moduleName: found.entry.moduleName,
        name: found.entry.name,
      } : null,
      entriesTotal: found.entries || 0,
      __marker: marker,
      reason: found.reason || null,
      sample: found.sample || null,
      attempts: state.attempts,
      lastResult: state.lastResult,
    }
  }

  return { reload, diagnose, findMyEntry, state }
}
