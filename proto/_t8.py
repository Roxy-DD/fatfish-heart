import sys, json
import importlib.util
spec = importlib.util.spec_from_file_location("w", r"C:\Users\xuee\Desktop\肥鱼之心\proto\watch_itsaplan.py")
w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)
for iid in (15, 8):
    iso = w.mcp_call("get_issue", {"issueId":iid}, timeout=25)
    print("="*70)
    print(iso.get("identifier"), iso.get("title"), "| col", iso.get("columnId"))
    print("DESC:", (iso.get("description") or "")[:1500])
    act = w.mcp_call("list_issue_activity", {"issueId":iid}, timeout=25)
    for a in (act.get("items") or [])[:6]:
        print(" -", a.get("createdAt"), a.get("actorName"), a.get("kind"), (a.get("body") or "")[:400])
