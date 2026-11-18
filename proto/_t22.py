import os, sqlite3, tempfile
base = tempfile.gettempdir()
for sub in (None, "nested", "nested/deeper"):
    d = os.path.join(base, sub) if sub else base
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "probe.db")
    try:
        con = sqlite3.connect(p); con.execute("create table a(x)"); con.commit(); con.close(); os.remove(p)
        print("OK  ", p)
    except Exception as e:
        print("FAIL", p, type(e).__name__, e)
