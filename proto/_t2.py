import sys, json
sys.path.insert(0, r"C:\Users\xuee\Desktop\肥鱼之心\proto")
import importlib.util
spec = importlib.util.spec_from_file_location("w", r"C:\Users\xuee\Desktop\肥鱼之心\proto\watch_itsaplan.py")
w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)
for tool,args in [("get_issue",{"issueId":110}),("list_comments",{"issueId":110})]:
    res = w.mcp_call(tool, args, timeout=20)
    print("=== ", tool)
    print(json.dumps(res, ensure_ascii=False, indent=1)[:6000])
