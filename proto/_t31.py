import re
src = open("iterate.py", encoding="utf-8").read()
m = re.search(r"CLIENT_CHECKS\s*=\s*\[(.*?)\n\]", src, re.S)
body = m.group(1)
names = re.findall(r'\(\s*"([^"]+)"\s*,\s*"([^"]+)"', body)
print("count", len(names))
for n in names[-12:]:
    print(n)
PY_CHECKS = re.search(r"PY_CHECKS\s*=\s*\[(.*?)\n\]", src, re.S)
print("py checks:", len(re.findall(r'\(\s*"', PY_CHECKS.group(1))) if PY_CHECKS else "none")
