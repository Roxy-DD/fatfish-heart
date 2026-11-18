import re
src = open("iterate.py", encoding="utf-8").read()
m = re.search(r"CLIENT_CHECKS\s*=\s*\[(.*?)\]", src, re.S)
print(m.group(1)[:3000] if m else "no CLIENT_CHECKS")
