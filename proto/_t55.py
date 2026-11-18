import sqlite3
con = sqlite3.connect(r"cosmos_mem\dsh_mem.db")
print("--- recent audit all kinds today")
for r in con.execute("select datetime(at,'unixepoch','localtime'), kind, sid, substr(msg,1,70) from audit_log where at > strftime('%s','now','-6 hours') order by at desc limit 20"):
    print("  ", r)
con.close()
