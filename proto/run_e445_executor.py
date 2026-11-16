"""E-445 查 executor 如何把 agent 传给单个工具的 execute

E-444 确认 dsh-tools.execute(input) 里 input 含 agent。
但还要确认：executor 具体怎么调用**插件注册的** tool.execute。

关键问题：
  插件注册时写的是 execute(args, execCtx)。
  execCtx 里到底有什么？有没有 agent？

从 asar 提取 dsh-tool-executor-native（原生执行器）。
"""
import json
import struct
import re

ASAR = r"C:\Users\xuee\AppData\Local\Programs\DeepSeek Harness\resources\app.asar"

with open(ASAR, "rb") as f:
    data = f.read()
header_str_size = struct.unpack("<I", data[12:16])[0]
header = json.loads(data[16:16 + header_str_size].decode("utf-8"))
base = (16 + header_str_size + 3) & ~3


def find_node(parts):
    node = {"files": header["files"]}
    for p in parts:
        fs = node.get("files", {})
        if p not in fs:
            return None
        node = fs[p]
    return node


def read_node(node):
    off = int(node.get("offset", 0))
    size = int(node.get("size", 0))
    return data[base + off: base + off + size]


print("=" * 74)
print("E-445 executor 如何调用工具 execute")
print("=" * 74)

for pkg in ["dsh-tool-executor-native", "dsh-tools"]:
    for rel in (["lib", "index.js"], ["index.js"]):
        node = find_node(["dsh", "node_modules", "@deepseek-ai",
                          pkg] + rel)
        if not node:
            continue
        content = read_node(node).decode("utf-8", errors="replace")
        print(f"\n{'='*60}")
        print(f"{pkg}/{rel[-1]}  ({len(content)} 字节)")
        print(f"{'='*60}")

        # 找 tool.execute 或 .execute( 的调用，看参数
        pats = [
            r"\.execute\(\s*\{",
            r"\.execute\([^)]{0,200}\)",
            r"handler\(",
            r"invoke\(",
        ]
        for pat in pats:
            for m in re.finditer(pat, content):
                s = max(0, m.start() - 150)
                seg = content[s:m.start() + 300]
                if "agent" in seg or "input" in seg or "args" in seg:
                    print(f"\n--- 匹配 {pat} ---")
                    print(seg.replace("\n", "\n  ")[:500])
                    break
        break
    else:
        continue
    break

# 直接搜 agent 透传的写法
print(f"\n{'='*60}")
print("搜 agent 透传写法")
print(f"{'='*60}")

node = find_node(["dsh", "node_modules", "@deepseek-ai",
                  "dsh-tools", "lib", "index.js"])
if not node:
    node = find_node(["dsh", "node_modules", "@deepseek-ai",
                      "dsh-tools", "index.js"])
if node:
    content = read_node(node).decode("utf-8", errors="replace")
    for kw in ["agent", "execContext", "toolContext", "context"]:
        hits = list(re.finditer(rf"\b{kw}\b", content))
        print(f"\n'{kw}': {len(hits)} 次")
        for h in hits[:3]:
            s = max(0, h.start() - 100)
            print(f"  ...{content[s:h.start()+150]}...".replace("\n", " "))
