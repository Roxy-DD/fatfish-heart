import importlib.util, json
spec = importlib.util.spec_from_file_location("w", r"C:\Users\xuee\Desktop\肥鱼之心\proto\watch_itsaplan.py")
w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)
r = w.mcp_call("get_project", {"key":"FISH"}, timeout=25)
# find FISH project
for p in r:
    if p.get("key")=="FISH":
        print(json.dumps({k:p[k] for k in ("id","key","name","description")}, ensure_ascii=False, indent=1)[:1500])
print("--- columns")
c = w.mcp_call("list_projects", {}, timeout=25)
print(json.dumps(c, ensure_ascii=False)[:300])
