import os, sqlite3, stat, shutil
base = os.path.join(os.getcwd(), "work", "__mode_probe")
shutil.rmtree(base, ignore_errors=True); os.makedirs(base, exist_ok=True)
def trial(label, maker):
    d = os.path.join(base, label)
    try:
        maker(d)
        ok_write = True
        try:
            open(os.path.join(d, "a.txt"), "w").write("x")
        except Exception as e:
            ok_write = f"{type(e).__name__}"
        print(f"{label:<12} mkdir=OK  write={ok_write}  mode={oct(stat.S_IMODE(os.stat(d).st_mode))}")
    except Exception as e:
        print(f"{label:<12} mkdir=FAIL {type(e).__name__}: {e}")
trial("mkdir_default", lambda d: os.mkdir(d))
trial("mkdir_700", lambda d: os.mkdir(d, 0o700))
trial("mkdir_777", lambda d: os.mkdir(d, 0o777))
trial("makedirs_def", lambda d: os.makedirs(d))
trial("makedirs_700", lambda d: os.makedirs(d, mode=0o700))
