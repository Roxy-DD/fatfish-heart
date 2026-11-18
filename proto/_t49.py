import re
src = open("iterate.py", encoding="utf-8").read()
m = re.search(r"REGRESSION_SUITE\s*=\s*\[(.*?)\n\]", src, re.S)
body = m.group(1) if m else ""
n = len(re.findall(r'\(\s*"', body))
print("REGRESSION_SUITE entries:", n)
print(body[:400])
