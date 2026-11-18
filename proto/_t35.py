import importlib.util, json
spec = importlib.util.spec_from_file_location("w", r"C:\Users\xuee\Desktop\肥鱼之心\proto\watch_itsaplan.py")
w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)
d = w.mcp_call("list_documents", {"projectId":1}, timeout=25)
print(json.dumps(d, ensure_ascii=False)[:2500])
