import json,urllib.request
MCP="http://127.0.0.1:3000/mcp"
KEY="itp_tbHAEckPfENHVGJefyICYzCiEvwPXwKoFTKmPXSoUEqLJziJUVxaZdblYLBpGzTC"
body=json.dumps({"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}).encode()
req=urllib.request.Request(MCP,data=body,method="POST",headers={"Authorization":f"Bearer {KEY}","Content-Type":"application/json","Accept":"application/json, text/event-stream"})
raw=urllib.request.urlopen(req,timeout=30).read().decode("utf-8","replace")
payload=None
for line in raw.split("\n"):
    if line.startswith("data: "):
        payload=json.loads(line[6:]);break
if payload is None: payload=json.loads(raw)
tools=payload["result"]["tools"]
for t in tools:
    req_obj=t.get("inputSchema",{})
    props=list((req_obj.get("properties") or {}).keys())
    reqd=req_obj.get("required") or []
    print(f'{t["name"]}: req={reqd} props={props}')
