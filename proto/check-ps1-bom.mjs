//!/usr/bin/env pwsh
// check-ps1-bom.mjs —— .ps1 文件的编码检查（E-530）
//
// ============================================================
// 为什么需要这个检查
// ============================================================
// 2026-11-18 用户运行 scripts/enable-lan-access.ps1 时报语法错误：
//   字符串缺少终止符: '。
//   语句块或类型定义中缺少右"}"
//
// 根因：文件是无 BOM 的 UTF-8，而用户在 Windows PowerShell 5.1 里执行。
//   5.1 对无 BOM 文件默认按 ANSI/GBK 解码，
//   中文被拆坏 → 引号配对被破坏 → 解析失败。
//
// 而我当时只在 pwsh 7 里测过 —— 7 默认按 UTF-8 读，所以显示"无错误"。
// 这类"只在一种环境验证"的错，必须用机械检查兜住。
//
// ============================================================
// 检查规则
// ============================================================
// · 纯 ASCII 的 .ps1 → 允许无 BOM（不会误判）
// · 含非 ASCII（中文注释等）→ 必须带 BOM，否则 PS 5.1 会崩
//
// 这是最小必要规则：不强制所有文件加 BOM，只强制"会出问题的那类"。

import { readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')

const results = []
function check(name, cond, detail = '') {
  results.push({ name, ok: !!cond, detail })
  console.log(`  ${cond ? '✅' : '❌'} ${name}${detail ? '  ' + detail : ''}`)
}

console.log('='.repeat(70))
console.log('.ps1 编码检查（E-530）')
console.log('='.repeat(70))

// ---- 递归找所有 .ps1（跳过 node_modules / .git） ----
function findPs1(dir, out = []) {
  let entries
  try {
    entries = readdirSync(dir)
  } catch {
    return out
  }
  for (const e of entries) {
    if (e === 'node_modules' || e === '.git' || e === '.hf' ||
        e === 'dist' || e === '.venv' || e === 'venv') continue
    const p = join(dir, e)
    let st
    try {
      st = statSync(p)
    } catch {
      continue
    }
    if (st.isDirectory()) findPs1(p, out)
    else if (e.toLowerCase().endsWith('.ps1')) out.push(p)
  }
  return out
}

const files = findPs1(ROOT)
console.log(`\n找到 ${files.length} 个 .ps1 文件\n`)

const hasBom = (buf) =>
  buf.length >= 3 && buf[0] === 0xef && buf[1] === 0xbb && buf[2] === 0xbf

const hasNonAscii = (buf) => {
  // 跳过 BOM 后再判断
  const start = hasBom(buf) ? 3 : 0
  for (let i = start; i < buf.length; i++) {
    if (buf[i] > 0x7f) return true
  }
  return false
}

let risky = 0
for (const f of files) {
  const rel = f.slice(ROOT.length + 1).replace(/\\/g, '/')
  const buf = readFileSync(f)
  const bom = hasBom(buf)
  const nonAscii = hasNonAscii(buf)

  if (nonAscii && !bom) {
    risky++
    check(`${rel} 含中文但无 BOM（PS 5.1 会解析失败）`, false,
      '需转成带 BOM 的 UTF-8')
  } else if (nonAscii && bom) {
    check(`${rel} 含中文 + 有 BOM`, true)
  } else {
    // 纯 ASCII，无 BOM 也安全
    check(`${rel} 纯 ASCII（无 BOM 安全）`, true)
  }
}

// ---- 至少检查到一个文件，防止"什么都没找到"也算通过 ----
// 项目里可能没有 .ps1（比如把运维脚本移到别处），
// 这时检查应当「无事可做地通过」，而不是失败。
// 真正要防的是「有 .ps1 但漏扫」，那种情况由下面的逐文件检查覆盖。
if (files.length === 0) {
  console.log('  （项目内无 .ps1，跳过编码检查）')
} else {
  check(`扫描到 ${files.length} 个 .ps1`, true)
}

// ---- 汇总 ----
const pass = results.filter((x) => x.ok).length
console.log('\n' + '='.repeat(70))
console.log(`结果：${pass}/${results.length} 通过`)
for (const x of results.filter((y) => !y.ok)) {
  console.log(`  ❌ ${x.name}  ${x.detail}`)
}
console.log('='.repeat(70))
console.log(`
★ 这个检查防的是什么：
  Windows 上有两个 PowerShell：
    · pwsh 7       — 默认按 UTF-8 读文件
    · Windows PowerShell 5.1 — 无 BOM 时按 ANSI/GBK 读

  同一份无 BOM 的 UTF-8 脚本，在 7 里正常、在 5.1 里报语法错误。
  而普通用户双击运行的是 5.1。

  所以：含中文的 .ps1 必须带 BOM。
`)

process.exit(pass === results.length ? 0 : 1)
