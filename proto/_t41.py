import tempfile, os, sqlite3, stat
base = os.path.join(os.getcwd(), "work", "__doctor_tmp2")
os.makedirs(base, exist_ok=True)
d = tempfile.mkdtemp(prefix="x_", dir=base)
print("created", d, oct(stat.S_IMODE(os.stat(d).st_mode)))
try:
    os.chmod(d, 0o777); print("chmod ok ->", oct(stat.S_IMODE(os.stat(d).st_mode)))
except Exception as e:
    print("chmod FAIL", e)
try:
    con = sqlite3.connect(os.path.join(d, "t.db")); con.execute("create table a(x)"); con.commit(); con.close(); print("sqlite OK")
except Exception as e:
    print("sqlite FAIL", e)
