import sqlite3
p = r"C:\Users\xuee\Desktop\肥鱼之心\proto\cosmos_mem\dsh_mem.db"
con = sqlite3.connect(p)
print("--- todos")
for r in con.execute("select todo_id, status, priority, substr(title,1,80), created_at, updated_at from todos order by updated_at desc limit 15"):
    print(r)
print("--- notes")
for r in con.execute("select note_id, level, substr(title,1,70), updated_at from notes order by updated_at desc limit 12"):
    print(r)
con.close()
