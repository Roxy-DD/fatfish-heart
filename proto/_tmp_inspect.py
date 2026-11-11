import json,sqlite3,time
m=json.load(open(r"proto\work\versions.json",encoding="utf-8"))
print("current",m["current"],"n",len(m["versions"]))
for v in m["versions"]:
    print(" ",v["id"],v.get("status"),time.strftime("%m-%d %H:%M",time.localtime(v.get("at",0))),v.get("commit"),list((v.get("artifacts") or {}).keys()),repr(v.get("reason"))[:40])
db=sqlite3.connect(r"proto\cosmos_mem\dsh_mem.db")
cur=db.cursor()
qs=["select count(*) from episodes",
    "select count(*) from episodes where invalid_at is null",
    "select count(*) from environment_events",
    "select COALESCE(MAX(at),0) from environment_events",
    "select count(*) from audit_log",
    "select name from sqlite_master where type='table' order by name"]
for q in qs:
    try:
        print(q,"->",cur.execute(q).fetchall())
    except Exception as e:
        print(q,"ERR",e)
