import sqlite3
con = sqlite3.connect(r"cosmos_mem\dsh_mem.db")
print("history rows per ep for the 4:")
for eid in ('ab54d7105aa04243','4d2821e436384ebf','97470820ca9c40ad','99a6a22053454b6f'):
    n = con.execute("select count(*) from episodes_history where ep_id=?", (eid,)).fetchone()[0]
    row = con.execute("select datetime(invalid_at,'unixepoch'), superseded_by, revision, text_hash from episodes where ep_id=?", (eid,)).fetchone()
    print(" ", eid, "history", n, row)
print("--- any ep with revision>1 but no vectors?")
print(con.execute("select count(*) from episodes where revision>1 and ep_id not in (select ep_id from vectors)").fetchone())
con.close()
