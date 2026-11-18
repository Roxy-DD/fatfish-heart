import tempfile, os, sqlite3
d = tempfile.mkdtemp(prefix="ffh_probe2_")
print("tmpdir", d)
p = os.path.join(d, "t.db")
try:
    open(os.path.join(d,"x.txt"),"w").write("hello")
    print("plain file write OK")
except Exception as e:
    print("plain FAIL", type(e).__name__, e)
try:
    con = sqlite3.connect(os.path.join(os.getcwd(), "_probe_local.db")); con.execute("create table a(x)"); con.commit(); con.close()
    print("workspace sqlite OK")
    os.remove("_probe_local.db")
except Exception as e:
    print("workspace sqlite FAIL", type(e).__name__, e)
