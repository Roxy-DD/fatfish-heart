import re
src = open("dsh-plugin/client.js", encoding="utf-8").read()
tabs = re.findall(r"\{[\s\S]{0,80}?(?:id|key):\s*['\"](list|timeline|inject|sleep|audit|config|note|protect|stats|todo|memory)[\"']", src)
print("tab ids found:", sorted(set(tabs)))
print("len", len(set(tabs)))
