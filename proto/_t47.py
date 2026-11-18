import sqlite3, datetime
con = sqlite3.connect(r"cosmos_mem\dsh_mem.db")
print("--- inject events (local time)")
for r in con.execute("select datetime(at,'unixepoch','localtime'), sid, substr(msg,1,80) from audit_log where kind='inject' order by at desc limit 12"):
    print("  ", r)
print("--- recall today")
for r in con.execute("select datetime(at,'unixepoch','localtime'), substr(msg,1,60) from audit_log where kind='recall' and at > strftime('%s','now','-1 day') order by at desc limit 8"):
    print("  ", r)
con.close()
