import sqlite3, os, subprocess, tempfile
p = r"cosmos_mem\dsh_mem.db"
con = sqlite3.connect(p)
print("episodes", con.execute("select count(*) from episodes").fetchone()[0])
print("vectors", con.execute("select count(*) from vectors").fetchone()[0])
print("fts", con.execute("select count(*) from ep_fts").fetchone()[0])
print("missing vectors:")
for r in con.execute("select ep_id, created_at, substr(raw_text,1,40) from episodes where ep_id not in (select ep_id from vectors)"):
    print("  ", r)
print("orphan vectors:")
for r in con.execute("select ep_id from vectors where ep_id not in (select ep_id from episodes) limit 10"):
    print("  ", r)
print("--- audit kinds")
for r in con.execute("select kind, count(*), max(created_at) from audit_log group by kind order by 2 desc"):
    print("  ", r)
con.close()
d = tempfile.mkdtemp(prefix="aclprobe_")
print("mkdtemp:", d)
print(subprocess.run(["icacls", d], capture_output=True, text=True).stdout)
