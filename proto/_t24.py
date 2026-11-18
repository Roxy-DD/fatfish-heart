import os
os.makedirs("work/__probe_newdir", exist_ok=True)
try:
    open("work/__probe_newdir/a.txt","w").write("x"); print("new subdir write OK")
except Exception as e: print("new subdir FAIL", e)
try:
    open("work/tmp/a.txt","w").write("x"); print("existing work/tmp write OK")
except Exception as e: print("work/tmp FAIL", e)
try:
    open("_rootprobe.txt","w").write("x"); print("root write OK")
except Exception as e: print("root FAIL", e)
