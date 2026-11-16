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

import { readFileSync, writeFileSync, existsSync, readdirSync, unlinkSync,
         statSync }
  from 'node:fs'
import { join, dirname, basename } from 'node:path'
import { fileURLToPath } from 'node:url'
import { homedir } from 'node:os'

/** 去掉 reload 标记行，用于比较"正文是否变化" */
function stripStamps(s) {
  return String(s || '')
    .split('\n')
    .filter((line) => !line.startsWith('# __reload_stamp__'))
    .join('\n')
    .trimEnd()
}

/** 只保留最近 N 份备份，删更旧的 */
function pruneOldBackups(patchPath, keep = 5) {
  try {
    const dir = dirname(patchPath)
    const base = basename(patchPath) + '.cosmos-bak-'
    const files = readdirSync(dir)
      .filter((f) => f.startsWith(base))
      .sort()   // 时间戳命名，字典序即时间序
    while (files.length > keep) {
      const victim = files.shift()
      try { unlinkSync(join(dir, victim)) } catch { /* ignore */ }
    }
  } catch { /* ignore */ }
}

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
  async function reloadViaConfigEditor(opts = {}) {
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

      return { ok: true, path: 'configEditor', stamp, dryRun: !!opts.dryRun }
    } catch (e) {
      return {
        ok: false, path: 'configEditor',
        error: String(e && e.message),
      }
    }
  }

  /**
   * 路径 A：改 cordis.patch.yml 触发重载（E-299 实测唯一有效）
   *
   * E-301 改进：不再直接污染用户的配置文件
   *
   * 原做法的问题（实测数据）：
   *   · 每次 reload 都改写用户 26KB 的 cordis.patch.yml，只为加一行注释
   *   · .bak 被反复覆盖（第2次覆盖第1次、第3次覆盖第2次）
   *     → 连续 3 次后，.bak 里是第 2 次的内容，不是最初的原件
   *     → 若第 3 次写坏，无法回滚到干净状态
   *
   * 新做法：
   *   1. 只在**文件末尾**操作标记行（不动正文），正文保持逐字节一致
   *   2. .bak 用时间戳命名，保留历史（最多 5 份，超出删最旧）
   *   3. 写完立即读回校验：正文必须与原文件逐字节一致
   *   4. 校验失败则自动用备份回滚
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
        const original = readFileSync(patch, 'utf8')
        const stamp = new Date().toISOString()

        // 去掉正文里的旧标记，得到"干净正文"
        const cleanBody = original
          .replace(/^# __reload_stamp__.*\n?/gm, '')
          .trimEnd()

        const nextContent = cleanBody + `\n# __reload_stamp__ ${stamp}\n`

        if (opts.dryRun) {
          // 离线测试：只计算，不写盘
          return {
            ok: true, path: 'patchFile', profile: prof, stamp,
            dryRun: true,
            bodyUnchanged: stripStamps(original) === stripStamps(nextContent),
          }
        }

        // ---- 1. 带时间戳的备份（保留历史）----
        const ts = stamp.replace(/[:.]/g, '-')
        const bak = `${patch}.cosmos-bak-${ts}`
        writeFileSync(bak, original, 'utf8')
        pruneOldBackups(patch, 5)

        // ---- 2. 写入 ----
        writeFileSync(patch, nextContent, 'utf8')

        // ---- 3. 读回校验：正文必须与原文件一致 ----
        const verify = readFileSync(patch, 'utf8')
        const bodyOk = stripStamps(verify) === stripStamps(original)
        if (!bodyOk) {
          // 校验失败 → 用备份回滚
          try {
            writeFileSync(patch, original, 'utf8')
          } catch { /* ignore */ }
          return {
            ok: false, path: 'patchFile',
            error: '正文校验失败，已回滚', rolledBack: true,
          }
        }

        return {
          ok: true, path: 'patchFile', profile: prof, stamp,
          backup: bak, bodyUnchanged: true,
        }
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
      strategies = [['configEditor', () => reloadViaConfigEditor(opts)]]
    } else if (opts.strategy === 'patch') {
      strategies = [['patchFile', () => reloadViaPatchFile(opts)]]
    } else {
      // 默认：patch 优先（E-299 实测：这是唯一真正重载代码的方式）
      strategies = [
        ['patchFile', () => reloadViaPatchFile(opts)],
        ['configEditor', () => reloadViaConfigEditor(opts)],
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

    // E-480：磁盘代码是否比运行中的新？
    //
    // 为什么需要这个：
    //   E-479 排查时，我改完 index.js 后 /selfreload 报 reloaded=True，
    //   但工具始终不可用。最后发现 DSH 进程启动于 10:47，
    //   而改动写入是 12:35 —— 进程在跑一个多小时前的代码。
    //
    //   「reload 返回成功」和「代码真的在用」是两件事。
    //   原来的 diagnose 只看 marker（是否重新执行过 apply），
    //   但如果从来没重载成功，marker 一直是旧的，看不出「落后多少」。
    //
    //   所以直接比对：磁盘文件 mtime vs 本次 apply 时间。
    //   如果磁盘更新 → 明确告诉用户「需要重启宿主」。
    let stale = null
    try {
      const here = fileURLToPath(import.meta.url)
      const selfPath = join(dirname(here), 'index.js')
      const diskMs = statSync(selfPath).mtimeMs
      // marker 形如 apply@2026-11-16T04:35:35.429Z#abc123
      let applyMs = null
      if (marker && marker.startsWith('apply@')) {
        const iso = marker.slice(6).split('#')[0]
        const t = Date.parse(iso)
        if (!Number.isNaN(t)) applyMs = t
      }
      const gapSec = applyMs === null ? null
        : Math.round((diskMs - applyMs) / 1000)
      stale = {
        diskMtime: new Date(diskMs).toISOString(),
        lastApply: applyMs === null ? null : new Date(applyMs).toISOString(),
        // >5 秒即视为落后（文件系统时间精度 + 写入延迟的余量）
        diskNewer: gapSec === null ? null : gapSec > 5,
        gapSec,
      }
    } catch (e) {
      stale = { error: String(e && e.message) }
    }

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
      // E-480：磁盘 vs 运行中
      stale,
      // 一句话结论，避免用户/模型自己推断
      verdict: (() => {
        if (!stale || stale.error) return '? 无法判断：' + (stale && stale.error)
        if (stale.diskNewer === true) {
          return '⚠ 磁盘代码比运行中的新（落后 ' + stale.gapSec
            + ' 秒）→ 必须重启 DSH 宿主才会生效，自重启无效'
        }
        if (stale.diskNewer === false) {
          return '✅ 运行中的代码就是磁盘上的最新版本'
        }
        // 没有 marker：本次运行从未执行过 apply()（或 marker 被清了）
        return '⚠ 没有 apply marker —— 当前进程可能从未加载过本插件的最新代码，'
          + '建议重启 DSH 宿主后重试'
      })(),
      reason: found.reason || null,
      sample: found.sample || null,
      attempts: state.attempts,
      lastResult: state.lastResult,
    }
  }

  /**
   * 带验证的重载（E-304）
   *
   * E-303/E-304 实测发现的陷阱：
   *   reload() 返回 ok=True **不代表代码真的重载了**。
   *   如果 index.js 的内容没变，Node 模块缓存命中，
   *   apply() 不会重新执行 —— 是一次空操作。
   *
   *   证据：
   *     · 只改 patch 注释      → 指纹不变（空操作）
   *     · 改 index.js 内容     → 指纹变化（真重载）
   *
   * 这个函数做三件事：
   *   1. 记录重载前的指纹
   *   2. 触发 reload
   *   3. 等 waitMs 后读新指纹，判定是否真的重载
   */
  async function reloadVerified(opts = {}) {
    const waitMs = opts.waitMs ?? 6000
    const readMarker = () => {
      try { return globalThis.__COSMOS_RELOAD_MARKER__ || null } catch { return null }
    }

    const markerBefore = readMarker()
    const r = await reload(opts)

    if (!r.ok) {
      return { ...r, reloaded: false, markerBefore, markerAfter: markerBefore }
    }
    if (opts.dryRun) {
      return {
        ...r, reloaded: null, markerBefore, markerAfter: markerBefore,
        hint: 'dryRun：未真正触发重载',
      }
    }

    await new Promise((res) => setTimeout(res, waitMs))
    const markerAfter = readMarker()
    const reloaded = !!(markerAfter && markerBefore &&
                        markerAfter !== markerBefore)

    return {
      ...r,
      reloaded,
      markerBefore,
      markerAfter,
      hint: reloaded
        ? '代码已真正重载'
        : '触发了 reconcile，但模块内容未变，Node 缓存命中 —— '
          + '代码没有重新执行。若刚改过代码，请确认文件确实已写入。',
    }
  }

  return { reload, reloadVerified, diagnose, findMyEntry, state }
}
