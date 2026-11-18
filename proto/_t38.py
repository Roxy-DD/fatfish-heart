import sys, os, tempfile, traceback
MEM = os.path.join(os.getcwd(), "cosmos_mem")
sys.path.insert(0, MEM)
import warnings; warnings.filterwarnings("ignore")
import environment as env
from store_v2 import MemoryStore
base = os.path.join(os.getcwd(), "work", "__doctor_tmp")
os.makedirs(base, exist_ok=True)
tmpdir = tempfile.mkdtemp(prefix="ffh_doctor_", dir=base)
print("tmpdir", tmpdir)
tmpdb = os.path.join(tmpdir, "test.db")
try:
    st = MemoryStore(tmpdb)
    ep = st.add("自检测试：肥鱼之心的记忆系统能否正常工作", atmosphere="doctor")
    r = st.search("记忆系统", top=3)
    print("RESULT search hits:", len(r))
    st.con.close()
except Exception:
    traceback.print_exc()
