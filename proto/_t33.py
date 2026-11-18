import sqlite3
con = sqlite3.connect(r"cosmos_mem\dsh_mem.db")
print("--- 4 missing-vector episodes detail")
for r in con.execute("select ep_id, revision, superseded_by, invalid_at is not null from episodes where ep_id not in (select ep_id from vectors)"):
    print("  ", r)
print("--- invalid episodes WITH vectors:", con.execute("select count(*) from episodes e where e.invalid_at is not null and exists(select 1 from vectors v where v.ep_id=e.ep_id)").fetchone()[0])
print("--- invalid total:", con.execute("select count(*) from episodes where invalid_at is not null").fetchone()[0])
print("--- valid total:", con.execute("select count(*) from episodes where invalid_at is null").fetchone()[0])
print("--- valid with vectors:", con.execute("select count(*) from episodes e where e.invalid_at is null and exists(select 1 from vectors v where v.ep_id=e.ep_id)").fetchone()[0])
con.close()
