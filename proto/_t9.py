import sys, json
import importlib.util
spec = importlib.util.spec_from_file_location("w", r"C:\Users\xuee\Desktop\肥鱼之心\proto\watch_itsaplan.py")
w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)
res = w.mcp_call("list_issues", {"projectKey":"FISH", "limit":"100"}, timeout=30)
for it in res:
    print(it["identifier"], "id=", it.get("id"), it.get("issueId"), list(it.keys())[:8])
