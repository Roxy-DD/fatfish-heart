import json, urllib.request
KEY = "itp_tbHAEckPfENHVGJefyICYzCiEvwPXwKoFTKmPXSoUEqLJziJUVxaZdblYLBpGzTC"
body = json.dumps({"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}).encode()
req = urllib.request.Request("http://127.0.0.1:3000/mcp", data=body, headers={"Authorization":f"Bearer {KEY}","Content-Type":"application/json","Accept":"application/json, text/event-stream"}, method="POST")
raw = urllib.request.urlopen(req, timeout=30).read().decode("utf-8","replace")
for line in raw.split("\n"):
    if line.startswith("data: "):
        p = json.loads(line[6:]); break
else:
    p = json.loads(raw)
tools = p.get("result",{}).get("tools",[])
for t in tools:
    print(t["name"], "|", (t.get("description") or "")[:90].replace("\n"," "))
