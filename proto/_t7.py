import sys, json
import importlib.util
spec = importlib.util.spec_from_file_location("w", r"C:\Users\xuee\Desktop\肥鱼之心\proto\watch_itsaplan.py")
w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)
res = w.mcp_call("list_issues", {"projectKey":"FISH", "limit":"100"}, timeout=30)
print("n=", len(res))
COL={1:"Backlog",2:"Todo",3:"InProgress",4:"Done",5:"Canceled"}
for it in res:
    print(f'{it["identifier"]:<8} {COL.get(it["columnId"],it["columnId"]):<11} {str(it.get("priority")):<7} {it["title"]}')
