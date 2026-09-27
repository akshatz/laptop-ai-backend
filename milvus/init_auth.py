import os
import sys

from pymilvus import connections, utility
from pymilvus.exceptions import MilvusException

root_password = os.environ["MILVUS_ROOT_PASSWORD"]

try:
    connections.connect(host="milvus", port="19530", user="root", password=root_password)
    print("Root password already rotated, nothing to do.")
    sys.exit(0)
except MilvusException:
    pass

connections.connect(host="milvus", port="19530", user="root", password="Milvus")
utility.reset_password("root", "Milvus", root_password)
print("Rotated Milvus root password.")
