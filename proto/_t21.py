import sys, os, tempfile, traceback
MEM = os.path.join(os.getcwd(), "cosmos_mem")
sys.path.insert(0, MEM)
import warnings; warnings.filterwarnings("ignore")
import environment as env
from store_v2 import MemoryStore
tmpdir = tempfile.mkdtemp(prefix="ffh_doctor_")
print("tmpdir", tmpdir)
tmpdb = os.path.join(tmpdir, "test.db")
try:
    st = MemoryStore(tmpdb)
    ep = st.add("自检测试：肥鱼之心的记忆系统能否正常工作", atmosphere="doctor")
    print("added ok")
    r = st.search("记忆系统", top=3)
    print("search", len(r))
except Exception:
    traceback.print_exc()
