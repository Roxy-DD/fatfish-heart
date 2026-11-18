import importlib.util
spec = importlib.util.spec_from_file_location("w", r"C:\Users\xuee\Desktop\肥鱼之心\proto\watch_itsaplan.py")
w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)
for iid in (68, 70, 69, 109):
    iso = w.mcp_call("get_issue", {"issueId":iid}, timeout=25)
    print("="*70)
    print(iso.get("identifier"), iso.get("title"), "| col", iso.get("columnId"))
    print((iso.get("description") or "")[:900])
    act = w.mcp_call("list_issue_activity", {"issueId":iid}, timeout=25)
    for a in (act.get("items") or [])[:3]:
        if a.get("kind")=="comment":
            print(" >>", a.get("createdAt"), (a.get("body") or "")[:700])
