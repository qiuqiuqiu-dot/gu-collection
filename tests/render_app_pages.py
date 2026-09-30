"""渲染「统一 + 号」相关页面给 jsdom 用（临时库，两个账号）。"""
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
                  # 换谷默认为关；这里打开是为了继续完整测「+ 面板 → 换谷表单」那条线
                  "EXCHANGE_ENABLED": True,
                  "SQLALCHEMY_DATABASE_URI": db_uri("verify_dom_sheet.db")})
with app.app_context():
    db.drop_all()
    db.create_all()
c = app.test_client()
c.post("/register", data={"username": "sheetdom", "password": "abc123"})

# 分类：给数据账号建几个，用来验「分类管理」按钮上的数量角标（2 品类 + 3 IP）
for cat_name in ("吧唧", "色纸"):
    c.post("/categories/add", data={"kind": "type", "name": cat_name})
for cat_name in ("某某", "台风眼", "藏南晚星"):
    c.post("/categories/add", data={"kind": "ip", "name": cat_name})

# 一个已有数据的账号：谷柜有货、在途有货、心愿单有货
c.post("/items/add", data={"name": "展示的吧唧", "emoji": "🐰", "count": "2",
                           "status": "displaying"})
c.post("/items/add", data={"name": "在路上的色纸", "emoji": "🚚", "count": "1",
                           "status": "in_transit", "expect_date": "2026-12-01",
                           "channel": "taobao", "order_no": "SF123"})
c.post("/items/add", data={"name": "想要的立牌", "emoji": "⭐", "count": "1",
                           "status": "wishlist", "want_level": "high",
                           "budget": "300"})
c.post("/reminders/add", data={"title": "星野再贩", "event_time": "2026-12-31T20:00",
                               "priority": "high"})
c.post("/exchanges/add", data={"title": "求换 星野吧唧", "want": "白兔立牌",
                               "distance_km": "1.5"})
# 空账号：用来看空状态里的一键添加
empty = app.test_client()
empty.post("/register", data={"username": "sheetempty", "password": "abc123"})


def save(path, url, client=None):
    html = (client or c).get(url).get_data(as_text=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"{os.path.basename(path)} <- {url}: fab={html.count('class=\"fab\"')}, "
          f"addSheet={'id=\"addSheet\"' in html}, "
          f"addModal={'id=\"addModal\"' in html}")


save(os.path.join(BUILD_DIR, "s_dash.html"), "/")
save(os.path.join(BUILD_DIR, "s_items.html"), "/items")
save(os.path.join(BUILD_DIR, "s_transit.html"), "/transit")
save(os.path.join(BUILD_DIR, "s_transit_empty.html"), "/transit", empty)
save(os.path.join(BUILD_DIR, "s_account.html"), "/account")
save(os.path.join(BUILD_DIR, "s_account_data.html"), "/account-data")
save(os.path.join(BUILD_DIR, "s_account_password.html"), "/account/password")
save(os.path.join(BUILD_DIR, "s_account_sessions.html"), "/account/sessions")
save(os.path.join(BUILD_DIR, "s_profile.html"), "/profile")
save(os.path.join(BUILD_DIR, "s_profile_avatar.html"), "/profile/avatar")
save(os.path.join(BUILD_DIR, "s_profile_username.html"), "/profile/username")
# 地区页要连「已经选到区/县」的样子一起渲染出来（s_profile_region_saved）：
# 页面上的四级联动 JS 会在加载后重填下拉，很容易把服务端预选的区/县冲掉，
# 只有渲染一个「本来就有值」的页面才测得到。
c.post("/profile/region", data={"country": "中国", "province": "广东省",
                                "city": "深圳市", "area": "南山区",
                                "next": "/profile"})
save(os.path.join(BUILD_DIR, "s_profile_region_saved.html"), "/profile/region")
c.post("/profile/region", data={"country": "", "next": "/profile"})   # 清回去
save(os.path.join(BUILD_DIR, "s_profile_region.html"), "/profile/region")
save(os.path.join(BUILD_DIR, "s_profile_signature.html"), "/profile/signature")
save(os.path.join(BUILD_DIR, "s_backup.html"), "/backup")
save(os.path.join(BUILD_DIR, "s_wishlist_empty.html"), "/wishlist", empty)
save(os.path.join(BUILD_DIR, "s_sold_empty.html"), "/sold", empty)
save(os.path.join(BUILD_DIR, "s_reminders_empty.html"), "/reminders", empty)

# 换谷板是谷友互看的：要看到「还没有换谷信息」的空状态，
# 得整个库里一条都没有（别的账号发了也会显示给我看），所以另开一个空库。
app2 = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False,
                   "EXCHANGE_ENABLED": True,
                   "SQLALCHEMY_DATABASE_URI": db_uri("verify_dom_sheet2.db")})
with app2.app_context():
    db.drop_all()
    db.create_all()
nobody = app2.test_client()
nobody.post("/register", data={"username": "sheetonely", "password": "abc123"})
save(os.path.join(BUILD_DIR, "s_exchanges_empty.html"), "/exchanges", nobody)

# 换谷默认是关的：另外渲染一份「出厂状态」的页面，专门验关掉之后看不到任何入口
app3 = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False,
                   "SQLALCHEMY_DATABASE_URI": db_uri("verify_dom_sheet3.db")})
with app3.app_context():
    db.drop_all()
    db.create_all()
off = app3.test_client()
off.post("/register", data={"username": "sheetoff", "password": "abc123"})
save(os.path.join(BUILD_DIR, "s_dash_off.html"), "/", off)
print("换谷关闭时的 /exchanges 状态码:",
      off.get("/exchanges").status_code)
