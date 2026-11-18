import tempfile, os, sqlite3
d = tempfile.mkdtemp(prefix="ffh_probe_")
print("tmpdir", d)
p = os.path.join(d, "t.db")
try:
    con = sqlite3.connect(p); con.execute("create table a(x)"); con.commit(); con.close()
    print("OK temp sqlite write")
except Exception as e:
    print("FAIL", type(e).__name__, e)
