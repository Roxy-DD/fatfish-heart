import re
src = open(r"cosmos_mem\store_v1.py", encoding="utf-8").read()
for m in re.finditer(r".*DELETE.*", src):
    print(m.group(0).strip())
