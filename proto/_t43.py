import sqlite3
con = sqlite3.connect(r"cosmos_mem\dsh_mem.db")
print("--- audit near 2026-11-16 04:18")
for r in con.execute("select datetime(at,'unixepoch'), kind, ep_id, substr(msg,1,100) from audit_log where at between 1794802400 and 1794802900 order by at"):
    print("  ", r)
con.close()
