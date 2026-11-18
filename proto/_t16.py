import sqlite3
p = r"C:\Users\xuee\Desktop\肥鱼之心\proto\cosmos_mem\dsh_mem.db"
con = sqlite3.connect(p)
for t in ("todos","notes"):
    print(t, [r[1] for r in con.execute(f"PRAGMA table_info({t})")])
con.close()
