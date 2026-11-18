import sqlite3, os
p = r"C:\Users\xuee\Desktop\肥鱼之心\proto\dsh_mem.db"
con = sqlite3.connect(p); cur = con.cursor()
tabs = [r[0] for r in cur.execute("select name from sqlite_master where type='table' order by name")]
print("tables:", len(tabs))
for t in tabs:
    try:
        n = cur.execute(f"select count(*) from {t}").fetchone()[0]
        print(f"  {t:<28} {n}")
    except Exception as e:
        print("  ", t, "ERR", e)
print()
try:
    print("valid:", cur.execute("select count(*) from episodes where invalid_at is null").fetchone()[0])
    print("invalid:", cur.execute("select count(*) from episodes where invalid_at is not null").fetchone()[0])
except Exception as e:
    print("ep split err", e)
con.close()
