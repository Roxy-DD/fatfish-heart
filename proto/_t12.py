import sqlite3
p = r"C:\Users\xuee\Desktop\肥鱼之心\proto\cosmos_mem\dsh_mem.db"
con = sqlite3.connect(p); cur = con.cursor()
tabs = [r[0] for r in cur.execute("select name from sqlite_master where type='table' order by name")]
print("tables:", len(tabs))
tot=0
for t in tabs:
    try:
        n = cur.execute(f"select count(*) from {t}").fetchone()[0]
        tot += n
        print(f"  {t:<28} {n}")
    except Exception as e:
        print("  ", t, "ERR", e)
print("total rows", tot)
print("valid:", cur.execute("select count(*) from episodes where invalid_at is null").fetchone()[0])
print("invalid:", cur.execute("select count(*) from episodes where invalid_at is not null").fetchone()[0])
print("history:", cur.execute("select count(*) from episodes_history").fetchone()[0])
print("audit:", cur.execute("select count(*) from audit_log").fetchone()[0])
print("protection:", cur.execute("select protect_level, count(*) from episodes where invalid_at is null group by protect_level").fetchall())
print("latest episodes:")
for r in cur.execute("select id, substr(raw_text,1,80), created_at, protect_level from episodes order by created_at desc limit 5"):
    print("   ", r)
con.close()
