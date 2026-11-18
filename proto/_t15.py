import sqlite3
p = r"C:\Users\xuee\Desktop\肥鱼之心\proto\cosmos_mem\dsh_mem.db"
con = sqlite3.connect(p)
for r in con.execute("select id, status, substr(text,1,120), created_at, updated_at from todos order by id desc limit 12"):
    print(r)
print("--- notes")
for r in con.execute("select id, substr(title,1,80), updated_at from notes order by id desc limit 8"):
    print(r)
print("--- env events kinds")
for r in con.execute("select kind, count(*) from environment_events group by kind"):
    print(r)
con.close()
