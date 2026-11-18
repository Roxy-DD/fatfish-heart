import sqlite3
p = r"C:\Users\xuee\Desktop\肥鱼之心\proto\cosmos_mem\dsh_mem.db"
con = sqlite3.connect(p); cur = con.cursor()
cols = [r[1] for r in cur.execute("PRAGMA table_info(episodes)")]
print(cols)
for r in cur.execute("select level, count(*) from episodes where invalid_at is null group by level"):
    print(r)
print("--- recent")
for r in cur.execute("select id, level, substr(raw_text,1,60), created_at from episodes order by created_at desc limit 4"):
    print(r)
con.close()
