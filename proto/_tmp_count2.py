import re
src = open(r"proto\iterate.py", encoding="utf-8").read()
def cnt(name):
    m = re.search(name + r" = \[(.*?)\n\]", src, re.S)
    return re.findall(r'^\s*\("([^"]+)"\s*,\s*"([^"]+)"', m.group(1), re.M)
a = cnt("REGRESSION_SUITE"); b = cnt("CLIENT_CHECKS")
print("REGRESSION_SUITE:", len(a))
print("CLIENT_CHECKS:", len(b))
print("total:", len(a)+len(b))
