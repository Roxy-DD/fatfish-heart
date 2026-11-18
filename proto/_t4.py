import sys, json
import importlib.util
spec = importlib.util.spec_from_file_location("w", r"C:\Users\xuee\Desktop\肥鱼之心\proto\watch_itsaplan.py")
w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)
res = w.mcp_call("list_issue_activity", {"issueId":110}, timeout=25)
print(json.dumps(res, ensure_ascii=False, indent=1)[:8000])
