import os, sqlite3, tempfile
d = tempfile.mkdtemp(prefix="ffh_probe_")
print("mkdtemp:", d, "exists", os.path.isdir(d))
try:
    open(os.path.join(d,"a.txt"),"w").write("x"); print("plain write OK")
except Exception as e: print("plain FAIL", type(e).__name__, e)
p = os.path.join(d, "test.db")
try:
    con = sqlite3.connect(p); con.execute("create table a(x)"); con.commit(); con.close(); print("sqlite OK")
except Exception as e: print("sqlite FAIL", type(e).__name__, e)
try:
    con = sqlite3.connect(os.path.join(d, "journal.db"), timeout=30); con.execute("create table a(x)"); con.commit(); con.close(); print("sqlite2 OK")
except Exception as e: print("sqlite2 FAIL", type(e).__name__, e)
print("acls:")
