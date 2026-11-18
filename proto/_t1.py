import sys, json
sys.path.insert(0, r"C:\Users\xuee\Desktop\肥鱼之心\proto")
import importlib.util
spec = importlib.util.spec_from_file_location("w", r"C:\Users\xuee\Desktop\肥鱼之心\proto\watch_itsaplan.py")
w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)
res = w.mcp_call("list_notifications", {"includeRead":"false","limit":"30"}, timeout=20)
print(json.dumps(res, ensure_ascii=False)[:2000])
