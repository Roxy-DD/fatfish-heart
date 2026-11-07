"""E-222 端到端验证：kernel_server 常驻进程 + rerank 参数

直接 spawn kernel_server.py，用 JSONL 协议发请求，
验证真实运行时 search(rerank=true) 的行为（最接近插件实际调用路径）。
"""
import json
import subprocess
import sys
import time
import os

_KERNEL = r"C:\Users\xuee\Desktop\肥鱼之心\proto\cosmos_mem\kernel_server.py"
_DB = r"C:\Users\xuee\Desktop\肥鱼之心\proto\cosmos_mem\dsh_mem.db"
_PY = r"D:\anaconda3\python.exe"

def main():
    env = dict(os.environ)
    env["KMP_DUPLICATE_LIB_OK"] = "TRUE"
    env["PYTHONIOENCODING"] = "utf-8"
    env["HF_HOME"] = r"C:\Users\xuee\Desktop\肥鱼之心\.hf"

    print("启动 kernel_server ...")
    proc = subprocess.Popen(
        [_PY, "-u", _KERNEL, _DB],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, env=env, encoding="utf-8",
    )

    # 等预热（BGE 模型加载，~2-5秒）
    print("等待预热 ...")
    time.sleep(6)

    def call(cmd, payload):
        req = {"id": 1, "cmd": cmd, "payload": payload}
        proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline().strip()
        try:
            return json.loads(line)
        except Exception:
            return {"ok": False, "error": f"解析失败: {line[:100]}"}

    print("=" * 70)
    print("E-222 kernel_server 常驻进程 + rerank 验证")
    print("=" * 70)

    # 不带 rerank
    r1 = call("search", {"query": "密码", "top": 5})
    print(f"\n不带 rerank: ok={r1.get('ok')}, results={len(r1.get('results', []))}")

    # 带 rerank
    r2 = call("search", {"query": "密码", "top": 5, "rerank": True})
    print(f"带 rerank:   ok={r2.get('ok')}, results={len(r2.get('results', []))}")
    if r2.get("results"):
        for it in r2["results"][:3]:
            print(f"    [{it['ep_id'][:8]}] {it['text'][:35]} score={it.get('score')}")

    proc.stdin.close()
    proc.terminate()
    print("\n→ 常驻进程 rerank 链路验证完成")


if __name__ == "__main__":
    main()
