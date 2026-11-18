import sqlite3, datetime
con = sqlite3.connect(r"cosmos_mem\dsh_mem.db")
for r in con.execute("select ep_id, datetime(created_at,'unixepoch'), length(raw_text), invalid_at, revision from episodes where ep_id not in (select ep_id from vectors)"):
    print(r)
print("--- max created")
print(con.execute("select datetime(max(created_at),'unixepoch') from episodes").fetchone())
print("--- vectors max")
print(con.execute("select datetime(max(created_at),'unixepoch') from vectors").fetchone() if True else "")
con.close()
