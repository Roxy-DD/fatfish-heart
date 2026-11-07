// check-client.mjs —— client.js 运行时安全检查（常驻）
//
// 背景：三次界面崩溃（E-253 白屏、SleepHistoryView 白屏、InjectHistoryView 白屏）
//       都是"语法通过但运行时崩"的类型：
//         · renderTab 作用域错误（闭包变量访问不到）
//         · 回调参数 h 遮蔽 React.createElement
//       这些 node -c 查不出来，必须专门检查。
//
// 用法：node check-client.mjs
// 退出码 0 = 通过，1 = 有问题

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const CLIENT = join(__dirname, '..', 'dsh-plugin', 'client.js');

let src;
try {
  src = readFileSync(CLIENT, 'utf8');
} catch (e) {
  console.error('❌ 读不到 client.js:', e.message);
  process.exit(1);
}

const problems = [];
const warn = (msg) => problems.push(msg);

// ============================================================
// 1. React.createElement 别名 h 不能被遮蔽
// ============================================================
const SHADOW_RE = [
  /\.(map|forEach|filter|some|every|find|reduce|sort)\(\s*\(\s*h\s*[,)]/g,
  /\.(map|forEach|filter|some|every|find|reduce|sort)\(\s*h\s*=>/g,
  /function\s+\w+\(\s*h\s*[,)]/g,
  /(?:const|let|var)\s+h\s*=/g,
];

// 第 33 行 const h = React.createElement 是正常定义，排除
// 注释里的示例代码也排除（形如 * const h = ... 或 // const h = ...）
const allowed = new Set();
const lines0 = src.split('\n');
lines0.forEach((l, i) => {
  const t = l.trim();
  if (/^const\s+h\s*=\s*React\.createElement/.test(t)) allowed.add(i + 1);
  if (/^(\*|\/\/)/.test(t)) allowed.add(i + 1);   // 注释行跳过
});

for (const re of SHADOW_RE) {
  let m;
  while ((m = re.exec(src)) !== null) {
    const line = src.slice(0, m.index).split('\n').length;
    if (allowed.has(line)) continue;
    warn(`h 被遮蔽（行 ${line}）: ${lines0[line - 1]?.trim().slice(0, 70)}`);
  }
}

// ============================================================
// 2. 函数作用域：内部函数必须能访问它引用的变量
// ============================================================
function findFunctionRange(text, fnName) {
  const start = text.indexOf(`function ${fnName}(`);
  if (start < 0) return null;
  let depth = 0, started = false;
  let inStr = false, strCh = '', inLine = false, inBlock = false, inTpl = false;
  for (let i = start; i < text.length; i++) {
    const c = text[i], n = text[i + 1];
    if (inLine) { if (c === '\n') inLine = false; continue; }
    if (inBlock) { if (c === '*' && n === '/') { inBlock = false; i++; } continue; }
    if (inStr) { if (c === '\\') { i++; continue; } if (c === strCh) inStr = false; continue; }
    if (inTpl) { if (c === '\\') { i++; continue; } if (c === '`') inTpl = false; continue; }
    if (c === '/' && n === '/') { inLine = true; i++; continue; }
    if (c === '/' && n === '*') { inBlock = true; i++; continue; }
    if (c === '"' || c === "'") { inStr = true; strCh = c; continue; }
    if (c === '`') { inTpl = true; continue; }
    if (c === '{') { depth++; started = true; }
    else if (c === '}') { depth--; if (started && depth === 0) return [start, i]; }
  }
  return null;
}

// renderTab / renderList 必须在 MemoryPage 内部
const mp = findFunctionRange(src, 'MemoryPage');
if (!mp) {
  warn('找不到 MemoryPage 函数');
} else {
  for (const inner of ['renderTab', 'renderList']) {
    const r = findFunctionRange(src, inner);
    if (!r) { warn(`找不到 ${inner} 函数`); continue; }
    if (r[0] < mp[0] || r[0] > mp[1]) {
      warn(`${inner} 定义在 MemoryPage 外部（无法访问闭包变量 tab/displayItems/cards）`);
    }
  }
}

// ============================================================
// 3. 所有使用的组件必须有定义
// ============================================================
const defined = new Set();
let m2;
const defRe = /function\s+(\w+View|\w+Page|\w+Row)\s*\(/g;
while ((m2 = defRe.exec(src)) !== null) defined.add(m2[1]);

const used = new Set();
const useRe = /h\((\w+View|\w+Page|\w+Row),/g;
while ((m2 = useRe.exec(src)) !== null) used.add(m2[1]);

for (const u of used) {
  if (!defined.has(u)) warn(`使用了未定义的组件: ${u}`);
}

// ============================================================
// 4. S.xxx 样式必须有定义
// ============================================================
const sBlock = src.match(/const S = \{[\s\S]*?\n {4}\};/);
if (!sBlock) {
  warn('找不到 S 样式定义');
} else {
  const sDefined = new Set();
  const sDefRe = /^\s{6}(\w+):/gm;
  let m3;
  while ((m3 = sDefRe.exec(sBlock[0])) !== null) sDefined.add(m3[1]);

  const sUsed = new Set();
  const sUseRe = /S\.(\w+)/g;
  while ((m3 = sUseRe.exec(src)) !== null) sUsed.add(m3[1]);

  for (const u of sUsed) {
    if (!sDefined.has(u)) warn(`引用了未定义的样式 S.${u}`);
  }
}

// ============================================================
// 5. renderTab 每个 tab 都要有分支
// ============================================================
const TABS = ['config', 'inject', 'sleep', 'audit', 'note', 'todo', 'timeline'];
const rt = findFunctionRange(src, 'renderTab');
if (rt) {
  const body = src.slice(rt[0], rt[1]);
  for (const t of TABS) {
    if (!body.includes(`tab === '${t}'`)) warn(`renderTab 缺少 tab='${t}' 分支`);
  }
}

// ============================================================
// 报告
// ============================================================
console.log('='.repeat(64));
console.log('client.js 运行时安全检查');
console.log('='.repeat(64));
console.log(`文件: ${CLIENT}`);
console.log(`大小: ${(src.length / 1024).toFixed(1)} KB`);
console.log('');

if (problems.length === 0) {
  console.log('✅ 全部检查通过');
  console.log('');
  console.log('检查项：');
  console.log('  · h 未被遮蔽（React.createElement 可用）');
  console.log('  · renderTab/renderList 在 MemoryPage 内部');
  console.log('  · 所有组件有定义');
  console.log('  · 所有 S.xxx 样式有定义');
  console.log('  · renderTab 分支完整');
  process.exit(0);
} else {
  console.log(`❌ 发现 ${problems.length} 个问题：\n`);
  problems.forEach((p, i) => console.log(`  ${i + 1}. ${p}`));
  process.exit(1);
}
