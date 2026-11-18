import sqlite3, os, subprocess, tempfile
p = r"cosmos_mem\dsh_mem.db"
con = sqlite3.connect(p)
for r in con.execute("select kind, count(*), datetime(max(at),'unixepoch') from audit_log group by kind order by 2 desc"):
    print("  ", r)
con.close()
d = tempfile.mkdtemp(prefix="aclprobe_")
print("mkdtemp:", d)
print(subprocess.run(["icacls", d], capture_output=True, text=True).stdout)
