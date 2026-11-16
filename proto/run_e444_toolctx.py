"""E-444 确认 mem_done 能拿到 agent

mem_done 的 execute(args, execCtx) 依赖 execCtx.agent。
如果 DSH 的工具层不透传 agent，这个工具就拿不到当前会话，
无法调用 supervisor.declareDone(agent)。

本脚本从 asar 提取工具层源码，找 execute 的真实调用方式。
"""
import json
import struct
import re
import os

ASAR = r"C:\Users\xuee\AppData\Local\Programs\DeepSeek Harness\resources\app.asar"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proto", "results")

print("=" * 74)
print("E-444 确认工具 execute 能否拿到 agent")
print("=" * 74)

with open(ASAR, "rb") as f:
    data = f.read()

header_str_size = struct.unpack("<I", data[12:16])[0]
header = json.loads(data[16:16 + header_str_size].decode("utf-8"))
base = (16 + header_str_size + 3) & ~3


def find_node(path_parts):
    node = {"files": header["files"]}
    for p in path_parts:
        fs = node.get("files", {})
        if p not in fs:
            return None
        node = fs[p]
    return node


def read_node(node):
    off = int(node.get("offset", 0))
    size = int(node.get("size", 0))
    return data[base + off: base + off + size]


# 找工具相关包
ds = header["files"].get("dsh", {}).get("files", {})
nm = ds.get("node_modules", {}).get("files", {})
dsai = nm.get("@deepseek-ai", {}).get("files", {})

cands = [k for k in dsai if "tool" in k.lower() and "presentation" not in k]
print(f"\n含 tool 的包（{len(cands)}）: {cands[:15]}")

# 找 dsh-tool 主包
for pkg in ["dsh-tool", "dsh-tools", "dsh-tool-registry"]:
    if pkg in dsai:
        node = find_node(["dsh", "node_modules", "@deepseek-ai",
                          pkg, "lib", "index.js"])
        if not node:
            node = find_node(["dsh", "node_modules", "@deepseek-ai",
                              pkg, "index.js"])
        if node:
            content = read_node(node).decode("utf-8", errors="replace")
            print(f"\n=== {pkg} ({len(content)} 字节) ===")

            # 找 execute 调用
            for m in re.finditer(r"\.execute\s*\(", content):
                s = max(0, m.start() - 200)
                snippet = content[s:m.start() + 400]
                print(f"\n--- execute 调用 ---")
                print(snippet.replace("\n", "\n  ")[:600])

            # 找是否透传 agent
            for kw in ["agent:", "context:", "execCtx", "toolContext"]:
                cnt = content.count(kw)
                if cnt:
                    print(f"\n  '{kw}' 出现 {cnt} 次")
                    for m in re.finditer(re.escape(kw), content):
                        s = max(0, m.start() - 80)
                        print(f"    ...{content[s:m.start()+120]}...".replace("\n", " "))
                        break
            break
else:
    print("\n没找到 dsh-tool 主包，列出所有包名样本：")
    print(list(dsai.keys())[:40])
