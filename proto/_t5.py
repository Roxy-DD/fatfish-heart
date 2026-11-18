import sys, json
import importlib.util
spec = importlib.util.spec_from_file_location("w", r"C:\Users\xuee\Desktop\肥鱼之心\proto\watch_itsaplan.py")
w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)
res = w.mcp_call("list_issues", {"projectId":1, "limit":"100"}, timeout=30)
items = res.get("items") or res.get("issues") or []
print("count", len(items))
for it in items:
    print(it.get("identifier"), "|", it.get("column") if isinstance(it.get("column"),str) else it.get("columnId"), "|", it.get("stateType",""), "|", it.get("title"))
