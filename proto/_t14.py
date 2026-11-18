import importlib.util, json
spec = importlib.util.spec_from_file_location("w", r"C:\Users\xuee\Desktop\肥鱼之心\proto\watch_itsaplan.py")
w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)
res = w.mcp_call("get_issue", {"issueId":110}, timeout=25)
print("--- project fields")
pr = w.mcp_call("get_project", {"key":"FISH"}, timeout=25)
print(json.dumps(pr, ensure_ascii=False)[:1200])
