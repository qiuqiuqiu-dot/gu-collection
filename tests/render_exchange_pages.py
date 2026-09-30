"""渲染同城换谷页面给 jsdom 用（临时库，两个账号）。"""
import os
import sys

sys.path.insert(0, ".")

HERE = os.path.dirname(os.path.abspath(__file__))

from app import create_app  # noqa: E402
from models import db  # noqa: E402

# 测试库统一写到 .tmp/testdb/，别把真实数据目录 instance/ 塞满 verify*.db
TEST_DB_DIR = os.path.join(HERE, ".build", "testdb")
os.makedirs(TEST_DB_DIR, exist_ok=True)
BUILD_DIR = os.path.join(HERE, ".build")
os.makedirs(BUILD_DIR, exist_ok=True)


def db_uri(name):
    """拼一个指向 .tmp/testdb/ 的 sqlite URI（Windows 路径要转成 /）。"""
    return "sqlite:///" + os.path.join(TEST_DB_DIR, name).replace("\\", "/")


app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False,
                  "EXCHANGE_ENABLED": True,   # 换谷默认关掉了，这页给换谷板 DOM 测试用
                  "SQLALCHEMY_DATABASE_URI": db_uri("verify_dom_m.db")})
with app.app_context():
    db.drop_all()
    db.create_all()
c = app.test_client()
c.post("/register", data={"username": "domex", "password": "abc123"})
mate = app.test_client()
mate.post("/register", data={"username": "dommate", "password": "abc123"})

SHANGHAI_XUHUI = {"country": "中国", "province": "上海市",
                  "city": "市辖区", "area": "徐汇区"}
BEIJING_CHAOYANG = {"country": "中国", "province": "北京市",
                    "city": "市辖区", "area": "朝阳区"}

c.post("/profile/region", data=SHANGHAI_XUHUI)
mate.post("/profile/region", data=SHANGHAI_XUHUI)

c.post("/exchanges/add", data=dict(
    title="求换 星野吧唧", want="白兔立牌", emoji="🐰", distance_km="1.5",
    place="地铁 2 号线站内", contact="微信 me_1", note="只换原画"))
mate.post("/exchanges/add", data=dict(
    title="求换 抹茶吧唧", want="樱花立牌", emoji="🍵", distance_km="0.5",
    place="小区门口", contact="微信 mate_1"))
mate.post("/profile/region", data=BEIJING_CHAOYANG)
mate.post("/exchanges/add", data=dict(title="求换 星野立牌", distance_km="9"))
c.get("/")


def save(path, url, client=None):
    html = (client or c).get(url).get_data(as_text=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"{os.path.basename(path)} <- {url}: 换谷行={html.count('class=\"side-item')}, "
          f"卡片={html.count('class=\"ex-card')}, 编辑按钮={html.count('data-exchange-edit=')}")


save(os.path.join(BUILD_DIR, "page_a.html"), "/exchanges")
save(os.path.join(BUILD_DIR, "page_b.html"), "/")
