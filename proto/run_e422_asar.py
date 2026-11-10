"""E-422 从 DSH asar 提取 agent-loop 源码，查 agent 到底有什么

问题：
  lifecycle.js 的 countToolCalls() 在猜接口：
    sess.events / sess.length / sess.size
  实测日志里 toolCalls 恒为 0，说明全猜错了。
  这导致"无进展"误判 → streak 到 3 → 永久 give-up。

不能继续猜，必须读源码确认 agent 的真实结构。

本脚本：
  ① 解析 asar 头部（JSON 目录表）
  ② 找到 dsh-agent-loop 的文件
  ③ 提取 index.js 内容并搜索关键符号
"""
import json
import struct
import re
import os

ASAR = r"C:\Users\xuee\AppData\Local\Programs\DeepSeek Harness\resources\app.asar"
OUT = r"C:\Users\xuee\Desktop\肥鱼之心\proto\results"

print("=" * 74)
print("E-422 从 asar 提取 agent-loop 源码")
print("=" * 74)

with open(ASAR, "rb") as f:
    data = f.read()

# ---------- ① 解析 asar 头部 ----------
# asar 格式：
#   4 bytes: uint32 = 4 (header size field size)
#   4 bytes: uint32 = header string size
#   4 bytes: uint32 = header 实际长度
#   4 bytes: uint32 = header JSON 长度
#   N bytes: header JSON

print(f"\n① asar 大小: {len(data)} 字节")

# 读前 16 字节
sig = data[0:4]
size_pickle = struct.unpack("<I", data[4:8])[0]
header_size = struct.unpack("<I", data[8:12])[0]
header_str_size = struct.unpack("<I", data[12:16])[0]

print(f"   header_size: {header_size}")
print(f"   header_str_size: {header_str_size}")

# JSON 从 offset 16 开始
json_bytes = data[16:16 + header_str_size]
try:
    header = json.loads(json_bytes.decode("utf-8"))
    print(f"   ✅ 头部解析成功，顶层 keys: {list(header.keys())[:5]}")
except Exception as e:
    print(f"   ❌ 头部解析失败: {e}")
    raise SystemExit(1)

# ---------- ② 找 dsh-agent-loop ----------
print("\n② 定位 dsh-agent-loop")

files = header.get("files", {})
dsh = files.get("dsh", {}).get("files", {})
node_modules = dsh.get("node_modules", {}).get("files", {})
ds_ai = node_modules.get("@deepseek-ai", {}).get("files", {})

print(f"   @deepseek-ai 下的包数: {len(ds_ai)}")

target_pkgs = [k for k in ds_ai if "agent" in k.lower()]
print(f"   含 agent 的包: {target_pkgs}")

# ---------- ③ 提取 agent-loop 的 index.js ----------
def find_file(header_files, path_parts):
    """在 asar 目录树里找文件节点"""
    node = {"files": header_files}
    for p in path_parts:
        fs = node.get("files", {})
        if p not in fs:
            return None
        node = fs[p]
    return node


def read_content(node):
    """按 offset/size 从 asar 读内容"""
    off = int(node.get("offset", 0))
    size = int(node.get("size", 0))
    # 数据区从 16 + header_str_size 开始，然后对齐到 4 字节
    base = 16 + header_str_size
    # asar 有 4 字节对齐
    base = (base + 3) & ~3
    return data[base + off: base + off + size]


TARGET = None
# 精确指定要读的包（不要按字母序碰运气）
WANT = "dsh-agent-loop"
node = find_file(
    header["files"],
    ["dsh", "node_modules", "@deepseek-ai", WANT, "lib", "index.js"])
if not node:
    node = find_file(
        header["files"],
        ["dsh", "node_modules", "@deepseek-ai", WANT, "index.js"])
if not node:
    # 列出该包的文件结构
    pkgnode = find_file(
        header["files"],
        ["dsh", "node_modules", "@deepseek-ai", WANT])
    if pkgnode:
        print(f"   {WANT} 的目录: {list(pkgnode.get('files', {}).keys())}")
        libnode = pkgnode.get("files", {}).get("lib", {})
        if libnode:
            print(f"   lib/ 下: {list(libnode.get('files', {}).keys())}")
if node:
    TARGET = (WANT, node)
    print(f"\n③ 找到 {WANT} 的入口文件")

if not TARGET:
    print("   ❌ 没找到入口文件，尝试列目录")
    for pkg in target_pkgs:
        node = find_file(
            header["files"],
            ["dsh", "node_modules", "@deepseek-ai", pkg])
        if node:
            fs = node.get("files", {})
            print(f"   {pkg}: {list(fs.keys())}")
    raise SystemExit(1)

pkg, node = TARGET
content = read_content(node)
print(f"   大小: {len(content)} 字节")

os.makedirs(OUT, exist_ok=True)
outpath = os.path.join(OUT, f"_asar_{pkg.replace('/', '_')}_index.js")
with open(outpath, "wb") as f:
    f.write(content)
print(f"   已保存: {outpath}")

# ---------- ④ 搜索关键符号 ----------
print("\n④ 搜索 agent 结构相关符号")

text = content.decode("utf-8", errors="replace")

SYMBOLS = [
    ("toolCalls", "工具调用计数"),
    ("tool/call", "工具调用事件"),
    ("messages", "消息列表"),
    ("getMessages", "取消息方法"),
    ("this.phase", "phase 状态"),
    ("this.inbox", "inbox"),
    ("step", "步数"),
    ("class Agent", "Agent 类定义"),
]

for sym, desc in SYMBOLS:
    cnt = text.count(sym)
    print(f"   {sym:<18} {cnt:>4} 次  ({desc})")

# ---------- ⑤ 找 Agent 类的方法签名 ----------
print("\n⑤ Agent 类的方法")

# 找 class 定义
for m in re.finditer(r"class\s+(\w*Agent\w*)\s*(?:extends\s+\w+\s*)?\{",
                     text):
    print(f"   找到 class {m.group(1)} at offset {m.start()}")

    # 取类内 5000 字符，找方法名
    seg = text[m.start():m.start() + 8000]
    methods = re.findall(r"^\s{2}(\w+)\s*\(", seg, re.M)
    print(f"      方法: {methods[:20]}")

# ---------- ⑥ 找 tool 计数相关的实现 ----------
print("\n⑥ 搜 tool 计数的真实实现")

for pat, desc in [
    (r"toolCalls?\s*[:=]", "toolCalls 赋值"),
    (r"\.messages\b", ".messages 访问"),
    (r"assistantMessages?", "assistant 消息"),
]:
    hits = list(re.finditer(pat, text))
    print(f"\n   {desc}（{len(hits)} 处）")
    for h in hits[:3]:
        s = max(0, h.start() - 80)
        snippet = text[s:h.start() + 120].replace("\n", " ")
        print(f"     ...{snippet}...")

print("\n" + "=" * 74)
print(f"源码已提取到 {outpath}")
print("=" * 74)
