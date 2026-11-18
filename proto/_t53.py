import sys, os, runpy
sys.path.insert(0, os.getcwd())
import _tmpfix  # noqa
for script in ("run_e493_auto_backup.py", "run_e495_write_trigger.py", "run_e473_branch_state.py", "run_e440_version_reg.py"):
    print("="*30, script)
    try:
        runpy.run_path(script, run_name="__main__")
    except SystemExit as e:
        print("exit", e.code)
    except Exception as e:
        print("EXC", type(e).__name__, e)
