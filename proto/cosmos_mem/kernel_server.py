"""
kernel_server.py —— 常驻记忆内核（消除每轮进程启动开销）

问题（实测）：
  旧 cli_mcp.py 每轮 spawn 一次，每次都要重新加载 BGE 模型（约 1.5 秒）
  → 每轮固定延迟 1.5-2 秒，这是当前最大性能瓶颈

方案：常驻进程 + JSONL 行协议
  · 一次启动，永久驻留，BGE 模型只加载一次
  · 每行一个请求 {"id":1,"cmd":"search","payload":{...}}
  · 每行一个响应 {"id":1,"ok":true,...}
  · 插件侧保持长连接，按 id 匹配响应

协议设计要点：
  · 用 \n 分隔（JSON 不能含裸换行，json.dumps 默认转义）
  · 每行带 id 用于并发匹配
  · 任何异常都返回 {ok:false}，绝不让进程崩
"""
from __future__ import annotations

import json
import os
import sys
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

_STORE = None
_DB = None


def get_store(db: str):
    """单例：整个进程生命周期只加载一次模型"""
    global _STORE, _DB
    if _STORE is None or _DB != db:
        from store_v2 import MemoryStore
        _STORE = MemoryStore(db, use_semantic=True)
        _DB = db
    return _STORE


def handle(st, cmd: str, payload: dict) -> dict:
    import cli_mcp
    handlers = {
        "add": cli_mcp.cmd_add,
        "search": cli_mcp.cmd_search,
        "revise": cli_mcp.cmd_revise,
        "invalidate": cli_mcp.cmd_invalidate,
        "stats": cli_mcp.cmd_stats,
        "list": cli_mcp.cmd_list,
        "judge": cli_mcp.cmd_judge,
        "scan_conflicts": cli_mcp.cmd_scan_conflicts,
        "expand": cli_mcp.cmd_expand,
        "chain_prepare": cli_mcp.cmd_chain_prepare,
        "chain_apply": cli_mcp.cmd_chain_apply,
        "affect": cli_mcp.cmd_affect,
        "sleep_prepare": cli_mcp.cmd_sleep_prepare,
        "sleep_apply": cli_mcp.cmd_sleep_apply,
        "sleep_history": cli_mcp.cmd_sleep_history,
        "audit": cli_mcp.cmd_audit,
        "hook": cli_mcp.cmd_hook,
        "seed": cli_mcp.cmd_seed,
        "trace": cli_mcp.cmd_trace,
        "inject_log": cli_mcp.cmd_inject_log,
        "inject_history": cli_mcp.cmd_inject_history,
        "note": cli_mcp.cmd_note,
        "todo": cli_mcp.cmd_todo,
        "compose": cli_mcp.cmd_compose,
        "triage": cli_mcp.cmd_triage,
        "knowledge_hint": cli_mcp.cmd_knowledge_hint,
        "history": cli_mcp.cmd_history,
    }
    fn = handlers.get(cmd)
    if not fn:
        return {"ok": False, "error": f"未知命令 {cmd}"}
    return fn(st, payload or {})


def main():
    db = sys.argv[1] if len(sys.argv) > 1 else None
    if not db:
        sys.stderr.write("usage: kernel_server.py <db_path>\n")
        return 2

    # 预热：真正跑一次编码，让 BGE 模型在此刻加载完
    # 注意：只建 store 对象不会加载模型，必须真的 encode 一次
    try:
        st0 = get_store(db)
        try:
            from store_v1 import Embedder
            Embedder.get().encode("预热")
        except Exception as e:
            sys.stderr.write(f"[kernel] embed warmup failed: {e}\n")
        # 再跑一次真实检索，把向量矩阵也建好
        try:
            st0.search("预热", top=1)
        except Exception as e:
            sys.stderr.write(f"[kernel] search warmup failed: {e}\n")
        sys.stderr.write("[kernel] ready\n")
        sys.stderr.flush()
    except Exception as e:
        sys.stderr.write(f"[kernel] warmup failed: {e}\n")
        sys.stderr.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req_id = None
        try:
            req = json.loads(line)
            req_id = req.get("id")
            cmd = req.get("cmd") or ""
            payload = req.get("payload") or {}
            st = get_store(db)
            out = handle(st, cmd, payload)
            out["id"] = req_id
        except Exception as e:
            out = {"id": req_id, "ok": False,
                   "error": f"{type(e).__name__}: {e}",
                   "trace": traceback.format_exc()[-500:]}

        try:
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception:
            break

    return 0


if __name__ == "__main__":
    sys.exit(main())

