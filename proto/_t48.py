import importlib.util, json
spec = importlib.util.spec_from_file_location("w", r"C:\Users\xuee\Desktop\肥鱼之心\proto\watch_itsaplan.py")
w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)
for tool,args in [("list_agent_schedules",{"projectId":1}),("list_agent_runs",{"projectId":1})]:
    r = w.mcp_call(tool, args, timeout=25)
    print("==", tool)
    print(json.dumps(r, ensure_ascii=False)[:1500])
