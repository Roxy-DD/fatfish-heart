import tempfile, os
_orig = tempfile.mkdtemp
base = os.path.join(os.getcwd(), "work", "__doctor_tmp")
os.makedirs(base, exist_ok=True)
def patched(*a, **kw):
    kw["dir"] = base
    d = _orig(*a, **kw)
    try:
        os.chmod(d, 0o777)
    except Exception:
        pass
    return d
tempfile.mkdtemp = patched
import runpy, sys
sys.argv = ["doctor.py"]
runpy.run_path("doctor.py", run_name="__main__")
