import tempfile, os
print("gettempdir:", tempfile.gettempdir())
import sqlite3
p = os.path.join(tempfile.gettempdir(), "x.db")
try:
    con=sqlite3.connect(p); con.execute("create table a(x)"); con.commit(); con.close(); print("OK")
except Exception as e: print("FAIL", e)
print("env TEMP", os.environ.get("TEMP"))
