"""itsaplan_call.py —— 命令行调 itaplan MCP

给 headless agent 用的：它没有 MCP 插件，但有 pwsh 工具，
可以执行 `python itsaplan_call.py <工具> <JSON参数>`。

用法：
    python itsaplan_call.py get_current_user
    python itsaplan_call.py get_issue '{"issueId": 110}'
    python itsaplan_call.py list_issue_activity '{"issueId": 110, "limit": 5}'
    python itsaplan_call.py add_comment '{"issueId": 110, "body": "收到"}'

输出：美化后的 JSON（UTF-8）
"""
import json
import os
import sys
import urllib.error
import urllib.request

MCP_URL = "http://127.0.0.1:3000/mcp"
API_KEY = os.environ.get("ITSAPLAN_KEY") or (
    "itp_tbHAEckPfENHVGJefyICYzCiEvwPXwKoFTKmPXSoUEqLJziJUVxaZdblYLBpGzTC"
)


def mcp(tool, args, timeout=60):
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": tool, "arguments": args},
    }).encode("utf-8")

    req = urllib.request.Request(
        MCP_URL, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")

    payload = None
    for line in raw.split("\n"):
        if line.startswith("data: "):
            try:
                payload = json.loads(line[6:])
            except Exception:
                continue
            break
    if payload is None:
        payload = json.loads(raw)

    if "error" in payload:
        raise RuntimeError(str(payload["error"])[:300])

    content = payload["result"]["content"]
    if isinstance(content, list) and content:
        txt = content[0].get("text", "")
        try:
            return json.loads(txt)
        except Exception:
            return {"text": txt}
    return payload.get("result", {})


def main():
    if len(sys.argv) < 2:
        print("用法: python itsaplan_call.py <工具名> [JSON参数]")
        print("例:   python itsaplan_call.py get_issue '{\"issueId\": 110}'")
        return 1

    tool = sys.argv[1]
    args = {}
    if len(sys.argv) > 2:
        try:
            args = json.loads(sys.argv[2])
        except Exception as e:
            print(f"参数不是合法 JSON: {e}", file=sys.stderr)
            return 1

    try:
        result = mcp(tool, args)
    except Exception as e:
        print(f"调用失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
