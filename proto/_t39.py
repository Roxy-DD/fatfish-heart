import os, sqlite3, stat
base = os.path.join(os.getcwd(), "work", "__acl_probe")
os.makedirs(base, exist_ok=True)
for label, mode in (("default", None), ("0700", 0o700)):
    d = os.path.join(base, label)
    try:
        if mode is None:
            os.makedirs(d, exist_ok=True)
        else:
            if not os.path.isdir(d):
                os.mkdir(d, mode)
        p = os.path.join(d, "p.db")
        con = sqlite3.connect(p); con.execute("create table a(x)"); con.commit(); con.close()
        print(label, "OK", oct(stat.S_IMODE(os.stat(d).st_mode)))
    except Exception as e:
        print(label, "FAIL", type(e).__name__, e)
