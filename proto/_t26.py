import sqlite3, os, subprocess, tempfile
p = r"cosmos_mem\dsh_mem.db"
con = sqlite3.connect(p)
print([r[1] for r in con.execute("PRAGMA table_info(audit_log)")])
for r in con.execute("select event, count(*) from audit_log group by event order by 2 desc limit 15"):
    print("  ", r)
con.close()
d = tempfile.mkdtemp(prefix="aclprobe_")
print("mkdtemp:", d)
print(subprocess.run(["icacls", d], capture_output=True, text=True).stdout)
