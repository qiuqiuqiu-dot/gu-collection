"""冒烟测试：端到端验证认证、输入校验、统计一致性、越权、CSRF、图片上传与数据备份。

用法：.venv\\Scripts\\python.exe smoke_test.py
说明：使用独立的 .tmp/testdb/*.db，不会动 instance/gu.db（只会读它验证自动补列）。
"""
import io
import json
import os
import re
import sys
import time
import zipfile
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from PIL import Image as PILImage  # noqa: E402
from sqlalchemy import inspect as sa_inspect  # noqa: E402

import backup  # noqa: E402
import regions  # noqa: E402
import routes  # noqa: E402
from app import create_app, login_manager  # noqa: E402
from models import (Category, Exchange, GuItem, Reminder, User, db,  # noqa: E402
                    local_today)
from routes import PANEL_PREVIEW, SOLD_PREVIEW, TRANSIT_PREVIEW  # noqa: E402

FAILS = []


def check(label, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + label + ("" if ok else f"   -> {detail}"))
    if not ok:
        FAILS.append(label)


def fresh_app(uri, **cfg):
    cfg["SQLALCHEMY_DATABASE_URI"] = uri
    cfg.setdefault("TESTING", True)
    app = create_app(cfg)
    with app.app_context():
        db.drop_all()
        db.create_all()
    return app


OK_STATUS = ("展示中", "在途", "已出", "心愿单")

# 测试库统一写到 .tmp/testdb/：以前直接落在 instance/ 里，
# 跑一次 smoke 就多一个 verify*.db，把真实数据目录搞得乱七八糟。
TEST_DB_DIR = os.path.join(HERE, "tests", ".build", "testdb")
# 站点自己的 smoke 也把库和上传写到 tests/.build/，别碰 instance/
os.makedirs(TEST_DB_DIR, exist_ok=True)


def db_uri(name):
    """拼一个指向 .tmp/testdb/ 的 sqlite URI（Windows 路径要转成 /）。"""
    return "sqlite:///" + os.path.join(TEST_DB_DIR, name).replace("\\", "/")


# 备份也写到临时目录：不然测试（尤其种子脚本自己 create_app）会往
# 真实的 instance/backups/ 里丢测试库的备份
os.environ["GU_BACKUP_DIR"] = os.path.join(TEST_DB_DIR, "backups")
# 上传的测试图片同理：不这么设的话，每次跑测试都会往 static/uploads/
# 里丢一堆没人引用的图（真实数据目录里就攒了 15 个孤儿文件）
os.environ["GU_UPLOAD_DIR"] = os.path.join(TEST_DB_DIR, "uploads")


print("\n=== 1. 认证 ===")
app = fresh_app(db_uri("verify.db"), WTF_CSRF_ENABLED=False)

anon = app.test_client()
r = anon.get("/")
check("匿名访问 / 跳转登录", r.status_code == 302 and "/login" in r.headers.get("Location", ""),
      f"{r.status_code} {r.headers.get('Location')}")
check("GET /login 200", anon.get("/login").status_code == 200)

r = anon.post("/register", data={"username": "tester", "password": "abc"})
body = r.get_data(as_text=True)
check("短密码被拒", r.status_code == 200 and "至少 6 位" in body, r.status_code)

r = anon.post("/register", data={"username": "ab", "password": "abc123"})
body = r.get_data(as_text=True)
check("短用户名被拒", r.status_code == 200 and "用户名长度" in body, r.status_code)

r = anon.post("/register", data={"username": "tester", "password": "abc123"})
check("注册成功跳首页", r.status_code == 302 and r.headers.get("Location", "").endswith("/"),
      f"{r.status_code} {r.headers.get('Location')}")

body = anon.get("/").get_data(as_text=True)
check("已登录首页 200 且渲染正常", "我的谷仓" in body)
check("首页退出是 POST 表单", 'action="/logout"' in body)

anon2 = app.test_client()
r = anon2.post("/register", data={"username": "tester", "password": "abc123"})
check("重名被拒", "已被占用" in r.get_data(as_text=True))

r = anon2.post("/login", data={"username": "tester", "password": "wrong"})
check("密码错误被拒", "用户名或密码错误" in r.get_data(as_text=True))

r = anon2.post("/login", data={"username": "tester", "password": "abc123"})
check("登录成功跳首页", r.status_code == 302 and r.headers.get("Location", "").endswith("/"), r.status_code)

print("\n=== 2. 输入校验 ===")
r = anon.post("/items/add", data={"name": "  白兔 吧唧  ", "emoji": "🐰",
                                  "count": "abc", "status": "hacker"})
check("非法 count/status 不 500", r.status_code == 302, r.status_code)
with app.app_context():
    it = GuItem.query.filter_by(name="白兔 吧唧").first()
    check("名称去空白后入库", it is not None)
    check("非法 count 回退 1", it is not None and it.count == 1, it and it.count)
    check("非法 status 回退 displaying", it is not None and it.status == "displaying", it and it.status)

anon.post("/items/add", data={"name": "超量", "count": "12345", "status": "sold"})
anon.post("/items/add", data={"name": "负数", "count": "-5", "status": "sold"})
anon.post("/items/add", data={"name": "长" * 100, "status": "displaying"})
anon.post("/items/add", data={"name": "emoji超长", "emoji": "🎁" * 20, "status": "wishlist"})
with app.app_context():
    check("count 上限 9999", GuItem.query.filter_by(name="超量").first().count == 9999)
    check("count 负数归 1", GuItem.query.filter_by(name="负数").first().count == 1)
    check("名称截断到 64", len(GuItem.query.filter_by(name="长" * 64).first().name) == 64)
    check("emoji 截断到 8", len(GuItem.query.filter_by(name="emoji超长").first().emoji) == 8)

with app.app_context():
    before = GuItem.query.count()
r = anon.post("/items/add", data={"name": "   ", "status": "sold"})
with app.app_context():
    after = GuItem.query.count()
check("空名称入不了库", before == after, f"{before} -> {after}")
check("空名称 flash 提示可见", "请填写谷子名称" in anon.get("/").get_data(as_text=True))
anon.post("/items/add", data={"name": "正常添加", "count": "3", "status": "in_transit"})
check("添加成功 flash 可见", "已添加「正常添加」" in anon.get("/").get_data(as_text=True))

print("\n=== 3. 统计一致性 ===")
r = anon.get("/api/stats")
st = r.get_json()
check("GET /api/stats 200", r.status_code == 200, r.status_code)
check("四类之和 == total",
      st["displaying"] + st["in_transit"] + st["sold"] + st["wishlist"] == st["total"], st)
check("month_add == total（全是本月新增）", st["month_add"] == st["total"], st)

r = anon.get("/api/items")
rows = r.get_json()
check("GET /api/items 200", r.status_code == 200, r.status_code)
check("api/items 条数 == total", len(rows) == st["total"], f'{len(rows)} vs {st["total"]}')
check("status_text 全部合法", all(x["status_text"] in OK_STATUS for x in rows),
      {x["status_text"] for x in rows})

body = anon.get("/").get_data(as_text=True)
check("仪表盘四宫格数字与 API 一致",
      f'<b>{st["displaying"]}</b>' in body and f'<b>{st["wishlist"]}</b>' in body)

print("\n=== 4. 删除与越权 ===")
anon.post("/items/add", data={"name": "待删除", "count": "1", "status": "sold"})
with app.app_context():
    target_id = GuItem.query.filter_by(name="待删除").first().id
    other = User(username="other")
    other.set_password("abc123")
    db.session.add(other)
    db.session.flush()
    other_item = GuItem(user_id=other.id, name="别人的谷子", count=1, status="displaying")
    db.session.add(other_item)
    db.session.commit()
    other_item_id = other_item.id

r = anon.post(f"/items/{other_item_id}/delete")
check("删别人的谷子 404", r.status_code == 404, r.status_code)
with app.app_context():
    check("别人的谷子未被动", db.session.get(GuItem, other_item_id) is not None)

r = anon.post(f"/items/{target_id}/delete")
check("删自己的谷子跳首页", r.status_code == 302, r.status_code)
with app.app_context():
    check("自己的谷子已删除", db.session.get(GuItem, target_id) is None)

print("\n=== 5. 登出与 user_loader 健壮性 ===")
check("GET /logout 405", anon.get("/logout").status_code == 405,
      anon.get("/logout").status_code)
r = anon.post("/logout")
check("POST /logout 跳登录", r.status_code == 302 and "/login" in r.headers.get("Location", ""),
      r.status_code)
check("登出后 / 受保护", anon.get("/").status_code == 302)

with app.app_context():
    cb = login_manager._user_callback
    check('load_user("abc") 不抛异常且返回 None', cb("abc") is None)
    check("load_user(None) 返回 None", cb(None) is None)
    check('load_user("1") 正常返回用户', cb("1") is not None)

print("\n=== 6. CSRF 保护（未关闭） ===")
app2 = fresh_app(db_uri("verify_csrf.db"))
c2 = app2.test_client()
html = c2.get("/register").get_data(as_text=True)
m = re.search(r'name="csrf_token" value="([^"]+)"', html)
check("注册页含 csrf_token", bool(m))
r = c2.post("/register", data={"csrf_token": m.group(1), "username": "csrfuser",
                               "password": "abc123"})
check("带 token 注册成功", r.status_code == 302, r.status_code)

r = c2.post("/items/add", data={"name": "无token"})
check("缺 token 的请求被拒（不放行/不 500）", r.status_code in (302, 400), r.status_code)
# flash 存在 session 里，由下一个请求消费
check("缺 token 时给出提示", "页面已过期" in c2.get("/").get_data(as_text=True))
with app2.app_context():
    check("缺 token 的数据没入库", GuItem.query.filter_by(name="无token").first() is None)

html = c2.get("/").get_data(as_text=True)
m2 = re.search(r'name="csrf_token" value="([^"]+)"', html)
check("首页含 csrf_token（添加/删除表单）", bool(m2))
# AJAX 缺 token 时也要给 JSON，前端才能在弹窗里就地提示（而不是整页跳走）
r = c2.post("/categories/add", data={"kind": "type", "name": "没token"},
            headers={"X-Requested-With": "XMLHttpRequest"})
check("AJAX 缺 CSRF token 返回 400 JSON",
      r.status_code == 400 and r.get_json()["ok"] is False,
      (r.status_code, r.get_data(as_text=True)[:60]))

r = c2.post("/items/add", data={"csrf_token": m2.group(1), "name": "带token的谷子",
                                "count": "2", "status": "sold"})
check("带 token 添加成功", r.status_code == 302, r.status_code)
with app2.app_context():
    row = GuItem.query.filter_by(name="带token的谷子").first()
    check("带 token 的数据正确入库", row is not None and row.count == 2 and row.status == "sold")

print("\n=== 7. 真实库完整性 + 种子脚本 ===")
# 7a) 真实的 instance/gu.db：_ensure_schema 补过列之后数据必须完好
app3 = create_app({"TESTING": True})
with app3.app_context():
    demo = User.query.filter_by(username="demo").first()
    n_items = GuItem.query.filter_by(user_id=demo.id).count() if demo else 0
    cols = {c["name"] for c in sa_inspect(db.engine).get_columns("gu_items")}
    stats = db.session.get(User, demo.id).get_stats() if demo else {}
check("真实库能打开，demo 账号在", demo is not None)
check("demo / demo123 密码校验通过", demo is not None and demo.check_password("demo123"))
check("老库自动补上 image 列", "image" in cols, sorted(cols))
check("老库自动补上在途字段",
      {"expect_date", "channel", "order_no", "price", "arrived_at"} <= cols, sorted(cols))
check("补列没有让数据变少", n_items >= 6, n_items)
check("真实库统计自洽",
      stats["displaying"] + stats["in_transit"] + stats["sold"] + stats["wishlist"]
      == stats["total"], stats)

# 模型字段必须在真实库里都存在：漏把新字段写进 _COLUMN_ADDITIONS，
# 老库上就会 no such column（新建的库看不出来，所以这条必须针对真实库查）
with app3.app_context():
    _insp = sa_inspect(db.engine)
    col_gaps = {}
    for _model in (User, GuItem, Category, Reminder, Exchange):
        _table = _model.__tablename__
        _db_cols = {c["name"] for c in _insp.get_columns(_table)}
        _missed = sorted({c.name for c in _model.__table__.columns} - _db_cols)
        if _missed:
            col_gaps[_table] = _missed
check("真实库的列和模型完全一致（迁移没漏）", not col_gaps, col_gaps)

# 7b) 种子脚本：用临时库真跑一遍（绝不动真实库）
os.environ["DATABASE_URL"] = db_uri("verify_seed.db")
from init_db import seed  # noqa: E402
seed()
app_seed = create_app({"TESTING": True})
with app_seed.app_context():
    s_demo = User.query.filter_by(username="demo").first()
    s_items = GuItem.query.all()
    s_transit = [i for i in s_items if i.status == "in_transit"]
    s_stats = s_demo.get_stats()
    seed_info = {
        "items": len(s_items),
        "types": Category.query.filter_by(kind="type").count(),
        "ips": Category.query.filter_by(kind="ip").count(),
        "transit": len(s_transit),
        "overdue": sum(1 for i in s_items if i.overdue),
        "transit_filled": all(i.channel and i.order_no and i.price is not None
                              for i in s_transit),
        "login": bool(s_demo and s_demo.check_password("demo123")),
        "cats_linked": all(i.category_type_id or i.category_ip_id for i in s_items),
        "sold_income": s_stats["sold_income"],
        "sold_profit": s_stats["sold_profit"],
    }
    mate = User.query.filter_by(username="nene").first()
    mate_ex = Exchange.query.filter_by(user_id=mate.id).all() if mate else []
    seed_info["mate"] = (mate.username if mate else None,
                         mate.region if mate else None, len(mate_ex))
    seed_info["ex_filled"] = bool(mate_ex) and all(
        e.region and e.title and e.distance_km is not None for e in mate_ex)
os.environ.pop("DATABASE_URL", None)
check("种子账号 demo/demo123 可登录", seed_info["login"] is True)
check("种子 7 件谷子", seed_info["items"] == 7, seed_info["items"])
check("种子品类/IP 按条目自动去重",
      seed_info["types"] == 7 and seed_info["ips"] == 6,
      (seed_info["types"], seed_info["ips"]))
check("种子每件谷子都挂了分类", seed_info["cats_linked"] is True)
check("种子 2 件在途、其中 1 件超期",
      seed_info["transit"] == 2 and seed_info["overdue"] == 1,
      (seed_info["transit"], seed_info["overdue"]))
check("种子的在途都带了渠道/单号/金额", seed_info["transit_filled"] is True)
check("种子已出带成交价，能算盈亏（含部分已出的那件）",
      seed_info["sold_income"] == 208.0 and seed_info["sold_profit"] == 50.0,
      (seed_info["sold_income"], seed_info["sold_profit"]))
check("种子还建了一个谷友账号（换谷板用）",
      seed_info["mate"] == ("nene", "上海 徐汇", 2), seed_info["mate"])
check("种子的换谷信息字段齐全", seed_info["ex_filled"] is True, seed_info["ex_filled"])

print("\n=== 8. 图片上传 ===")


def make_image(fmt="PNG", size=(60, 40), mode="RGB"):
    """在内存里造一张测试图片，避免仓库里放二进制测试素材。"""
    buf = io.BytesIO()
    color = (255, 0, 0) if mode == "RGB" else (255, 0, 0, 128)
    PILImage.new(mode, size, color).save(buf, format=fmt)
    buf.seek(0)
    return buf


def upload(client, **fields):
    """fields 里的 image 传 (BytesIO, filename) 元组。"""
    return client.post("/items/add", data=fields, content_type="multipart/form-data")


app4 = fresh_app(db_uri("verify_img.db"), WTF_CSRF_ENABLED=False)
ci = app4.test_client()
up_dir = app4.config["UPLOAD_FOLDER"]
files_before = set(os.listdir(up_dir)) if os.path.isdir(up_dir) else set()
check("上传目录已自动创建", os.path.isdir(up_dir), up_dir)
ci.post("/register", data={"username": "imguser", "password": "abc123"})

# --- 正常上传：中文文件名 ---
r = upload(ci, name="白兔 吧唧", count="2", status="displaying",
           image=(make_image(), "白兔 吧唧.png"))
with app4.app_context():
    stored = GuItem.query.filter_by(name="白兔 吧唧").first().image
check("上传成功并入库文件名", bool(stored), stored)
check("文件名是 uuid.ext（不是中文原名）",
      bool(stored) and re.fullmatch(r"[0-9a-f]{32}\.png", stored) is not None, stored)
check("图片真的落盘", bool(stored) and os.path.exists(os.path.join(up_dir, stored)))
if stored:
    with PILImage.open(os.path.join(up_dir, stored)) as im:
        check("落盘的是可解码图片", im.size == (60, 40), im.size)
html = ci.get("/").get_data(as_text=True)
check("首页渲染出缩略图 img", bool(stored) and f'/static/uploads/{stored}"' in html)

# --- 伪造图片：文本改名成 .png ---
upload(ci, name="假图片", count="1", status="displaying",
       image=(io.BytesIO(b"this is definitely not an image"), "fake.png"))
with app4.app_context():
    fake = GuItem.query.filter_by(name="假图片").first()
check("伪造的 .png 被拒（image 为空）", fake is not None and fake.image is None,
      fake and fake.image)
check("伪造图片仍保留了文字信息", fake is not None)
check("伪造图片给出提示", "图片格式不支持" in ci.get("/").get_data(as_text=True))

# --- 扩展名骗人：PNG 改名成 .jpg ---
upload(ci, name="改名PNG", count="1", status="displaying",
       image=(make_image(mode="RGBA"), "其实是png.jpg"))
with app4.app_context():
    renamed = GuItem.query.filter_by(name="改名PNG").first().image
check("按真实格式存为 .png（RGBA 不会被当成 JPEG 写失败）",
      bool(renamed) and renamed.endswith(".png"), renamed)

# --- 路径穿越文件名 ---
upload(ci, name="穿越", count="1", status="displaying",
       image=(make_image(), "../../../evil.png"))
with app4.app_context():
    traversal = GuItem.query.filter_by(name="穿越").first().image
check("穿越文件名被忽略，仍是安全 uuid 名",
      bool(traversal) and re.fullmatch(r"[0-9a-f]{32}\.png", traversal) is not None, traversal)
check("上传目录之外没有生成文件",
      not os.path.exists(os.path.join(HERE, "evil.png"))
      and not os.path.exists(os.path.join(os.path.dirname(HERE), "evil.png")))

# --- 大图压缩 ---
upload(ci, name="大图", count="1", status="displaying",
       image=(make_image(size=(2000, 1000)), "big.png"))
with app4.app_context():
    big_name = GuItem.query.filter_by(name="大图").first().image
max_side = app4.config["IMAGE_MAX_SIDE"]
with PILImage.open(os.path.join(up_dir, big_name)) as im:
    check(f"超过 {max_side}px 的图被等比压缩",
          im.size == (max_side, max_side // 2), im.size)

# --- 超过 5MB ---
huge = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * (5 * 1024 * 1024 + 64))
r = upload(ci, name="超大", count="1", status="displaying", image=(huge, "huge.png"))
check("超过 5MB 不 500（友好跳转）", r.status_code in (302, 400, 413), r.status_code)
check("超限时给出提示", "图片太大了" in ci.get("/").get_data(as_text=True))
with app4.app_context():
    check("超限的图没有入库", GuItem.query.filter_by(name="超大").first() is None)

# --- API 暴露图片地址 ---
rows_img = ci.get("/api/items").get_json()
big_row = [x for x in rows_img if x["name"] == "大图"]
check("api/items 带 image_url",
      bool(big_row) and big_row[0]["image_url"].startswith("/static/uploads/"), big_row)
check("无图条目 image_url 为 null", any(x["image_url"] is None for x in rows_img))
check("无图条目仍回落到 emoji", "🎁" in ci.get("/").get_data(as_text=True))

# --- 删除时一起清磁盘文件 ---
with app4.app_context():
    del_id = GuItem.query.filter_by(name="白兔 吧唧").first().id
ci.post(f"/items/{del_id}/delete")
check("删除谷子时磁盘文件一起清掉",
      bool(stored) and not os.path.exists(os.path.join(up_dir, stored)))
with app4.app_context():
    check("数据库记录已删除", db.session.get(GuItem, del_id) is None)

print("\n=== 9. 详情弹窗（点小图看大图） ===")
html_v = ci.get("/").get_data(as_text=True)
cards = re.findall(r'<div class="g-card"[^>]*>', html_v)
with_img = [c for c in cards if "data-image=" in c]

check("网格里有卡片", len(cards) >= 2, len(cards))
check("每张卡片都是可点击的详情入口",
      bool(cards) and all('data-item-view' in c and 'role="button"' in c
                          and 'tabindex="0"' in c for c in cards))
with app4.app_context():
    n_img = GuItem.query.filter(GuItem.image.isnot(None)).count()
    n_plain = GuItem.query.filter(GuItem.image.is_(None)).count()
check("带 data-image 的卡片数 == 库里有图的记录数",
      len(with_img) == n_img, f"{len(with_img)} vs {n_img}")
check("不带 data-image 的卡片数 == 库里无图的记录数",
      len(cards) - len(with_img) == n_plain,
      f"{len(cards) - len(with_img)} vs {n_plain}")
check("详情弹窗结构完整",
      all(t in html_v for t in ('id="viewModal"', 'class="viewer-shot"',
                                'id="viewerImg"', 'id="viewerEmoji"',
                                'id="viewerName"', 'id="viewerStatus"',
                                'id="viewerCount"', 'id="viewerTime"',
                                'id="viewerCatType"', 'id="viewerCatIP"',
                                'id="rowPrice"', 'id="viewerPrice"',
                                'id="rowChannel"', 'id="viewerChannel"',
                                'id="rowOrderNo"', 'id="viewerOrderNo"',
                                'id="rowExpect"', 'id="viewerExpect"',
                                'id="rowArrived"', 'id="viewerArrived"',
                                'id="rowSoldPrice"', 'id="viewerSoldPrice"',
                                'id="rowProfit"', 'id="viewerProfit"',
                                'id="rowSoldSource"', 'id="viewerSoldSource"',
                                'id="rowSoldAt"', 'id="viewerSoldAt"',
                                'id="rowCount"', 'id="viewerCountText"',
                                'id="rowWant"', 'id="viewerWant"',
                                'id="rowWantLink"', 'id="viewerWantLink"',
                                'id="rowWantNote"', 'id="viewerWantNote"',
                                'id="viewerLink"')))

# 卡片上的 data-* 必须和详情 JS 读的键对得上，否则点开就是空的
viewer_keys = ["name", "status", "count", "time", "emoji",
               "colorStart", "colorEnd", "catType", "catIp",
               "catTypeName", "catIpName",
               "priceText", "channelText", "orderNo", "expectText",
               "expectOverdue", "arrivedText",
               "expectDate", "channel", "price",
               "soldPrice", "soldPriceText", "soldMethod", "soldChannel",
               "soldSourceText", "soldAt", "soldAtText",
               "profitText", "profitState",
               "remaining", "soldCount", "countText", "partial",
               "wantLevel", "wantLevelText", "wantLevelState",
               "budgetText", "wantLink", "wantLinkHref", "wantNote"]
missing = [k for k in viewer_keys
           if f'data-{re.sub(r"(?<!^)(?=[A-Z])", "-", k).lower()}=' not in html_v]
check("详情 JS 读的 data-* 模板全都渲染了", not missing, missing)

# JS 里 getElementById 引用的 id 必须真实存在（防手滑改名 / 漏 include 片段）
# 两类弹窗只在用到它们的页面 include（JS 里都是先判空再用）：
#   提醒弹窗 → 仪表盘 + /reminders
#   换谷弹窗 → 只有 /exchanges（仪表盘的换谷板块是只读的）
OPTIONAL_MODAL_IDS = {
    "reminderModal", "reminderForm", "reminderFormTitle",
    "remTitle", "remTime", "remPriority", "remSubtitle", "remLink",
    "exchangeModal", "exchangeForm", "exchangeFormTitle",
    "exTitle", "exWant", "exEmoji", "exDistance",
    "exPlace", "exContact", "exNote",
}
base_js = open(os.path.join(HERE, "templates", "base.html"), encoding="utf-8").read()
js_ids = set(re.findall(r"getElementById\(\s*'([^']+)'\s*\)", base_js))
missing_ids = sorted(i for i in js_ids
                     if f'id="{i}"' not in html_v and i not in OPTIONAL_MODAL_IDS)
check("JS 引用的元素 id 页面里都有", not missing_ids, missing_ids)

# 时间要按本地时区渲染（入库是 naive UTC，直接显示会差 8 小时）
with app4.app_context():
    big_item = GuItem.query.filter_by(name="大图").first()
    created_text = big_item.created_text
check("created_text 是「年-月-日 时:分」格式",
      re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", created_text or "") is not None,
      created_text)
check("data-time 渲染的就是 created_text",
      f'data-time="{created_text}"' in html_v, created_text)

# 清理本次测试产生的上传文件
for extra in set(os.listdir(up_dir)) - files_before:
    try:
        os.remove(os.path.join(up_dir, extra))
    except OSError:
        pass

print("\n=== 10. 编辑功能 ===")
app5 = fresh_app(db_uri("verify_edit.db"), WTF_CSRF_ENABLED=False)
ce = app5.test_client()
edir = app5.config["UPLOAD_FOLDER"]
files_e = set(os.listdir(edir)) if os.path.isdir(edir) else set()
ce.post("/register", data={"username": "edituser", "password": "abc123"})

upload(ce, name="原始名字", emoji="🐰", count="2", status="displaying",
       image=(make_image(size=(300, 200)), "orig.png"))
with app5.app_context():
    _it = GuItem.query.filter_by(name="原始名字").first()
    iid, orig_img = _it.id, _it.image
check("建好一条带照片的谷子", bool(orig_img) and os.path.exists(os.path.join(edir, orig_img)))

# 只改文字字段
ce.post(f"/items/{iid}/edit", data={"name": "改过的名字", "emoji": "🍵",
                                    "count": "9", "status": "sold"})
with app5.app_context():
    _it = db.session.get(GuItem, iid)
    got = (_it.name, _it.emoji, _it.count, _it.status, _it.image)
check("名称已更新", got[0] == "改过的名字", got[0])
check("emoji 已更新", got[1] == "🍵", got[1])
check("数量已更新", got[2] == 9, got[2])
check("状态已更新", got[3] == "sold", got[3])
check("没传新图时保留原照片", got[4] == orig_img, got[4])
check("原图文件还在", os.path.exists(os.path.join(edir, orig_img)))

# 非法输入
ce.post(f"/items/{iid}/edit", data={"name": "再改", "count": "abc", "status": "hack"})
with app5.app_context():
    _it = db.session.get(GuItem, iid)
    got = (_it.status, _it.count, _it.name)
check("编辑时非法 status 回退 displaying", got[0] == "displaying", got[0])
check("编辑时非法 count 回退 1", got[1] == 1, got[1])

ce.post(f"/items/{iid}/edit", data={"name": "   ", "status": "sold"})
with app5.app_context():
    name_after = db.session.get(GuItem, iid).name
check("空名称被拒（原名不变）", name_after == "再改", name_after)
check("空名称有提示", "请填写谷子名称" in ce.get("/").get_data(as_text=True))

# 换图：新图生效，旧文件清掉
ce.post(f"/items/{iid}/edit", data={"name": "换图后", "emoji": "🐰", "count": "3",
                                    "status": "wishlist",
                                    "image": (make_image(size=(500, 250)), "new.png")},
        content_type="multipart/form-data")
with app5.app_context():
    new_img = db.session.get(GuItem, iid).image
check("换成新图", bool(new_img) and new_img != orig_img, new_img)
check("旧图文件已清掉", not os.path.exists(os.path.join(edir, orig_img)))
check("新图文件已落盘", os.path.exists(os.path.join(edir, new_img)))

# 传了非法新图：保留原照片
ce.post(f"/items/{iid}/edit", data={"name": "坏图", "count": "3", "status": "wishlist",
                                    "image": (io.BytesIO(b"nope"), "bad.png")},
        content_type="multipart/form-data")
with app5.app_context():
    kept = db.session.get(GuItem, iid).image
check("非法新图不覆盖原照片", kept == new_img, kept)
check("非法新图有提示", "已保留原来的照片" in ce.get("/").get_data(as_text=True))

# 勾选删除照片
ce.post(f"/items/{iid}/edit", data={"name": "无图了", "count": "1", "status": "sold",
                                    "remove_image": "1"})
with app5.app_context():
    after_rm = db.session.get(GuItem, iid).image
check("勾选后照片被移除", after_rm is None, after_rm)
check("被移除的照片文件也删了", not os.path.exists(os.path.join(edir, new_img)))

# 越权编辑
with app5.app_context():
    _o = User(username="editother")
    _o.set_password("abc123")
    db.session.add(_o)
    db.session.flush()
    _oi = GuItem(user_id=_o.id, name="别人的", count=1, status="displaying")
    db.session.add(_oi)
    db.session.commit()
    oi_id = _oi.id
r = ce.post(f"/items/{oi_id}/edit", data={"name": "篡改", "count": "1", "status": "sold"})
check("改别人的谷子 404", r.status_code == 404, r.status_code)
with app5.app_context():
    other_name = db.session.get(GuItem, oi_id).name
check("别人的谷子没被改", other_name == "别人的", other_name)

for f in set(os.listdir(edir)) - files_e:
    try:
        os.remove(os.path.join(edir, f))
    except OSError:
        pass

print("\n=== 11. 仪表盘谷柜只露 6 件 + 查看全部入口 ===")
app6 = fresh_app(db_uri("verify_grid.db"), WTF_CSRF_ENABLED=False)
cg = app6.test_client()
cg.post("/register", data={"username": "griduser", "password": "abc123"})

# 0 件时入口也必须存在
html_empty = cg.get("/").get_data(as_text=True)
check("0 件时也有「查看全部」入口",
      'id="allItemsLink"' in html_empty and 'href="/items"' in html_empty)
check("0 件时入口不显示数量", "查看全部 ›" in html_empty)
check("0 件时显示空状态", "还没有谷子" in html_empty)

for n in range(8):
    cg.post("/items/add", data={"name": f"谷子{n:02d}", "count": "1",
                                "status": "displaying"})
html_g = cg.get("/").get_data(as_text=True)
cards_g = re.findall(r'<div class="g-card', html_g)
card_names = re.findall(r'data-name="([^"]+)"', html_g)
check("上限常量是 6", PANEL_PREVIEW == 6, PANEL_PREVIEW)
check("仪表盘只渲染 6 件（服务端截断）", len(cards_g) == 6, len(cards_g))
check("截断的是最新的 6 件，且顺序是最新在前",
      card_names == ["谷子07", "谷子06", "谷子05", "谷子04", "谷子03", "谷子02"],
      card_names)
check("入口文案带总数 8", "查看全部 8 件 ›" in html_g)
check("入口是 <a> 链接而且始终指向 /items",
      bool(re.search(r'<a class="more" id="allItemsLink" href="/items"', html_g)))
check("仪表盘预览不分组（没有分组标题）", "group-head" not in html_g)
check("仪表盘没有维度切换条（切换只在谷柜页）", "seg-btn" not in html_g)
check("仪表盘有分类管理入口", 'data-modal-open="catModal"' in html_g)
check("仪表盘有分类管理弹窗", 'id="catModal"' in html_g)
style_css = open(os.path.join(HERE, "static", "style.css"), encoding="utf-8").read()
check("CSS 里没有残留的折叠类", "is-extra" not in style_css)
base_src = open(os.path.join(HERE, "templates", "base.html"), encoding="utf-8").read()
check("折叠不再依赖 JS（上一版的 gridToggle 已移除）",
      "gridToggle" not in base_src and "expanded" not in base_src)

print("\n=== 12. /items 谷柜页 ===")
check("匿名访问 /items 跳登录",
      (lambda r: r.status_code == 302 and "/login" in r.headers.get("Location", ""))(
          app6.test_client().get("/items")))
check("匿名访问 /items 不被放行",
      app6.test_client().get("/items").status_code == 302)

r = cg.get("/items")
html_all = r.get_data(as_text=True)
n_all = len(re.findall(r'<div class="g-card', html_all))
names_all = re.findall(r'data-name="([^"]+)"', html_all)
check("GET /items 200", r.status_code == 200, r.status_code)
check("页面标题是谷柜", "<title>谷柜 · 谷仓</title>" in html_all)
check("8 件时谷柜页显示全部 8 件", n_all == 8, n_all)
check("谷柜页不做 6 件截断（00..07 全在）",
      sorted(names_all) == [f"谷子{n:02d}" for n in range(8)], names_all)
check("只显示自己的谷子（没有别人的条目）",
      "白兔 吧唧" not in names_all and len(names_all) == 8, names_all)
check("只显示自己的谷子（不是别人的）",
      "谷子00" in html_all and "白兔 吧唧" not in html_all)
check("有返回首页的入口", 'href="/"' in html_all and "返回首页" in html_all)
check("谷柜页也带详情弹窗",
      'id="viewModal"' in html_all and 'id="viewerEditBtn"' in html_all)
check("谷柜页也带添加弹窗", 'id="addModal"' in html_all and 'class="fab"' in html_all)
check("谷柜页卡片同样带编辑地址",
      bool(re.search(r'data-edit-url="/items/\d+/edit"', html_all)))
check("谷柜页有 CSRF token（弹窗能用）", 'name="csrf_token"' in html_all)
check("谷柜页默认平铺（没有分组标题）", "group-head" not in html_all)
seg_labels = re.findall(r'class="seg-btn[^"]*"[^>]*>\s*([^<]+?)\s*<', html_all)
check("切换条有三个选项：全部/按品类/按作品IP",
      seg_labels == ["全部", "按品类", "按作品IP"], seg_labels)
check("默认「全部」是选中态",
      bool(re.search(r'class="seg-btn active"[^>]*>\s*全部\s*<', html_all)))
check("三个切换链接都带 group 参数",
      all(f"group={m}" in html_all for m in ("all", "type", "ip")))
check("谷柜页有分类管理入口", 'data-modal-open="catModal"' in html_all)
# 谷柜页通过 include 复用 _grid/_viewer/_add，漏 include 会让 JS 找不到元素
missing_all = sorted(i for i in js_ids
                     if f'id="{i}"' not in html_all and i not in OPTIONAL_MODAL_IDS)
check("谷柜页包含 JS 引用的全部元素 id（片段 include 没漏）", not missing_all, missing_all)
check("两个页面用的卡片结构一致",
      set(re.findall(r'data-(name|status-value|edit-url|color-start)=', html_all))
      == set(re.findall(r'data-(name|status-value|edit-url|color-start)=', html_g)))

# 少于 6 件时入口照样能跳转
with app6.app_context():
    drop_ids = [i.id for i in GuItem.query.order_by(GuItem.id.desc()).limit(2).all()]
for i in drop_ids:
    cg.post(f"/items/{i}/delete")
html_g6 = cg.get("/").get_data(as_text=True)
check("正好 6 件时入口仍在且照样可跳转",
      'id="allItemsLink"' in html_g6 and 'href="/items"' in html_g6)
check("正好 6 件时入口文案带数量", "查看全部 6 件 ›" in html_g6)
check("正好 6 件时仪表盘没有折叠项", "is-extra" not in html_g6)
n_all6 = len(re.findall(r'<div class="g-card', cg.get("/items").get_data(as_text=True)))
check("正好 6 件时谷柜页仍是 6 件", n_all6 == 6, n_all6)

print("\n=== 13. 自定义分类（周边品类 / 作品IP） ===")
GROUP_RE = (r'class="sec-head group-head">\s*<h2>([^<]+)</h2>'
            r'\s*<span class="more">(\d+) 件</span>')


def group_headers(html):
    return re.findall(GROUP_RE, html)


app7 = fresh_app(db_uri("verify_cat.db"), WTF_CSRF_ENABLED=False)
cc = app7.test_client()
cc.post("/register", data={"username": "catuser", "password": "abc123"})

# --- 增删分类 ---
for kind, name in [("type", "吧唧"), ("type", "色纸"),
                   ("ip", "某某"), ("ip", "破云")]:
    cc.post("/categories/add", data={"kind": kind, "name": name})
with app7.app_context():
    cats = {(c.kind, c.name): c.id for c in Category.query.all()}
check("4 个分类都建好了", len(cats) == 4, sorted(cats))
check("两个维度分开存", {k for k, _ in cats} == {"type", "ip"}, sorted(cats))

cc.post("/categories/add", data={"kind": "type", "name": "吧唧"})
cc.post("/categories/add", data={"kind": "type", "name": "   "})
cc.post("/categories/add", data={"kind": "hack", "name": "乱来"})
with app7.app_context():
    n_after = Category.query.count()
check("重名/空名/非法维度都不入库", n_after == 4, n_after)
check("重名有提示", "已经在" in cc.get("/").get_data(as_text=True))

cc.post("/categories/add", data={"kind": "ip", "name": "吧唧"})
with app7.app_context():
    n_same = Category.query.count()
    same_id = Category.query.filter_by(kind="ip", name="吧唧").first().id
check("同名不同维度可以共存", n_same == 5, n_same)
cc.post(f"/categories/{same_id}/delete")

# --- 给谷子挂分类 ---
tid_badge, tid_paper = cats[("type", "吧唧")], cats[("type", "色纸")]
iid_mou, iid_poyun = cats[("ip", "某某")], cats[("ip", "破云")]
cc.post("/items/add", data={"name": "白兔吧唧", "count": "1", "status": "displaying",
                            "category_type_id": str(tid_badge),
                            "category_ip_id": str(iid_mou)})
cc.post("/items/add", data={"name": "某某色纸", "count": "1", "status": "displaying",
                            "category_type_id": str(tid_paper),
                            "category_ip_id": str(iid_mou)})
cc.post("/items/add", data={"name": "破云立牌", "count": "1", "status": "displaying",
                            "category_ip_id": str(iid_poyun)})
cc.post("/items/add", data={"name": "没分类的", "count": "1", "status": "displaying"})
with app7.app_context():
    rows = {i.name: (i.category_type_id, i.category_ip_id) for i in GuItem.query.all()}
check("品类+IP 同时存上", rows["白兔吧唧"] == (tid_badge, iid_mou), rows["白兔吧唧"])
check("只选 IP 也能存", rows["破云立牌"] == (None, iid_poyun), rows["破云立牌"])
check("都不选就是未分类", rows["没分类的"] == (None, None), rows["没分类的"])

# --- 两种维度随时切换（分组只在谷柜页） ---
html_t = cc.get("/items?group=type").get_data(as_text=True)
html_ip = cc.get("/items?group=ip").get_data(as_text=True)
g_t, g_ip = group_headers(html_t), group_headers(html_ip)
check("按品类分 3 组（吧唧/色纸/未分类）",
      [g[0] for g in g_t] == ["吧唧", "色纸", "未分类"], g_t)
check("按品类分组计数正确", [g[1] for g in g_t] == ["1", "1", "2"], g_t)
check("按IP分 3 组（某某/破云/未分类）",
      [g[0] for g in g_ip] == ["某某", "破云", "未分类"], g_ip)
check("同一批谷子换维度后重新分组", [g[1] for g in g_ip] == ["2", "1", "1"], g_ip)
check("分组里没有空组", all(int(g[1]) > 0 for g in g_t + g_ip))
check("按品类时「按品类」是选中态",
      bool(re.search(r'class="seg-btn active"[^>]*>按品类<', html_t)))
check("按IP时「按作品IP」是选中态",
      bool(re.search(r'class="seg-btn active"[^>]*>按作品IP<', html_ip)))
check("两种维度的链接都在（随时可切）",
      'group=type' in html_t and 'group=ip' in html_t)
check("按品类分组时卡片补显示 IP",
      '<span class="g-sub">某某</span>' in html_t)
check("按IP分组时卡片补显示品类",
      '<span class="g-sub">吧唧</span>' in html_ip and '<span class="g-sub">色纸</span>' in html_ip)

# 详情弹窗要能看到分类（卡片上带好名字，避免点开再查一次库）
check("卡片带品类名（详情弹窗展示用）",
      'data-cat-type-name="色纸"' in html_t, )
check("卡片带 IP 名", 'data-cat-ip-name="某某"' in html_t)
check("没分类的条目名字段是「未分类」",
      'data-cat-type-name="未分类"' in html_t and 'data-cat-ip-name="未分类"' in html_t)

# 默认进谷柜 = 平铺全部，即使已经有分类
cc_fresh = app7.test_client()
cc_fresh.post("/login", data={"username": "catuser", "password": "abc123"})
html_default = cc_fresh.get("/items").get_data(as_text=True)
check("已有分类时，进谷柜默认仍是「全部」平铺", "group-head" not in html_default)
check("默认「全部」是选中态",
      bool(re.search(r'class="seg-btn active"[^>]*>\s*全部\s*<', html_default)))
check("?group=all 同样是平铺",
      "group-head" not in cc_fresh.get("/items?group=all").get_data(as_text=True))
check("切到按品类就分组",
      "group-head" in cc_fresh.get("/items?group=type").get_data(as_text=True))
check("不记忆上次选择：再进谷柜又回到「全部」",
      "group-head" not in cc_fresh.get("/items").get_data(as_text=True)
      and "group-head" in html_ip)

html_keep = cc.get("/items?group=ip").get_data(as_text=True)
check("带 ?group= 时按该维度分组（不依赖上次选择）",
      [g[0] for g in group_headers(html_keep)] == [g[0] for g in g_ip],
      group_headers(html_keep))
check("不带 ?group= 时回到「全部」平铺（没有分组标题）",
      not group_headers(cc.get("/items").get_data(as_text=True)),
      group_headers(cc.get("/items").get_data(as_text=True)))
check("分类管理弹窗在仪表盘和谷柜页都有",
      'id="catModal"' in html_t
      and 'id="catModal"' in cc.get("/items").get_data(as_text=True))

# --- 权限与维度校验 ---
with app7.app_context():
    _o = User(username="catother")
    _o.set_password("abc123")
    db.session.add(_o)
    db.session.flush()
    _oc = Category(user_id=_o.id, kind="type", name="别人的品类")
    db.session.add(_oc)
    db.session.commit()
    other_cat_id = _oc.id

cc.post("/items/add", data={"name": "想偷分类", "count": "1", "status": "displaying",
                            "category_type_id": str(other_cat_id)})
cc.post("/items/add", data={"name": "维度错位", "count": "1", "status": "displaying",
                            "category_type_id": str(iid_mou)})
with app7.app_context():
    steal = GuItem.query.filter_by(name="想偷分类").first().category_type_id
    wrong = GuItem.query.filter_by(name="维度错位").first().category_type_id
check("用别人的分类 id 会被忽略", steal is None, steal)
check("把 IP 塞进品类维度会被忽略", wrong is None, wrong)
check("删别人的分类 404",
      cc.post(f"/categories/{other_cat_id}/delete").status_code == 404, )
with app7.app_context():
    check("别人的分类没被删", db.session.get(Category, other_cat_id) is not None)

# --- 编辑分类 ---
with app7.app_context():
    edit_id = GuItem.query.filter_by(name="破云立牌").first().id
cc.post(f"/items/{edit_id}/edit", data={"name": "破云立牌", "count": "1",
                                        "status": "displaying",
                                        "category_type_id": str(tid_paper),
                                        "category_ip_id": str(iid_poyun)})
with app7.app_context():
    got = db.session.get(GuItem, edit_id).category_type_id
check("编辑能给已有谷子补分类", got == tid_paper, got)
cc.post(f"/items/{edit_id}/edit", data={"name": "破云立牌", "count": "1",
                                        "status": "displaying",
                                        "category_type_id": "", "category_ip_id": ""})
with app7.app_context():
    _g = db.session.get(GuItem, edit_id)
    cleared = (_g.category_type_id, _g.category_ip_id)
check("编辑能把分类清空", cleared == (None, None), cleared)

# --- 删分类后谷子变未分类，另一个维度不受影响 ---
cc.post(f"/categories/{tid_badge}/delete")
with app7.app_context():
    _g = GuItem.query.filter_by(name="白兔吧唧").first()
    after_del = (_g.category_type_id, _g.category_ip_id)
check("删品类后该谷子变未分类", after_del[0] is None, after_del)
check("另一个维度不受影响", after_del[1] == iid_mou, after_del)
check("删除提示里说明影响了几件",
      "变为未分类" in cc.get("/").get_data(as_text=True))

# --- API 暴露分类 ---
api_rows = cc.get("/api/items").get_json()
row = [x for x in api_rows if x["name"] == "白兔吧唧"][0]
check("api/items 带两个维度的分类名",
      row["category_type"] == "未分类" and row["category_ip"] == "某某", row)
check("api/items 带分类 id",
      row["category_type_id"] is None and row["category_ip_id"] == iid_mou, row)

# --- 仪表盘预览不分类，只有谷柜页才按分类看 ---
dash_cat = cc.get("/").get_data(as_text=True)
check("仪表盘即使有分类也不分组", "group-head" not in dash_cat)
check("仪表盘卡片不显示分类小字", 'class="g-sub"' not in dash_cat)
check("谷柜页才分组（有分组标题）", "group-head" in html_ip)
check("谷柜页才显示分类小字", 'class="g-sub"' in html_ip)
check("仪表盘没有切换条", "seg-btn" not in dash_cat)

# --- 分类增删的 AJAX 契约（弹窗不重载就靠它） ---
AJAX = {"X-Requested-With": "XMLHttpRequest"}
r = cc.post("/categories/add", data={"kind": "ip", "name": "吞海"}, headers=AJAX)
data = r.get_json()
check("AJAX 加分类返回 200 JSON", r.status_code == 200 and data["ok"] is True,
      (r.status_code, data))
check("JSON 带 id/name/kind",
      data.get("id") and data.get("name") == "吞海" and data.get("kind") == "ip", data)
check("JSON 带该维度最新总数", data.get("count") == 3, data)
check("JSON 带删除地址（前端不拼路由）",
      data.get("delete_url") == f"/categories/{data['id']}/delete", data)
check("JSON 带提示文案", "吞海" in data.get("message", ""), data)

r = cc.post("/categories/add", data={"kind": "ip", "name": "吞海"}, headers=AJAX)
check("AJAX 重名返回 409 且 ok=False",
      r.status_code == 409 and r.get_json()["ok"] is False, (r.status_code, r.get_json()))
r = cc.post("/categories/add", data={"kind": "ip", "name": "  "}, headers=AJAX)
check("AJAX 空名返回 400", r.status_code == 400, r.status_code)
r = cc.post("/categories/add", data={"kind": "xx", "name": "乱"}, headers=AJAX)
check("AJAX 非法维度返回 400", r.status_code == 400, r.status_code)

r = cc.post(f"/categories/{data['id']}/delete", headers=AJAX)
dd = r.get_json()
check("AJAX 删分类返回 200 JSON", r.status_code == 200 and dd["ok"] is True,
      (r.status_code, dd))
check("删分类 JSON 带剩余数", dd.get("count") == 2, dd)
check("删分类 JSON 带 kind（前端据此清下拉选项）", dd.get("kind") == "ip", dd)
r = cc.post("/categories/999999/delete", headers=AJAX)
check("AJAX 删不存在的分类返回 404 JSON",
      r.status_code == 404 and r.get_json()["ok"] is False, r.status_code)

# 不带 AJAX 头时仍然是原来的整页跳转（渐进增强）
r = cc.post("/categories/add", data={"kind": "ip", "name": "破云2", "next": "/items"})
check("普通表单仍是 302 回跳", r.status_code == 302, r.status_code)
check("回跳到 next 指定的页面", r.headers.get("Location") == "/items",
      r.headers.get("Location"))
r = cc.post("/categories/add", data={"kind": "ip", "name": "x",
                                     "next": "//evil.example.com"})
check("next 是外站时被忽略（防开放重定向）",
      r.headers.get("Location") == "/", r.headers.get("Location"))

print("\n=== 14. 在途功能 ===")
app8 = fresh_app(db_uri("verify_transit.db"), WTF_CSRF_ENABLED=False)
ct = app8.test_client()
ct.post("/register", data={"username": "transituser", "password": "abc123"})
today = local_today()


def add_item(client, **fields):
    return client.post("/items/add", data=fields)


add_item(ct, name="超期的", count="1", status="in_transit",
         expect_date=(today - timedelta(days=2)).isoformat(),
         channel="daigou", order_no="SF001", price="268.5")
add_item(ct, name="今天到", count="1", status="in_transit",
         expect_date=today.isoformat(), channel="pintuan", price="100")
add_item(ct, name="五天后", count="1", status="in_transit",
         expect_date=(today + timedelta(days=5)).isoformat())
add_item(ct, name="没填日期", count="1", status="in_transit")
add_item(ct, name="不在途", count="1", status="displaying",
         expect_date=(today + timedelta(days=9)).isoformat())

with app8.app_context():
    rows = {i.name: i for i in GuItem.query.all()}
    got = {n: (r.expect_date, r.channel, r.order_no, r.price) for n, r in rows.items()}
check("预计到货日期入库", got["超期的"][0] == today - timedelta(days=2), got["超期的"][0])
check("渠道入库（存 key）", got["超期的"][1] == "daigou", got["超期的"][1])
check("单号入库", got["超期的"][2] == "SF001", got["超期的"][2])
check("金额入库并保留两位", got["超期的"][3] == 268.5, got["超期的"][3])
check("没填的就是 None",
      got["没填日期"][0] is None and got["没填日期"][1] is None
      and got["没填日期"][3] is None, got["没填日期"])

# --- 倒计时 / 超期判定 ---
with app8.app_context():
    r = {i.name: i for i in GuItem.query.all()}
    calc = {
        "超期的": (r["超期的"].days_to_arrive, r["超期的"].overdue,
                 r["超期的"].countdown_text, r["超期的"].expect_text),
        "今天到": (r["今天到"].days_to_arrive, r["今天到"].overdue,
                 r["今天到"].countdown_text, r["今天到"].expect_text),
        "五天后": (r["五天后"].days_to_arrive, r["五天后"].overdue,
                 r["五天后"].countdown_text, r["五天后"].expect_text),
        "没填日期": (r["没填日期"].days_to_arrive, r["没填日期"].overdue,
                  r["没填日期"].countdown_text, r["没填日期"].expect_text),
        "不在途": (r["不在途"].days_to_arrive, r["不在途"].overdue,
                 r["不在途"].countdown_text, r["不在途"].expect_text),
    }
    amount = r["超期的"].price_text
    source = r["超期的"].source_text
check("超期算负数天", calc["超期的"][0] == -2 and calc["超期的"][1] is True, calc["超期的"])
check("超期文案", calc["超期的"][2] == "超期 2 天", calc["超期的"][2])
check("超期详情文案带日期", "已超期 2 天" in calc["超期的"][3], calc["超期的"][3])
check("今天到", calc["今天到"][0] == 0 and calc["今天到"][2] == "今天到"
      and calc["今天到"][1] is False, calc["今天到"])
check("5 天后", calc["五天后"][0] == 5 and calc["五天后"][2] == "5 天后", calc["五天后"])
check("没填日期不算超期",
      calc["没填日期"][0] is None and calc["没填日期"][1] is False
      and calc["没填日期"][2] == "未填预计到货", calc["没填日期"])
check("不在途的不算超期（即使日期已过）",
      calc["不在途"][1] is False and "已超期" not in calc["不在途"][3], calc["不在途"])
check("金额显示成 ¥268.50", amount == "¥268.50", amount)
check("来源行拼了渠道和单号", source == "代购 · SF001", source)

# --- 非法输入 ---
add_item(ct, name="脏数据", count="1", status="in_transit",
         expect_date="2026-13-99", channel="hack", price="abc")
add_item(ct, name="负金额", count="1", status="in_transit", price="-5")
add_item(ct, name="带符号金额", count="1", status="in_transit", price="¥1,234.567")
with app8.app_context():
    dirty = GuItem.query.filter_by(name="脏数据").first()
    neg = GuItem.query.filter_by(name="负金额").first()
    fmt = GuItem.query.filter_by(name="带符号金额").first()
    bad = (dirty.expect_date, dirty.channel, dirty.price)
check("非法日期/渠道/金额都当成没填", bad == (None, None, None), bad)
check("负数金额被拒", neg.price is None, neg.price)
check("带 ¥ 和千分位的金额能解析，并四舍五入到两位",
      fmt.price == 1234.57, fmt.price)

# --- 汇总统计（7 件在途：4 件正式的 + 3 件脏数据测试） ---
with app8.app_context():
    st = db.session.get(User, User.query.filter_by(username="transituser").first().id).get_stats()
check("在途件数", st["in_transit"] == 7, st["in_transit"])
check("超期件数", st["transit_overdue"] == 1, st["transit_overdue"])
check("在途金额合计只算在途的、且两位小数",
      st["transit_amount"] == round(268.5 + 100 + 1234.57, 2), st["transit_amount"])

# --- 仪表盘板块 ---
dash = ct.get("/").get_data(as_text=True)
dash_rows = re.findall(r'class="s-name">([^<]+)<', dash)
check("仪表盘有「在途」板块", 'id="transitLink"' in dash and "在途" in dash)
check(f"仪表盘最多列 {TRANSIT_PREVIEW} 件", len(dash_rows) == TRANSIT_PREVIEW, dash_rows)
check("仪表盘按预计到货排序（超期最前）", dash_rows[0] == "超期的", dash_rows)
check("仪表盘显示超期角标", "warn-chip" in dash and "1 件超期" in dash, )
check("仪表盘有去在途专区的入口",
      bool(re.search(r'id="transitLink" href="/transit"', dash)))
check("仪表盘在途行也带详情数据",
      'class="side-item' in dash and 'data-expect-text=' in dash)
check("仪表盘在途行为超期的加了红边样式", "is-overdue" in dash)

# --- 卡片/详情带在途数据（趁还有超期条目先查） ---
card_html = ct.get("/items").get_data(as_text=True)
check("卡片带在途数据（金额/渠道/单号/倒计时）",
      'data-price-text="¥' in card_html and 'data-channel-text="' in card_html
      and 'data-order-no="' in card_html and 'data-expect-text="' in card_html)
check("超期条目在卡片上带标记", 'data-expect-overdue="1"' in card_html)
check("非在途条目的 overdue 标记是 0", 'data-expect-overdue="0"' in card_html)

# --- /transit 专区 ---
anon_t = app8.test_client()
check("/transit 匿名访问跳登录",
      anon_t.get("/transit").status_code == 302,
      anon_t.get("/transit").status_code)
r = ct.get("/transit")
html_t2 = r.get_data(as_text=True)
names_t = re.findall(r'class="s-name">([^<]+)<', html_t2)
check("GET /transit 200", r.status_code == 200, r.status_code)
check("专区列出全部 7 件在途", len(names_t) == 7, names_t)
check("专区顺序：有日期的按日期升序排前面",
      names_t[:3] == ["超期的", "今天到", "五天后"], names_t)
check("没填日期的排在最后",
      set(names_t[3:]) == {"没填日期", "脏数据", "负金额", "带符号金额"}, names_t)
check("专区不包含非在途的", "不在途" not in names_t, names_t)
check("专区标题显示件数与合计",
      "7 件在路上" in html_t2 and "合计 ¥" in html_t2, )
check("专区显示超期件数", "1 件超期" in html_t2)
check("每行都有「确认到货」按钮",
      html_t2.count('class="s-act"') == 7, html_t2.count('class="s-act"'))
check("确认到货表单带 next（回到原页）",
      html_t2.count('name="next" value="/transit') >= 7,
      html_t2.count('name="next" value="/transit'))
check("专区也带详情弹窗（点行能看大图）",
      'id="viewModal"' in html_t2 and 'class="side-item' in html_t2)
check("专区也能直接添加（统一入口里能选在途）",
      'data-modal-open="addSheet"' in html_t2
      and 'data-add-kind="in_transit"' in html_t2)

# --- 一键确认到货 ---
with app8.app_context():
    arrive_id = GuItem.query.filter_by(name="超期的").first().id
r = ct.post(f"/items/{arrive_id}/arrive", data={"next": "/transit"})
check("确认到货 302 回跳", r.status_code == 302, r.status_code)
check("回跳到 next 指定的页面", r.headers.get("Location") == "/transit",
      r.headers.get("Location"))
with app8.app_context():
    arrived = db.session.get(GuItem, arrive_id)
    after = (arrived.status, arrived.arrived_at is not None, arrived.arrived_text)
check("状态改成展示中", after[0] == "displaying", after[0])
check("记录了到货时间", after[1] is True, after[2])
check("到货时间能格式化", len(after[2]) == 16, after[2])
after_list = ct.get("/transit").get_data(as_text=True)
check("到货后从在途清单消失", "超期的" not in re.findall(r'class="s-name">([^<]+)<', after_list))
check("到货后有提示", "已确认到货" in after_list)
check("到货后不再算超期", "1 件超期" not in after_list, )

# 重复确认 / 别人的谷子
r = ct.post(f"/items/{arrive_id}/arrive")
check("已不在途的再确认给提示且不改状态",
      "已经不在途" in ct.get("/").get_data(as_text=True))
with app8.app_context():
    other = User(username="transitother")
    other.set_password("abc123")
    db.session.add(other)
    db.session.flush()
    oi = GuItem(user_id=other.id, name="别人的在途", count=1, status="in_transit")
    db.session.add(oi)
    db.session.commit()
    oi_id = oi.id
check("确认别人的谷子到货 404",
      ct.post(f"/items/{oi_id}/arrive").status_code == 404)
with app8.app_context():
    check("别人的在途没被动",
          db.session.get(GuItem, oi_id).status == "in_transit")

# --- 编辑在途字段 ---
with app8.app_context():
    eid = GuItem.query.filter_by(name="五天后").first().id
ct.post(f"/items/{eid}/edit", data={"name": "五天后", "count": "1",
                                    "status": "in_transit",
                                    "expect_date": (today + timedelta(days=1)).isoformat(),
                                    "channel": "official", "order_no": "OFF-9",
                                    "price": "88"})
with app8.app_context():
    ed = db.session.get(GuItem, eid)
    edited = (ed.expect_date, ed.channel, ed.order_no, ed.price, ed.countdown_text)
check("编辑能改到货日期", edited[0] == today + timedelta(days=1), edited[0])
check("编辑能改渠道/单号/金额",
      edited[1] == "official" and edited[2] == "OFF-9" and edited[3] == 88.0, edited)
check("倒计时跟着变", edited[4] == "明天到", edited[4])
# 不在途的条目编辑时不应该丢掉已填的在途信息（表单里那栏是收起的，但值照样提交）
with app8.app_context():
    keep_id = GuItem.query.filter_by(name="不在途").first().id
ct.post(f"/items/{keep_id}/edit", data={"name": "不在途", "count": "1",
                                        "status": "displaying",
                                        "expect_date": (today + timedelta(days=9)).isoformat()})
with app8.app_context():
    kept = db.session.get(GuItem, keep_id)
check("展示中的谷子也能保留预计到货（作为记录）",
      kept.expect_date == today + timedelta(days=9), kept.expect_date)

# --- 卡片与详情的数据（超期条目已到货，这里只看剩余字段） ---
check("卡片仍带在途数据", 'data-expect-text="' in ct.get("/items").get_data(as_text=True))

# --- API ---
api = ct.get("/api/items").get_json()
row = [x for x in api if x["name"] == "今天到"][0]
check("api/items 带在途字段",
      row["expect_date"] == today.isoformat() and row["days_to_arrive"] == 0
      and row["overdue"] is False and row["channel"] == "pintuan"
      and row["channel_text"] == "拼团" and row["price"] == 100.0, row)
stats_api = ct.get("/api/stats").get_json()
check("api/stats 带在途汇总（到货后扣掉那件与它的金额）",
      stats_api["in_transit"] == 6 and stats_api["transit_overdue"] == 0
      and stats_api["transit_amount"] == round(100 + 1234.57 + 88, 2), stats_api)

print("\n=== 15. 已出功能 ===")
app9 = fresh_app(db_uri("verify_sold.db"), WTF_CSRF_ENABLED=False)
cs = app9.test_client()
cs.post("/register", data={"username": "solduser", "password": "abc123"})

add_item(cs, name="赚了的", count="1", status="sold", price="140", sold_price="168",
         sold_method="sell", sold_channel="xianyu",
         sold_at=(today - timedelta(days=2)).isoformat())
add_item(cs, name="亏了的", count="1", status="sold", price="300", sold_price="250",
         sold_method="sell", sold_channel="qun",
         sold_at=(today - timedelta(days=5)).isoformat())
add_item(cs, name="平平的", count="1", status="sold", price="100", sold_price="100",
         sold_method="gift", sold_at=today.isoformat())
add_item(cs, name="换出去的", count="1", status="sold", sold_method="swap",
         sold_channel="offline", sold_at=(today - timedelta(days=1)).isoformat())
add_item(cs, name="没出手记录", count="1", status="sold")
add_item(cs, name="还在收藏", count="1", status="displaying")

with app9.app_context():
    rows = {i.name: i for i in GuItem.query.all()}
    got = {n: (r.sold_price, r.price, r.sold_method, r.sold_channel, r.sold_at)
           for n, r in rows.items()}
    calc = {n: (r.profit, r.profit_text, r.profit_state, r.sold_source_text)
            for n, r in rows.items()}
check("成交价与入手价入库", got["赚了的"][:2] == (168.0, 140.0), got["赚了的"])
check("出手方式/平台入库（存 key）",
      got["赚了的"][2] == "sell" and got["赚了的"][3] == "xianyu", got["赚了的"])
check("出手日期入库", got["赚了的"][4] == today - timedelta(days=2), got["赚了的"][4])
check("没填的就是 None",
      got["没出手记录"] == (None, None, None, None, None), got["没出手记录"])

check("赚了：+¥28.00 / up", calc["赚了的"][:3] == (28.0, "+¥28.00", "up"), calc["赚了的"])
check("亏了：-¥50.00 / down",
      calc["亏了的"][:3] == (-50.0, "-¥50.00", "down"), calc["亏了的"])
check("不赚不亏：¥0.00 / even",
      calc["平平的"][:3] == (0.0, "¥0.00", "even"), calc["平平的"])
check("缺价钱时算不出盈亏",
      calc["换出去的"][:3] == (None, "", ""), calc["换出去的"])
check("出手信息拼的是方式 + 平台",
      calc["赚了的"][3] == "卖出 · 闲鱼", calc["赚了的"][3])
check("换出 + 线下", calc["换出去的"][3] == "换出 · 线下", calc["换出去的"][3])

# --- 非法输入 ---
add_item(cs, name="脏已出", count="1", status="sold", sold_method="hack",
         sold_channel="hack", sold_price="abc", sold_at="2026-13-99")
with app9.app_context():
    dirty = GuItem.query.filter_by(name="脏已出").first()
    bad = (dirty.sold_method, dirty.sold_channel, dirty.sold_price, dirty.sold_at)
check("非法的出手方式/平台/成交价/日期都当成没填", bad == (None, None, None, None), bad)

# --- 汇总统计 ---
with app9.app_context():
    st = db.session.get(User, User.query.filter_by(username="solduser").first().id).get_stats()
check("已出件数", st["sold"] == 6, st["sold"])
check("回血合计（成交价之和）", st["sold_income"] == 518.0, st["sold_income"])
check("盈亏合计只算两个价都有的", st["sold_profit"] == -22.0, st["sold_profit"])

# --- 仪表盘板块 ---
dash2 = cs.get("/").get_data(as_text=True)
dash_sold = re.findall(r'class="s-name">([^<]+)<', dash2)
check("仪表盘有「已出」板块", 'id="soldLink"' in dash2 and "已出" in dash2)
check(f"仪表盘最多列 {SOLD_PREVIEW} 件已出", len(dash_sold) == SOLD_PREVIEW, dash_sold)
check("按出手日期从近到远", dash_sold[0] == "平平的", dash_sold)
check("标题带盈亏小标签", "profit-chip" in dash2 and "-¥22.00" in dash2)
check("有去已出专区的入口", bool(re.search(r'id="soldLink" href="/sold"', dash2)))
check("赚了的显示绿色盈亏标签", 'class="s-pill up"' in dash2)
check("仪表盘已出行也带详情数据",
      'class="side-item' in dash2 and 'data-profit-text=' in dash2)

# --- /sold 专区 ---
check("/sold 匿名访问跳登录", app9.test_client().get("/sold").status_code == 302)
r = cs.get("/sold")
html_s = r.get_data(as_text=True)
names_s = re.findall(r'class="s-name">([^<]+)<', html_s)
check("GET /sold 200", r.status_code == 200, r.status_code)
check("专区列出全部 6 件已出", len(names_s) == 6, names_s)
check("顺序：有日期的按日期倒序",
      names_s[:4] == ["平平的", "换出去的", "赚了的", "亏了的"], names_s)
check("没填日期的排最后",
      set(names_s[4:]) == {"没出手记录", "脏已出"}, names_s)
check("专区不包含没出掉的", "还在收藏" not in names_s, names_s)
check("标题显示累计出掉件数、回血和盈亏",
      "累计出掉 6 件" in html_s and "回血 ¥518.00" in html_s and "¥22.00" in html_s)
check("已出页说明只讲排序和撤回，不再讲「剩下的仍在谷柜」",
      (lambda hint: "按出手日期" in hint and "撤回" in hint and "在谷柜" not in hint)(
          re.search(r'class="transit-hint">(.*?)</p>', html_s, re.S).group(1)))
check("每行都有「撤回」按钮",
      html_s.count('class="s-act"') == 6, html_s.count('class="s-act"'))
check("撤回表单带 next（回到原页）",
      html_s.count('name="next" value="/sold') >= 6,
      html_s.count('name="next" value="/sold'))
check("缺价钱的显示「无记录」而不是假盈亏", "无记录" in html_s)
check("专区也带详情弹窗", 'id="viewModal"' in html_s)
check("专区也能直接添加（统一入口里能选已出）",
      'data-modal-open="addSheet"' in html_s
      and 'data-add-kind="sold"' in html_s)

# --- 一键撤回 ---
with app9.app_context():
    back_id = GuItem.query.filter_by(name="赚了的").first().id
r = cs.post(f"/items/{back_id}/restore", data={"next": "/sold"})
check("撤回 302 回跳", r.status_code == 302, r.status_code)
check("回跳到 next 指定的页面", r.headers.get("Location") == "/sold",
      r.headers.get("Location"))
with app9.app_context():
    back = db.session.get(GuItem, back_id)
    after = (back.status, back.sold_price, back.sold_at, back.price)
check("状态改回展示中", after[0] == "displaying", after[0])
check("出手记录仍然保留（成交价/日期/入手价都在）",
      after[1] == 168.0 and after[2] == today - timedelta(days=2) and after[3] == 140.0,
      after)
after_sold = cs.get("/sold").get_data(as_text=True)
check("撤回后从已出清单消失",
      "赚了的" not in re.findall(r'class="s-name">([^<]+)<', after_sold))
check("撤回后有提示", "已撤回" in after_sold)
check("撤回后盈亏合计跟着变", "-¥50.00" in after_sold, )

# 重复撤回 / 别人的
cs.post(f"/items/{back_id}/restore")
check("已不是已出的再撤回归提示",
      "没有出货记录" in cs.get("/sold").get_data(as_text=True))
with app9.app_context():
    other9 = User(username="soldother")
    other9.set_password("abc123")
    db.session.add(other9)
    db.session.flush()
    oi9 = GuItem(user_id=other9.id, name="别人的已出", count=1, status="sold")
    db.session.add(oi9)
    db.session.commit()
    oi9_id = oi9.id
check("撤回别人的谷子 404", cs.post(f"/items/{oi9_id}/restore").status_code == 404)
with app9.app_context():
    check("别人的已出没被动",
          db.session.get(GuItem, oi9_id).status == "sold")

# --- 编辑已出字段 ---
with app9.app_context():
    eid9 = GuItem.query.filter_by(name="亏了的").first().id
cs.post(f"/items/{eid9}/edit", data={"name": "亏了的", "count": "1", "status": "sold",
                                     "price": "300", "sold_price": "330",
                                     "sold_method": "sell", "sold_channel": "weidian",
                                     "sold_at": today.isoformat()})
with app9.app_context():
    ed9 = db.session.get(GuItem, eid9)
    edited9 = (ed9.sold_price, ed9.sold_channel, ed9.profit_text, ed9.profit_state)
check("编辑能改成交价", edited9[0] == 330.0, edited9[0])
check("编辑能改出手平台", edited9[1] == "weidian", edited9[1])
check("盈亏跟着变成 +¥30.00", edited9[2:4] == ("+¥30.00", "up"), edited9[2:4])
# 展示中的条目编辑时不该丢掉已出记录（那栏收起了，但值照样提交）
with app9.app_context():
    keep9 = GuItem.query.filter_by(name="赚了的").first().id
cs.post(f"/items/{keep9}/edit", data={"name": "赚了的", "count": "1",
                                      "status": "displaying",
                                      "sold_price": "168", "price": "140"})
with app9.app_context():
    kept9 = db.session.get(GuItem, keep9)
check("展示中的条目也能保留出手记录",
      kept9.sold_price == 168.0 and kept9.price == 140.0,
      (kept9.sold_price, kept9.price))

# --- 卡片数据 + API ---
card_s = cs.get("/items").get_data(as_text=True)
check("卡片带已出数据（撤回回来的那件也在谷柜里）",
      'data-sold-price-text="¥' in card_s and 'data-profit-text=' in card_s
      and 'data-profit-state=' in card_s and 'data-sold-at-text=' in card_s)
check("已出的谷子不再出现在谷柜页",
      "亏了的" not in re.findall(r'data-name="([^"]+)"', card_s),
      re.findall(r'data-name="([^"]+)"', card_s))
api9 = cs.get("/api/items").get_json()
row9 = [x for x in api9 if x["name"] == "亏了的"][0]
check("api/items 带已出字段",
      row9["sold_price"] == 330.0 and row9["sold_method"] == "sell"
      and row9["sold_method_text"] == "卖出" and row9["sold_channel_text"] == "微店"
      and row9["sold_at"] == today.isoformat() and row9["profit"] == 30.0
      and row9["profit_text"] == "+¥30.00", row9)
stats9 = cs.get("/api/stats").get_json()
check("api/stats 带回血与盈亏（撤回一件 + 改过价之后）",
      stats9["sold"] == 5 and stats9["sold_income"] == 430.0
      and stats9["sold_profit"] == 30.0, stats9)

print("\n=== 16. 部分已出：谷柜只展出剩余件数 ===")
app10 = fresh_app(db_uri("verify_partial.db"), WTF_CSRF_ENABLED=False)
cp = app10.test_client()
cp.post("/register", data={"username": "partuser", "password": "abc123"})

add_item(cp, name="出了两个的", count="5", sold_count="2", status="displaying",
         price="100", sold_price="150", sold_method="sell", sold_channel="xianyu")
add_item(cp, name="全出完的", count="3", sold_count="3", status="displaying")
add_item(cp, name="整件出掉", count="1", status="sold")
add_item(cp, name="没出过的", count="4", status="displaying")
add_item(cp, name="一件而已", count="1", status="displaying")

with app10.app_context():
    rows = {i.name: i for i in GuItem.query.all()}
    qty = {n: (r.count, r.sold_qty, r.remaining, r.partially_sold, r.fully_sold,
               r.status) for n, r in rows.items()}
check("部分已出：剩 3 件、状态仍是展示中",
      qty["出了两个的"][:2] == (5, 2) and qty["出了两个的"][2] == 3
      and qty["出了两个的"][3] is True and qty["出了两个的"][5] == "displaying",
      qty["出了两个的"])
check("已出数 = 总数 → 自动变成已出状态",
      qty["全出完的"][2] == 0 and qty["全出完的"][4] is True
      and qty["全出完的"][5] == "sold", qty["全出完的"])
check("状态填已出但没填件数 → 件数对齐到总数",
      qty["整件出掉"][1] == 1 and qty["整件出掉"][5] == "sold", qty["整件出掉"])
check("没出过的：remaining = count",
      qty["没出过的"][1] == 0 and qty["没出过的"][2] == 4
      and qty["没出过的"][3] is False, qty["没出过的"])

# --- 谷柜不再展出已出的 ---
with app10.app_context():
    gal = re.findall(r'data-name="([^"]+)"',
                     cp.get("/items").get_data(as_text=True))
check("谷柜页不含已出的", "全出完的" not in gal and "整件出掉" not in gal, gal)
check("谷柜页含部分已出的（还在柜里）", "出了两个的" in gal, gal)
check("谷柜页件数 = 5 条记录 − 2 件已出 = 3 条", len(gal) == 3, gal)
dash_p = cp.get("/").get_data(as_text=True)
check("仪表盘「查看全部」也按谷柜里的条数算",
      "查看全部 3 件 ›" in dash_p, re.findall(r'查看全部[^<]*', dash_p))
check("统计面板仍按记录条数（4 个状态之和 = 总数）",
      (lambda s: s["displaying"] + s["in_transit"] + s["sold"] + s["wishlist"]
       == s["total"] == 5)(cp.get("/api/stats").get_json()),
      cp.get("/api/stats").get_json())

# --- 角标显示剩余件数 ---
gal_html = cp.get("/items").get_data(as_text=True)
by_name = {}
for chunk in gal_html.split('<div class="g-card"')[1:]:
    n = re.search(r'data-name="([^"]+)"', chunk)
    b = re.search(r'class="g-badge">(\d+)</i>', chunk)
    if n:
        by_name[n.group(1)] = b.group(1) if b else None
check("出了 2/5 的那件角标是 3（显示剩余，不是总数 5）",
      by_name.get("出了两个的") == "3", by_name)
check("没出过的角标还是 4", by_name.get("没出过的") == "4", by_name)
check("卡片上不再标注「已出 N / 共 M」", "g-sold" not in gal_html and "已出 2 / 共 5" not in gal_html)

# --- 详情里的件数说明 ---
check("卡片带件数说明", 'data-count-text="共 5 件 · 已出 2 件 · 剩 3 件"' in gal_html)
check("卡片带 partial 标记", 'data-partial="1"' in gal_html and 'data-partial="0"' in gal_html)
check("卡片带剩余件数", 'data-remaining="3"' in gal_html)

# --- 编辑：改件数会连带调整 ---
with app10.app_context():
    pid = GuItem.query.filter_by(name="出了两个的").first().id
cp.post(f"/items/{pid}/edit", data={"name": "出了两个的", "count": "5",
                                    "sold_count": "5", "status": "displaying"})
with app10.app_context():
    got = db.session.get(GuItem, pid)
    after5 = (got.sold_qty, got.remaining, got.status)
check("编辑把已出数改成 5 → 自动变已出", after5 == (5, 0, "sold"), after5)
check("改成已出后从谷柜消失",
      "出了两个的" not in re.findall(r'data-name="([^"]+)"',
                                     cp.get("/items").get_data(as_text=True)))

cp.post(f"/items/{pid}/edit", data={"name": "出了两个的", "count": "5",
                                    "sold_count": "2", "status": "displaying"})
with app10.app_context():
    got = db.session.get(GuItem, pid)
check("再改回 2 件已出 → 又回到谷柜",
      (got.sold_qty, got.remaining, got.status) == (2, 3, "displaying"),
      (got.sold_qty, got.remaining, got.status))

# 件数被改小到比已出数还小时，夹回总数
cp.post(f"/items/{pid}/edit", data={"name": "出了两个的", "count": "2",
                                    "sold_count": "99", "status": "displaying"})
with app10.app_context():
    got = db.session.get(GuItem, pid)
    clamp = (got.count, got.sold_qty, got.remaining, got.status)
check("件数被改小到比已出数还小时，夹回总数",
      clamp == (2, 2, 0, "sold"), clamp)

# --- 关键：选了「已出」但同时填了小于总数的件数 → 以件数为准，剩下的继续展出 ---
add_item(cp, name="选了已出但有剩", count="4", sold_count="1", status="sold")
with app10.app_context():
    mixed = GuItem.query.filter_by(name="选了已出但有剩").first()
    mixed_state = (mixed.status, mixed.sold_qty, mixed.remaining, mixed.fully_sold)
check("选已出 + 已出1/共4 → 状态回落展示中、剩 3 件、仍在谷柜",
      mixed_state == ("displaying", 1, 3, False), mixed_state)
check("它出现在已出专区（出掉的那 1 件）",
      "选了已出但有剩" in re.findall(
          r'class="s-name">([^<]+)<', cp.get("/sold").get_data(as_text=True)))
sold_row = cp.get("/sold").get_data(as_text=True)
sold_metas = re.findall(r'class="s-meta">([^<]*)<', sold_row)
check("已出行只写出掉的件数，不再补「剩 N 件在谷柜」",
      "出 1 件" in sold_metas and all("在谷柜" not in m for m in sold_metas),
      sold_metas)

# --- 用户举的例子：共 6 件、出掉 1 件 → 谷柜显示 5、已出显示 1 ---
add_item(cp, name="某某色纸", count="6", sold_count="1", status="displaying",
         sold_price="50")
with app10.app_context():
    ex = GuItem.query.filter_by(name="某某色纸").first()
    ex_state = (ex.status, ex.count, ex.sold_qty, ex.remaining, ex.fully_sold)
check("共6出1：状态展示中、总数6、已出1、剩5、仍在展出",
      ex_state == ("displaying", 6, 1, 5, False), ex_state)
gal_ex = cp.get("/items").get_data(as_text=True)
card_ex = [c for c in gal_ex.split('<div class="g-card"')[1:]
           if 'data-name="某某色纸"' in c]
check("谷柜里显示 5 件",
      bool(card_ex) and re.search(r'class="g-badge">5</i>', card_ex[0]) is not None,
      card_ex[0][:200] if card_ex else "没找到卡片")
check("卡片不再标注「已出 1 / 共 6」",
      bool(card_ex) and "g-sold" not in card_ex[0])
sold_ex = cp.get("/sold").get_data(as_text=True)
row_ex = [c for c in sold_ex.split('<div class="side-item')[1:]
          if 'data-name="某某色纸"' in c]
check("已出专区里也有它（代表出掉的那 1 件）", bool(row_ex))
check("已出行写「出 1 件」，不写剩余件数",
      bool(row_ex) and re.search(r'class="s-meta">出 1 件', row_ex[0]) is not None
      and "在谷柜" not in row_ex[0],
      re.findall(r'class="s-meta">([^<]*)<', sold_ex))
check("同一件谷子确实同时出现在两处",
      bool(card_ex) and bool(row_ex))
check("已出专区标题把它算进累计出掉件数",
      "累计出掉" in sold_ex, re.findall(r'累计出掉[^<]*', sold_ex))

# 撤回：部分出货也能撤回，会把出掉的件数清零
with app10.app_context():
    ex_id = GuItem.query.filter_by(name="某某色纸").first().id
cp.post(f"/items/{ex_id}/restore", data={"next": "/sold"})
with app10.app_context():
    back_ex = db.session.get(GuItem, ex_id)
    back_state = (back_ex.status, back_ex.sold_qty, back_ex.remaining)
check("部分已出的也能撤回，件数清零", back_state == ("displaying", 0, 6), back_state)
check("撤回后不再出现在已出专区",
      "某某色纸" not in re.findall(
          r'class="s-name">([^<]+)<', cp.get("/sold").get_data(as_text=True)))

# 反过来：选已出但不填件数 = 整件出完
add_item(cp, name="选已出没填件数", count="4", status="sold")
with app10.app_context():
    whole = GuItem.query.filter_by(name="选已出没填件数").first()
    whole_state = (whole.status, whole.sold_qty, whole.remaining, whole.fully_sold)
check("选已出但没填件数 → 视为整件出完",
      whole_state == ("sold", 4, 0, True), whole_state)
check("整件出完的不在谷柜里",
      "选已出没填件数" not in re.findall(r'data-name="([^"]+)"',
                                        cp.get("/items").get_data(as_text=True)))

# --- 撤回会把已出件数清零，否则会「撤回了却看不见」 ---
with app10.app_context():
    bid = GuItem.query.filter_by(name="全出完的").first().id
cp.post(f"/items/{bid}/restore", data={"next": "/sold"})
with app10.app_context():
    back = db.session.get(GuItem, bid)
    restored = (back.status, back.sold_qty, back.remaining)
check("撤回后状态回展示中、已出件数清零",
      restored == ("displaying", 0, 3), restored)
check("撤回后重新出现在谷柜",
      "全出完的" in re.findall(r'data-name="([^"]+)"',
                               cp.get("/items").get_data(as_text=True)))

# 一条真正的「出掉一部分」链路：编辑里选已出 + 填 2/共5 → 存下来应该还在谷柜
with app10.app_context():
    flow_id = GuItem.query.filter_by(name="没出过的").first().id
cp.post(f"/items/{flow_id}/edit", data={"name": "没出过的", "count": "4",
                                        "sold_count": "2", "status": "sold"})
with app10.app_context():
    flow = db.session.get(GuItem, flow_id)
    flow_state = (flow.status, flow.sold_qty, flow.remaining)
check("编辑里选已出 + 填 2/共4 → 状态回落展示中、剩 2 件",
      flow_state == ("displaying", 2, 2), flow_state)
check("剩下的继续在谷柜展出（这是在途之外最核心的一条）",
      "没出过的" in re.findall(r'data-name="([^"]+)"',
                              cp.get("/items").get_data(as_text=True)))

# --- 已出清单里的件数显示 ---
sold_html = cp.get("/sold").get_data(as_text=True)
metas = re.findall(r'class="s-meta">([^<]*)<', sold_html)
check("已出行第一段是「出 N 件」，且每条都不带「在谷柜」的补提示",
      all(m == "未填成交价和出手日期" or m.startswith("出 ") for m in metas)
      and all("在谷柜" not in m for m in metas), metas)
grid_page = cp.get("/items").get_data(as_text=True)
check("谷柜页也没有「已出 N / 共 M」的标注",
      "g-sold" not in grid_page and "/ 共 " not in grid_page)

# --- API ---
api_p = cp.get("/api/items").get_json()
row_p = [x for x in api_p if x["name"] == "一件而已"][0]
check("api/items 带件数与可见性",
      row_p["count"] == 1 and row_p["sold_count"] == 0 and row_p["remaining"] == 1
      and row_p["partially_sold"] is False and row_p["visible"] is True, row_p)
row_partial = [x for x in api_p if x["name"] == "没出过的"][0]
check("api/items 对部分已出的给出 remaining/partially_sold/visible",
      row_partial["count"] == 4 and row_partial["sold_count"] == 2
      and row_partial["remaining"] == 2 and row_partial["partially_sold"] is True
      and row_partial["visible"] is True, row_partial)
row_h = [x for x in api_p if x["name"] == "整件出掉"][0]
check("已出的 visible=False", row_h["visible"] is False, row_h)

print("\n=== 17. 心愿单 ===")
app11 = fresh_app(db_uri("verify_wish.db"), WTF_CSRF_ENABLED=False)
cw = app11.test_client()
cw.post("/register", data={"username": "wishuser", "password": "abc123"})

add_item(cw, name="很想要贵", count="1", status="wishlist", want_level="hot",
         budget="500", want_link="https://example.com/a", want_note="等再贩")
add_item(cw, name="很想要便宜", count="1", status="wishlist", want_level="hot",
         budget="100")
add_item(cw, name="一般想要", count="1", status="wishlist", want_level="normal",
         budget="300")
add_item(cw, name="随缘的", count="1", status="wishlist", want_level="maybe")
add_item(cw, name="没分级的", count="1", status="wishlist")
add_item(cw, name="店铺名当链接", count="1", status="wishlist",
         want_link="闲鱼某某店")
add_item(cw, name="危险链接", count="1", status="wishlist",
         want_link="javascript:alert(1)")
add_item(cw, name="在柜里的", count="1", status="displaying")

with app11.app_context():
    rows = {i.name: i for i in GuItem.query.all()}
    got = {n: (r.want_level, r.budget, r.want_link, r.want_note)
           for n, r in rows.items()}
    derived = {n: (r.want_level_text, r.budget_text, r.want_link_href)
               for n, r in rows.items()}
check("想要程度/预算/链接/备注都入库",
      got["很想要贵"] == ("hot", 500.0, "https://example.com/a", "等再贩"),
      got["很想要贵"])
check("没填的字段保持 None", got["随缘的"][1:] == (None, None, None), got["随缘的"])
check("程度文案与预算文案",
      derived["很想要贵"][:2] == ("很想要", "¥500.00"), derived["很想要贵"])
check("http(s) 才被当成链接", derived["很想要贵"][2] == "https://example.com/a")
check("店铺名不当链接", derived["店铺名当链接"][2] == "",
      derived["店铺名当链接"][2])
check("javascript: 这种也不当链接（防 XSS）",
      derived["危险链接"][2] == "", derived["危险链接"][2])

add_item(cw, name="脏心愿", count="1", status="wishlist", want_level="hack",
         budget="abc")
with app11.app_context():
    dirty = GuItem.query.filter_by(name="脏心愿").first()
    bad = (dirty.want_level, dirty.budget)
check("非法想要程度/预算都当成没填", bad == (None, None), bad)

# --- 谷柜不放心愿单 ---
gal = cw.get("/items").get_data(as_text=True)
gal_names = re.findall(r'data-name="([^"]+)"', gal)
check("谷柜里没有心愿单的东西",
      not any(n in gal_names for n in ("很想要贵", "很想要便宜", "一般想要",
                                       "随缘的", "没分级的", "店铺名当链接")),
      gal_names)
check("谷柜里只有展示中的那件", gal_names == ["在柜里的"], gal_names)
dash11 = cw.get("/").get_data(as_text=True)
check("仪表盘谷柜也不含心愿单",
      'data-name="很想要贵"' not in dash11.split('id="wishlistLink"')[0])

# --- 心愿单专区 ---
check("/wishlist 匿名访问跳登录", app11.test_client().get("/wishlist").status_code == 302)
r = cw.get("/wishlist")
html_w = r.get_data(as_text=True)
names_w = re.findall(r'class="s-name">([^<]+)<', html_w)
check("GET /wishlist 200", r.status_code == 200, r.status_code)
check("列出全部 8 件心愿", len(names_w) == 8, names_w)
check("排序：很想要在前，同级里预算低的在前",
      names_w[:2] == ["很想要便宜", "很想要贵"], names_w)
check("然后是 一般 → 随缘 → 未分级",
      names_w[2] == "一般想要" and names_w[3] == "随缘的"
      and set(names_w[4:]) == {"没分级的", "店铺名当链接", "危险链接", "脏心愿"},
      names_w)
check("标题显示件数与总预算",
      "8 件想要" in html_w and "总预算 ¥900.00" in html_w,
      re.findall(r"\d+ 件想要[^<]*", html_w))
check("想要程度上色：很想要=hot", 's-pill want-hot' in html_w)
check("没分级的显示「未分级」", "未分级" in html_w)
check("每行都有「已入手」按钮",
      html_w.count('class="s-act"') == 8, html_w.count('class="s-act"'))
check("已入手表单带 next", html_w.count('name="next" value="/wishlist') >= 8)
check("https 链接渲染成可点的 <a>",
      '<a class="s-link" href="https://example.com/a"' in html_w)
check("店铺名只当纯文本，不是链接",
      "闲鱼某某店" in html_w and 'href="闲鱼某某店"' not in html_w)
check("javascript: 链接不会变成 <a href>（防 XSS）",
      'href="javascript:' not in html_w and "危险链接" in html_w)
check("专区也带详情弹窗", 'id="viewModal"' in html_w)
check("专区也能直接添加（统一入口里能选心愿单）",
      'data-modal-open="addSheet"' in html_w
      and 'data-add-kind="wishlist"' in html_w)

# --- 仪表盘板块 ---
check("仪表盘有心愿单板块与入口",
      'id="wishlistLink"' in dash11 and 'href="/wishlist"' in dash11)
check("仪表盘显示总预算角标", "总预算 ¥900.00" in dash11)

# --- 一键已入手 ---
with app11.app_context():
    hot_id = GuItem.query.filter_by(name="很想要便宜").first().id
r = cw.post(f"/items/{hot_id}/acquire", data={"next": "/wishlist"})
check("已入手 302 回跳", r.status_code == 302, r.status_code)
check("回跳到 next 指定的页面", r.headers.get("Location") == "/wishlist",
      r.headers.get("Location"))
with app11.app_context():
    got_it = db.session.get(GuItem, hot_id)
    after = (got_it.status, got_it.want_level, got_it.budget)
check("状态变成展示中", after[0] == "displaying", after)
check("心愿信息保留（程度/预算还在）",
      after[1] == "hot" and after[2] == 100.0, after)
after_w = cw.get("/wishlist").get_data(as_text=True)
check("从心愿单消失",
      "很想要便宜" not in re.findall(r'class="s-name">([^<]+)<', after_w))
check("已入手后有提示", "已入手" in after_w)
check("它出现在谷柜里",
      "很想要便宜" in re.findall(r'data-name="([^"]+)"',
                              cw.get("/items").get_data(as_text=True)))

cw.post(f"/items/{hot_id}/acquire")
check("不在心愿单的再入手给提示",
      "不在心愿单里" in cw.get("/wishlist").get_data(as_text=True))
with app11.app_context():
    other11 = User(username="wishother")
    other11.set_password("abc123")
    db.session.add(other11)
    db.session.flush()
    oi11 = GuItem(user_id=other11.id, name="别人的心愿", count=1,
                  status="wishlist")
    db.session.add(oi11)
    db.session.commit()
    oi11_id = oi11.id
check("入手别人的心愿 404", cw.post(f"/items/{oi11_id}/acquire").status_code == 404)
with app11.app_context():
    check("别人的心愿没被动", db.session.get(GuItem, oi11_id).status == "wishlist")

# --- 编辑心愿字段 ---
with app11.app_context():
    eid11 = GuItem.query.filter_by(name="随缘的").first().id
cw.post(f"/items/{eid11}/edit", data={"name": "随缘的", "count": "1",
                                      "status": "wishlist", "want_level": "hot",
                                      "budget": "88.5", "want_link": "官网",
                                      "want_note": "改主意了"})
with app11.app_context():
    ed11 = db.session.get(GuItem, eid11)
    edited11 = (ed11.want_level, ed11.budget, ed11.want_link, ed11.want_note)
check("编辑能改心愿字段",
      edited11 == ("hot", 88.5, "官网", "改主意了"), edited11)

# --- 卡片数据 + API + 统计 ---
with app11.app_context():
    expect_budget = GuItem.query.filter_by(name="很想要贵").first().budget_text
wish_html = cw.get("/wishlist").get_data(as_text=True)
check("卡片带心愿数据",
      f'data-budget-text="{expect_budget}"' in wish_html
      and 'data-want-level-text="很想要"' in wish_html
      and "data-want-link-href=" in wish_html)
api11 = cw.get("/api/items").get_json()
row11 = [x for x in api11 if x["name"] == "很想要贵"][0]
check("api/items 带心愿字段",
      row11["want_level"] == "hot" and row11["want_level_text"] == "很想要"
      and row11["budget"] == 500.0 and row11["want_link"] == "https://example.com/a"
      and row11["want_note"] == "等再贩", row11)
stats11 = cw.get("/api/stats").get_json()
check("api/stats 带心愿单件数与总预算",
      stats11["wishlist"] == 7 and stats11["wishlist_budget"] == 888.5, stats11)

print("\n=== 18. 再贩提醒 ===")
app12 = fresh_app(db_uri("verify_rem.db"), WTF_CSRF_ENABLED=False)
cr = app12.test_client()
cr.post("/register", data={"username": "remuser", "password": "abc123"})

now_local = datetime.now()
fmt = "%Y-%m-%dT%H:%M"
soon = (now_local + timedelta(days=2)).replace(hour=10, minute=30, second=0, microsecond=0)
past = (now_local - timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)


def add_rem(client, **fields):
    return client.post("/reminders/add", data=fields)


add_rem(cr, title="再贩的", event_time=soon.strftime(fmt), priority="hot",
        subtitle="官方再贩", link="https://example.com/goods")
add_rem(cr, title="过期的", event_time=past.strftime(fmt), priority="mute")
add_rem(cr, title="随缘的", event_time=(now_local + timedelta(days=9)).strftime(fmt),
        priority="hack", link="闲鱼某某店")

with app12.app_context():
    rows = {r.title: r for r in Reminder.query.all()}
    got = {t: (r.event_time, r.priority, r.subtitle, r.link) for t, r in rows.items()}
    calc = {t: (r.tag_text, r.is_past, r.days_left, r.link_href, r.priority_text)
            for t, r in rows.items()}
check("三条提醒都入库", len(got) == 3, sorted(got))
check("事件时间按本地时间原样存", got["再贩的"][0] == soon, got["再贩的"][0])
check("优先级/说明/链接入库",
      got["再贩的"][1:] == ("hot", "官方再贩", "https://example.com/goods"),
      got["再贩的"])
check("非法优先级回退 normal", got["随缘的"][1] == "normal", got["随缘的"][1])
check("标签：2 天后", calc["再贩的"][0] == "2天后", calc["再贩的"][0])
check("标签：已过期 1 天", calc["过期的"][0] == "已过期 1 天", calc["过期的"][0])
check("过期的 is_past=True、未过期的 False",
      calc["过期的"][1] is True and calc["再贩的"][1] is False)
check("http(s) 才当链接，店铺名不当",
      calc["再贩的"][3] == "https://example.com/goods" and calc["随缘的"][3] == "",
      calc["随缘的"][3])
check("优先级文案", calc["再贩的"][4] == "重点" and calc["随缘的"][4] == "普通")

# 必填校验
before_n = Reminder.query.count() if False else None
add_rem(cr, title="  ", event_time=soon.strftime(fmt))
add_rem(cr, title="没填时间", event_time="")
add_rem(cr, title="时间格式不对", event_time="2026-13-99T99:99")
with app12.app_context():
    n_after = Reminder.query.count()
check("空标题/没时间/非法时间都不入库", n_after == 3, n_after)
html_r = cr.get("/reminders").get_data(as_text=True)
check("有对应提示", "请填写提醒内容" in html_r and "请选择提醒时间" in html_r)

# --- 专区 ---
check("/reminders 匿名访问跳登录",
      app12.test_client().get("/reminders").status_code == 302)
r = cr.get("/reminders")
html_rem = r.get_data(as_text=True)
titles = re.findall(r'<div class="r-body">\s*<p>([^<]+)</p>', html_rem)
check("GET /reminders 200", r.status_code == 200, r.status_code)
check("列出三条", len(titles) == 3, titles)
check("未过期的在前、过期的排最后",
      titles == ["再贩的", "随缘的", "过期的"], titles)
check("标题显示条数与过期数",
      "3 条" in html_rem and "1 条已过期" in html_rem,
      re.findall(r"\d+ 条[^<]*", html_rem))
check("过期那条带 is-past 样式", 'class="remind is-past"' in html_rem)
check("过期那条的小标签是「已过期 1 天」", "已过期 1 天" in html_rem)
check("完整时间显示出来了", soon.strftime("%m-%d %H:%M") in html_rem,
      soon.strftime("%m-%d %H:%M"))
check("说明显示出来了", "官方再贩" in html_rem)
check("链接渲染成可点的 <a>",
      '<a class="s-link" href="https://example.com/goods"' in html_rem)
check("店铺名只当纯文本", "闲鱼某某店" in html_rem
      and 'href="闲鱼某某店"' not in html_rem)
check("每行有编辑和知道了按钮",
      html_rem.count("data-reminder-edit=") == 3 and html_rem.count('class="r-act"') == 6,
      (html_rem.count("data-reminder-edit="), html_rem.count('class="r-act"')))
check("知道了表单带 next 和确认文案",
      html_rem.count('name="next"') >= 3 and "data-confirm=" in html_rem,
      html_rem.count('name="next"'))
check("专区带添加弹窗和 FAB",
      'id="reminderModal"' in html_rem and 'data-reminder-mode="add"' in html_rem)
check("弹窗里有时间/程度/说明/链接四个输入",
      all(f'name="{n}"' in html_rem for n in
          ("title", "event_time", "priority", "subtitle", "link")))

# --- 编辑 ---
with app12.app_context():
    rid = Reminder.query.filter_by(title="过期的").first().id
r = cr.post(f"/reminders/{rid}/edit",
            data={"title": "过期的（改了）", "event_time": (now_local + timedelta(days=3)).strftime(fmt),
                  "priority": "hot", "subtitle": "改过了", "link": "https://example.com/b",
                  "next": "/reminders"})
check("编辑 302 回跳", r.status_code == 302, r.status_code)
with app12.app_context():
    ed = db.session.get(Reminder, rid)
    edited = (ed.title, ed.priority, ed.subtitle, ed.link, ed.is_past)
check("编辑改到了全部字段",
      edited[:4] == ("过期的（改了）", "hot", "改过了", "https://example.com/b"),
      edited)
check("改完不再是过期状态", edited[4] is False)
check("改了之后排在未过期里",
      re.findall(r'<div class="r-body">\s*<p>([^<]+)</p>',
                 cr.get("/reminders").get_data(as_text=True))[-1] != "过期的（改了）")

# 越权
with app12.app_context():
    other12 = User(username="remother")
    other12.set_password("abc123")
    db.session.add(other12)
    db.session.flush()
    oi12 = Reminder(user_id=other12.id, title="别人的提醒",
                    event_time=now_local + timedelta(days=1))
    db.session.add(oi12)
    db.session.commit()
    oi12_id = oi12.id
check("编辑别人的提醒 404",
      cr.post(f"/reminders/{oi12_id}/edit",
              data={"title": "篡改", "event_time": fmt}).status_code == 404)
check("删别人的提醒 404",
      cr.post(f"/reminders/{oi12_id}/delete").status_code == 404)
with app12.app_context():
    check("别人的提醒没被动",
          db.session.get(Reminder, oi12_id).title == "别人的提醒")

# --- 知道了（删除） ---
with app12.app_context():
    keep_n = Reminder.query.filter_by(user_id=1).count()
r = cr.post(f"/reminders/{rid}/delete", data={"next": "/reminders"})
check("知道了 302 回跳", r.status_code == 302, r.status_code)
with app12.app_context():
    left = Reminder.query.filter_by(user_id=1).count()
check("知道了会删掉这条", left == keep_n - 1, (keep_n, left))
check("删除后有提示", "已删除提醒" in cr.get("/reminders").get_data(as_text=True))
check("它不再出现在清单里",
      "过期的（改了）" not in re.findall(
          r'<div class="r-body">\s*<p>([^<]+)</p>',
          cr.get("/reminders").get_data(as_text=True)))

# --- 仪表盘板块 ---
dash12 = cr.get("/").get_data(as_text=True)
dash_titles = re.findall(r'<div class="r-body">\s*<p>([^<]+)</p>', dash12)
check("仪表盘有再贩提醒板块与入口",
      'id="reminderLink"' in dash12 and 'href="/reminders"' in dash12)
check("仪表盘列出提醒（最多 3 条）", len(dash_titles) == 2, dash_titles)
check("仪表盘提醒行也有编辑按钮",
      'data-reminder-edit=' in dash12 and 'data-modal-open="reminderModal"' in dash12)

# 地区现在是「国家→省→市→区」四级，下面几组常用组合供测试直接用
SHANGHAI_XUHUI = {"country": "中国", "province": "上海市",
                  "city": "市辖区", "area": "徐汇区"}
BEIJING_CHAOYANG = {"country": "中国", "province": "北京市",
                    "city": "市辖区", "area": "朝阳区"}
GUANGZHOU_TIANHE = {"country": "中国", "province": "广东省",
                    "city": "广州市", "area": "天河区"}
SHENZHEN_NANSHAN = {"country": "中国", "province": "广东省",
                    "city": "深圳市", "area": "南山区"}
SHENZHEN_FUTIAN = {"country": "中国", "province": "广东省",
                   "city": "深圳市", "area": "福田区"}

print("\n=== 19. 同城换谷（开关打开时完整跑一遍） ===")
# 换谷默认是关的（routes.DEFAULT_EXCHANGE_ENABLED=False）：
# 这里显式打开，保证功能本身没坏、开关一开就能用；关闭状态的断言在下一节。
app13 = fresh_app(db_uri("verify_ex.db"), WTF_CSRF_ENABLED=False,
                  EXCHANGE_ENABLED=True)
cx = app13.test_client()
cx.post("/register", data={"username": "exuser", "password": "abc123"})
cx2 = app13.test_client()
cx2.post("/register", data={"username": "exmate", "password": "abc123"})

# 一块空板：先看空状态能不能直接开发布表单（等下有数据就看不到空状态了）
ex_empty = cx.get("/exchanges").get_data(as_text=True)
check("换谷页空状态能直接开发布表单",
      'data-modal-open="exchangeModal"' in ex_empty
      and "发布一条换谷信息" in ex_empty)


def add_ex(client, **fields):
    return client.post("/exchanges/add", data=fields)


# 我（上海 徐汇）发一条
cx.post("/profile/region", data=SHANGHAI_XUHUI)
add_ex(cx, title="求换 星野吧唧", want="白兔立牌", emoji="🐰", distance_km="1.5",
       place="地铁 2 号线站内", contact="微信 me_1", note="只换原画")
# 谷友（同地区，0.5km）发一条
cx2.post("/profile/region", data=SHANGHAI_XUHUI)
add_ex(cx2, title="求换 抹茶吧唧", want="樱花立牌", emoji="🍵", distance_km="0.5",
       place="小区门口", contact="微信 mate_1")
# 谷友改成北京，再发一条（这条就不是同城了）
cx2.post("/profile/region", data=BEIJING_CHAOYANG)
add_ex(cx2, title="求换 星野立牌", want="蝴蝶挂饰", distance_km="9")

with app13.app_context():
    rows = {e.title: e for e in Exchange.query.all()}
    got = {t: (e.emoji, e.distance_km, e.region, e.place, e.contact, e.note)
           for t, e in rows.items()}
check("三条换谷信息都入库", len(got) == 3, sorted(got))
check("距离/面交地点/联系方式/说明都入库",
      got["求换 星野吧唧"][:2] == ("🐰", 1.5)
      and got["求换 星野吧唧"][3:] == ("地铁 2 号线站内", "微信 me_1", "只换原画"),
      got["求换 星野吧唧"])
check("发布时地区跟着发布者走",
      got["求换 星野吧唧"][2] == "上海 徐汇"
      and got["求换 星野立牌"][2] == "北京 朝阳",
      (got["求换 星野吧唧"][2], got["求换 星野立牌"][2]))
check("没填距离时退回默认 1", got["求换 抹茶吧唧"][1] == 0.5, got["求换 抹茶吧唧"][1])

# 之后改地区，旧信息不受影响
cx.post("/profile/region", data=GUANGZHOU_TIANHE)
with app13.app_context():
    old = Exchange.query.filter_by(title="求换 星野吧唧").first()
    old_region = old.region
check("改自己的地区不会动已发布的信息", old_region == "上海 徐汇", old_region)
cx.post("/profile/region", data=SHANGHAI_XUHUI)   # 改回来继续测

# 必填与非法输入
add_ex(cx, title="   ", distance_km="1")
add_ex(cx, title="脏距离", distance_km="abc")
with app13.app_context():
    check("空标题不入库", Exchange.query.count() == 4, Exchange.query.count())
    dirty = Exchange.query.filter_by(title="脏距离").first()
    check("非法距离退回默认 1", dirty.distance_km == 1.0, dirty.distance_km)
check("空标题有提示", "请填写想换什么" in cx.get("/exchanges").get_data(as_text=True))

# --- 换谷板：谷友互看 ---
check("/exchanges 匿名访问跳登录",
      app13.test_client().get("/exchanges").status_code == 302)
r = cx.get("/exchanges")
html_ex = r.get_data(as_text=True)
names_ex = re.findall(r'class="s-name">([^<]+)<', html_ex)
check("GET /exchanges 200", r.status_code == 200, r.status_code)
check("能看到全部 4 条（含谷友发的）", len(names_ex) == 4, names_ex)
check("排序：我发的 → 同地区 → 其它（同级里距离近的在前）",
      names_ex == ["脏距离", "求换 星野吧唧", "求换 抹茶吧唧", "求换 星野立牌"], names_ex)
check("我发的标「我发的」", "我发的" in html_ex)
check("同地区的标「同城」", "同城" in html_ex)
check("不同地区显示地区名", "北京 朝阳" in html_ex)
check("只有我发的才有编辑/撤下按钮",
      html_ex.count("data-exchange-edit=") == 2
      and html_ex.count('data-confirm="撤下') == 2,
      (html_ex.count("data-exchange-edit="), html_ex.count('data-confirm="撤下')))
check("谷友的信息没有撤下按钮",
      html_ex.count('data-confirm="撤下「求换 抹茶吧唧」？"') == 0)
check("每行显示可换/距离/地区",
      "可换 白兔立牌 · 约 1.5km · 上海 徐汇" in html_ex,
      re.findall(r'class="s-src">([^<]*)<', html_ex))
check("每行显示面交地点和联系方式",
      "地铁 2 号线站内 · 微信 me_1 · 只换原画" in html_ex,
      re.findall(r'class="s-meta">([^<]*)<', html_ex))
check("标题显示条数与同城数（只有抹茶那条和我同地区）",
      "4 条信息" in html_ex and "1 条同城" in html_ex,
      re.findall(r"\d+ 条信息[^<]*", html_ex))
check("地区栏显示当前地区", 'value="上海 徐汇"' in html_ex)
check("有发布弹窗和 FAB",
      'id="exchangeModal"' in html_ex and 'data-exchange-mode="add"' in html_ex)
check("弹窗里有全部字段",
      all(f'name="{n}"' in html_ex for n in
          ("title", "want", "emoji", "distance_km", "place", "contact", "note")))

# 换个用户看：他的视角不同（他自己没有信息，且同城判定按他的地区）
r2 = cx2.get("/exchanges")
html_ex2 = r2.get_data(as_text=True)
names_ex2 = re.findall(r'class="s-name">([^<]+)<', html_ex2)
check("谷友看到的排序也不同（他发的最前，其次是他同地区的）",
      names_ex2 == ["求换 星野立牌", "求换 抹茶吧唧", "脏距离", "求换 星野吧唧"],
      names_ex2)
check("谷友看到我发的会带地区名（他改成了北京）",
      "北京 朝阳" in html_ex2)

# --- 同城按「省 + 市」算：同一个市的不同区也要算同城 ---
cx.post("/profile/region", data=dict(SHENZHEN_NANSHAN, next="/exchanges"))
cx2.post("/profile/region", data=dict(SHENZHEN_FUTIAN, next="/exchanges"))
add_ex(cx2, title="求换 深圳跨区测试", want="色纸", distance_km="3")
board_now = cx.get("/exchanges").get_data(as_text=True)
row_chunks = board_now.split('<div class="side-item')
cross = [c for c in row_chunks if "求换 深圳跨区测试" in c]
check("不同区但同一个市，列表里标「同城」",
      bool(cross) and "同城" in cross[0], cross[0][:200] if cross else "没找到那行")
check("跨区的被算进「N 条同城」（不是被判成异地）",
      re.search(r"(\d+) 条同城", board_now) is not None
      and int(re.search(r"(\d+) 条同城", board_now).group(1)) >= 1,
      re.search(r"(\d+) 条同城", board_now))
# 恢复成原来的地区，免得影响后面的用例
cx.post("/profile/region", data=dict(SHANGHAI_XUHUI, next="/exchanges"))
cx2.post("/profile/region", data=dict(BEIJING_CHAOYANG, next="/exchanges"))

# --- 编辑 / 撤下 ---
with app13.app_context():
    mine_id = Exchange.query.filter_by(title="求换 星野吧唧").first().id
    mate_id = Exchange.query.filter_by(title="求换 抹茶吧唧").first().id
r = cx.post(f"/exchanges/{mine_id}/edit",
            data={"title": "求换 星野吧唧（改）", "want": "樱花立牌",
                  "distance_km": "2", "place": "改过的地点", "next": "/exchanges"})
check("编辑自己的信息 302", r.status_code == 302, r.status_code)
with app13.app_context():
    ed = db.session.get(Exchange, mine_id)
    edited = (ed.title, ed.want, ed.distance_km, ed.place)
check("编辑改到了字段",
      edited == ("求换 星野吧唧（改）", "樱花立牌", 2.0, "改过的地点"), edited)
check("编辑别人的信息 404",
      cx.post(f"/exchanges/{mate_id}/edit",
              data={"title": "篡改"}).status_code == 404)
check("撤下别人的信息 404",
      cx.post(f"/exchanges/{mate_id}/delete").status_code == 404)
with app13.app_context():
    check("别人的信息没被动",
          db.session.get(Exchange, mate_id).title == "求换 抹茶吧唧")
r = cx.post(f"/exchanges/{mine_id}/delete", data={"next": "/exchanges"})
check("撤下自己的信息 302", r.status_code == 302, r.status_code)
with app13.app_context():
    check("撤下后真的没了", db.session.get(Exchange, mine_id) is None)
check("撤下有提示", "已撤下换谷信息" in cx.get("/exchanges").get_data(as_text=True))

# --- 仪表盘板块 ---
dash13 = cx.get("/").get_data(as_text=True)
check("仪表盘有换谷板块与入口",
      'id="exchangeLink"' in dash13 and 'href="/exchanges"' in dash13)
check("仪表盘卡片显示求换内容", "求换 抹茶吧唧" in dash13)
check("仪表盘最多列 4 条",
      dash13.count('class="ex-card"') <= 4, dash13.count('class="ex-card"'))
check("仪表盘的加号打开「要添加什么」面板（而不是直接建谷子）",
      'data-modal-open="addSheet"' in dash13
      and 'data-modal-open="addModal"' not in dash13)
check("仪表盘也能直接发布换谷信息（面板里有入口）",
      'data-modal-open="exchangeModal"' in dash13)

# 关掉开关前，先把这一份数据整个拍下来，等下和关掉之后逐字段比对
with app13.app_context():
    exchange_snapshot = sorted(
        (e.id, e.user_id, e.emoji, e.title, e.want, e.distance_km, e.region,
         e.place, e.contact, e.note) for e in Exchange.query.all())

print("\n=== 20. 统一的「+」：自己选添加什么 ===")
app14 = fresh_app(db_uri("verify_sheet.db"), WTF_CSRF_ENABLED=False)
sx = app14.test_client()
sx.post("/register", data={"username": "sheetuser", "password": "abc123"})

PAGES = ["/", "/items", "/transit", "/sold", "/wishlist", "/reminders"]
page_html = {p: sx.get(p).get_data(as_text=True) for p in PAGES}

# 每个页面都是一个 + 号 → 先问「要添加什么」，而不是各自绑死一张表单
check("所有页面都有统一的添加入口",
      all('data-modal-open="addSheet"' in h for h in page_html.values()),
      [p for p, h in page_html.items() if 'data-modal-open="addSheet"' not in h])
check("所有页面都只有一个 + 号（不会多出第二个悬浮按钮）",
      all(h.count('class="fab"') == 1 for h in page_html.values()),
      {p: h.count('class="fab"') for p, h in page_html.items()})
check("+ 号不再直接打开「添加谷子」",
      all('data-modal-open="addModal"' not in h for h in page_html.values()),
      [p for p, h in page_html.items() if 'data-modal-open="addModal"' in h])

# 面板里五种都能选：前四种共用谷子表单（预设状态），提醒走自己的弹窗
sheet = page_html["/"]
for kind, label in [("displaying", "谷子"), ("in_transit", "在途"), ("sold", "已出"),
                    ("wishlist", "心愿单")]:
    check(f"面板里有「{label}」选项",
          f'data-add-kind="{kind}"' in sheet and label in sheet)
check("面板里有「再贩提醒」选项",
      'data-modal-open="reminderModal"' in sheet
      and 'data-reminder-mode="add"' in sheet)
# 面板里也要能直接进分类管理（只有面板那一块，别把页面上别的入口算进来）
sheet_only = sheet.split('id="addSheet"')[1].split('id="addModal"')[0]
check("面板里也有分类管理入口",
      'data-modal-open="catModal"' in sheet_only and "分类管理" in sheet_only)
check("分类管理和记录类型之间有分隔线",
      'class="sheet-sep"' in sheet_only)

# 谷子表单必须能表达这四种状态，否则预设就是空的
check("谷子表单的状态里有谷柜/在途/已出/心愿单四种",
      all(f'<option value="{v}">' in sheet
          for v in ("displaying", "in_transit", "sold", "wishlist")))

# 弹窗只在 _add_sheet.html 里 include 一次：id 重复会让 getElementById 抓错那个
for mid in ("addSheet", "addModal", "reminderModal", "catModal"):
    dup = {p: h.count(f'id="{mid}"') for p, h in page_html.items()
           if h.count(f'id="{mid}"') != 1}
    check(f"每页有且只有一个 {mid}", not dup, dup)

# --- 同城换谷已关掉：入口和路由都不该出现 ---
check("换谷关闭时不渲染换谷弹窗",
      all('id="exchangeModal"' not in h for h in page_html.values()),
      [p for p, h in page_html.items() if 'id="exchangeModal"' in h])
check("换谷关闭时面板里没有换谷选项",
      all('data-exchange-mode="add"' not in h for h in page_html.values()),
      [p for p, h in page_html.items() if 'data-exchange-mode="add"' in h])
check("换谷关闭时首页没有换谷板块",
      "同城换谷" not in page_html["/"] and 'id="exchangeLink"' not in page_html["/"],
      "同城换谷" in page_html["/"])
check("换谷关闭时 /exchanges 直接 404", sx.get("/exchanges").status_code == 404,
      sx.get("/exchanges").status_code)
check("换谷关闭时 /exchanges/add 也 404",
      sx.post("/exchanges/add", data={"title": "x"}).status_code == 404)
check("换谷关闭时 /exchanges/1/edit 也 404",
      sx.post("/exchanges/1/edit", data={"title": "x"}).status_code == 404)
status_now = {p: sx.get(p).status_code for p in PAGES}
check("别的页面都还正常（关掉换谷没连累其它功能）",
      all(code == 200 for code in status_now.values()), status_now)

# 选项改成 context processor 注入后，没显式传参的页面也要有下拉内容
check("在途页的添加表单也有下拉选项",
      'data-cat-select="type"' in page_html["/transit"]
      and 'data-cat-select="ip"' in page_html["/transit"]
      and 'id="remPriority"' in page_html["/transit"])
check("分类管理弹窗在专区页也能用",
      'id="catModal"' in page_html["/transit"]
      and 'data-modal-open="catModal"' in page_html["/transit"])

# 新加的分类要立刻出现在任意页面的下拉里
sx.post("/categories/add", data={"kind": "type", "name": "新品类"})
check("新分类在任何页面的下拉里都能选到",
      "新品类" in sx.get("/transit").get_data(as_text=True))

# --- 分类管理入口要显眼：以前是灰色小字，基本没人注意到 ---
items15 = sx.get("/items").get_data(as_text=True)
dash15 = sx.get("/").get_data(as_text=True)
check("谷柜页的分类管理是按钮样式（不是灰色小字）",
      'class="cat-manage"' in items15 and 'class="seg-more"' not in items15)
check("入口带图标和文字", "<svg" in items15 and "分类管理" in items15)
badge = re.search(r'class="cat-manage".*?<b>(\d+)</b>', items15, re.S)
check("入口的数量角标 = 已有的分类数（刚加了 1 个）",
      badge is not None and badge.group(1) == "1",
      badge.group(1) if badge else "没找到角标")
check("首页不再有分类管理入口（改用 + 号面板）",
      'class="cat-manage"' not in dash15 and 'class="sec-manage"' not in dash15)
check("谷柜页的切换条里还留着分类管理入口",
      'class="cat-manage"' in sx.get("/items").get_data(as_text=True))
check("添加表单里的分类管理也做成了小胶囊",
      'class="cat-link"' in items15)

# 从专区加谷子要回到专区，而不是被丢回首页
check("添加表单带 next（回到原页面）",
      'name="next" value="/transit' in page_html["/transit"])
r = sx.post("/items/add", data={"name": "从在途页加的", "count": "1",
                                "status": "in_transit", "next": "/transit"})
check("从在途页添加后回到在途页",
      r.status_code == 302 and r.headers.get("Location", "").startswith("/transit"),
      f"{r.status_code} {r.headers.get('Location')}")

# 空状态的页面要能一键加对应类型（不用先跑去别的页面改状态）
check("在途页空状态有「记一件在途」按钮",
      'data-add-kind="in_transit"' in page_html["/transit"]
      and "记一件在途的谷子" in page_html["/transit"])
check("心愿单空状态有「记一件想要的」按钮",
      'data-add-kind="wishlist"' in page_html["/wishlist"]
      and "记一件想要的" in page_html["/wishlist"])
check("已出页空状态有「记一件已出」按钮",
      'data-add-kind="sold"' in page_html["/sold"]
      and "记一件已出掉的谷子" in page_html["/sold"])
check("提醒页空状态能直接开提醒表单",
      'data-modal-open="reminderModal"' in page_html["/reminders"]
      and "记一个再贩提醒" in page_html["/reminders"])

check("登录页不需要这些选项也不会报错", sx.get("/login").status_code in (200, 302))
anon2 = app14.test_client()
check("未登录也能正常渲染登录页（选项给空值）", anon2.get("/login").status_code == 200)

print("\n=== 21. 换谷关掉之后：数据还在，随时能开回来 ===")
# 注意用 create_app 而不是 fresh_app：fresh_app 会 drop_all，那就把数据删了
app15 = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False,
                    "SQLALCHEMY_DATABASE_URI": db_uri("verify_ex.db")})
with app15.app_context():
    rows_left = Exchange.query.count()
    rows_now = sorted(
        (e.id, e.user_id, e.emoji, e.title, e.want, e.distance_km, e.region,
         e.place, e.contact, e.note) for e in Exchange.query.all())
check("关掉开关后库里的换谷信息还在（不是删数据，只是不显示）",
      rows_left > 0, rows_left)
check("每条换谷信息的字段都和关掉之前一模一样",
      rows_now == exchange_snapshot,
      [(a, b) for a, b in zip(rows_now, exchange_snapshot) if a != b][:1])

c15 = app15.test_client()
r = c15.post("/login", data={"username": "exuser", "password": "abc123"})
check("关闭状态下也能正常登录", r.status_code == 302, r.status_code)
check("关闭状态下 /exchanges 404（书签点进来也是 404）",
      c15.get("/exchanges").status_code == 404)
dash15b = c15.get("/").get_data(as_text=True)
check("关闭状态下首页看不到换谷板块", "同城换谷" not in dash15b)
check("关闭状态下 + 面板里也没有换谷",
      'data-exchange-mode="add"' not in dash15b
      and 'id="exchangeModal"' not in dash15b)
check("关掉换谷后首页其它板块照常",
      all(k in dash15b for k in ("谷柜", "在途", "心愿单", "再贩提醒")),
      [k for k in ("谷柜", "在途", "心愿单", "再贩提醒") if k not in dash15b])

# 打开开关（就地改 config）→ 同一份数据立刻又出现了，说明是纯开关、没破坏数据
app15.config["EXCHANGE_ENABLED"] = True
c15b = app15.test_client()
c15b.post("/login", data={"username": "exuser", "password": "abc123"})
dash15c = c15b.get("/").get_data(as_text=True)
check("开关一开，换谷板块和数据立刻回来",
      "同城换谷" in dash15c and "求换 星野吧唧" in dash15c)
check("开关一开，/exchanges 又是 200",
      c15b.get("/exchanges").status_code == 200)

print("\n=== 22. 返回首页按钮做明显 + 统计块可点 ===")
app16 = fresh_app(db_uri("verify_back.db"), WTF_CSRF_ENABLED=False)
bx = app16.test_client()
bx.post("/register", data={"username": "backuser", "password": "abc123"})
bx.post("/items/add", data={"name": "展示的", "count": "2", "status": "displaying"})
bx.post("/items/add", data={"name": "在途的", "count": "1", "status": "in_transit"})

back_pages = {p: bx.get(p).get_data(as_text=True)
              for p in ("/items", "/transit", "/sold", "/wishlist", "/reminders")}
check("每个专区页顶栏都有「返回首页」字样的按钮",
      all('class="back-btn"' in h and "<span>返回首页</span>" in h
          for h in back_pages.values()),
      [p for p, h in back_pages.items() if 'class="back-btn"' not in h])
check("顶栏按钮是真链接（回首页）",
      all('class="back-btn" href="/"' in h for h in back_pages.values()),
      [p for p, h in back_pages.items() if 'class="back-btn" href="/"' not in h])
check("不再用那个只有小箭头的 icon-btn 当返回",
      all('class="icon-btn" href="/"' not in h for h in back_pages.values()),
      [p for p, h in back_pages.items() if 'class="icon-btn" href="/"' in h])
# 一个页面只留一个返回首页（顶栏那个，它是 sticky 的，滚到哪儿都在）
check("每页只有一个返回首页入口（页脚那个已去掉）",
      all(h.count('aria-label="返回首页"') == 1 and 'class="back-link"' not in h
          for h in back_pages.values()),
      {p: h.count('aria-label="返回首页"') for p, h in back_pages.items()})
check("页脚那一段整体删掉了（不留空壳）",
      all('class="back-link"' not in h for h in back_pages.values()))

dash16 = bx.get("/").get_data(as_text=True)
for sid, href, label in [("statDisplaying", "/items", "展示中"),
                         ("statInTransit", "/transit", "在途"),
                         ("statSold", "/sold", "已出"),
                         ("statWishlist", "/wishlist", "心愿单")]:
    check(f"统计块「{label}」是通往 {href} 的链接",
          f'id="{sid}" href="{href}"' in dash16,
          [l for l in dash16.splitlines() if sid in l][:1])
check("统计块数字仍然正确（展示中 1 / 在途 1 / 已出 0 / 心愿单 0）",
      "<b>1</b><span>展示中</span>" in dash16
      and "<b>1</b><span>在途</span>" in dash16
      and "<b>0</b><span>已出</span>" in dash16
      and "<b>0</b><span>心愿单</span>" in dash16)
check("统计块点进去的页面都打得开",
      all(bx.get(href).status_code == 200
          for href in ("/items", "/transit", "/sold", "/wishlist")))

print("\n=== 23. 数据备份：导出 / 导入 / 自动备份 ===")


def _zip_of(files):
    """把 {名字: 内容} 打成一个内存 zip，用来造各种坏备份包。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


BK_DIR = os.path.join(TEST_DB_DIR, "sect23-backups")
app17 = fresh_app(db_uri("verify_backup.db"), WTF_CSRF_ENABLED=False,
                  BACKUP_FOLDER=BK_DIR)
bx = app17.test_client()                      # 主账号（备份下载权限归它）
bx.post("/register", data={"username": "bk_a", "password": "abc123"})
cx17 = app17.test_client()                    # 第二个账号，用来验越权
cx17.post("/register", data={"username": "bk_b", "password": "abc123"})

bx.post("/categories/add", data={"kind": "type", "name": "吧唧"})
bx.post("/categories/add", data={"kind": "ip", "name": "某某"})
with app17.app_context():
    cat_t = Category.query.filter_by(kind="type", name="吧唧").first().id
    cat_i = Category.query.filter_by(kind="ip", name="某某").first().id

upload(bx, name="带照片的色纸", emoji="⭐", count="3", status="displaying",
       category_type_id=str(cat_t), category_ip_id=str(cat_i),
       image=(make_image("PNG"), "a.png"))
bx.post("/items/add", data={"name": "在途的立牌", "count": "1", "status": "in_transit",
                            "expect_date": "2026-12-31", "channel": "xianyu",
                            "order_no": "SF123", "price": "268"})
bx.post("/items/add", data={"name": "出掉的吧唧", "count": "1", "status": "sold",
                            "sold_price": "168", "sold_method": "sell",
                            "sold_channel": "xianyu", "sold_at": "2026-09-01",
                            "price": "100"})
bx.post("/items/add", data={"name": "想要的挂件", "count": "1", "status": "wishlist",
                            "want_level": "hot", "budget": "300",
                            "want_link": "https://example.com/x", "want_note": "只收原画"})
bx.post("/reminders/add", data={"title": "星野再贩", "event_time": "2026-12-31T20:00",
                                "priority": "high", "subtitle": "官方再贩",
                                "link": "https://example.com/r"})
# 换谷功能关着，直接写库造一条，验「关掉的功能数据也能被备份带走」
with app17.app_context():
    me17 = User.query.filter_by(username="bk_a").first()
    db.session.add(Exchange(user_id=me17.id, title="换 星野吧唧", want="白兔立牌",
                            distance_km=1.5, region="上海 徐汇", place="地铁站",
                            contact="微信 x"))
    db.session.commit()
# 别人的数据：绝不能出现在我的导出里
cx17.post("/items/add", data={"name": "B的谷子", "count": "1", "status": "displaying"})

# 给主账号设好「个人资料」，用来验导出/导入有没有把它们带上
bx.post("/profile/region", data=dict(SHENZHEN_NANSHAN, next="/backup"))
bx.post("/profile/signature", data={"signature": "导出用签名-XYZ", "next": "/backup"})
bx.post("/profile/avatar",
        data={"avatar": (make_image("PNG"), "me.png"), "next": "/backup"},
        content_type="multipart/form-data")
with app17.app_context():
    me17b = User.query.filter_by(username="bk_a").first()
    export_avatar = me17b.avatar
check("测试前：主账号有地区 / 签名 / 头像",
      bool(export_avatar) and me17b.signature == "导出用签名-XYZ"
      and me17b.region == "广东 深圳 南山",
      (me17b.region, me17b.signature, export_avatar))

check("匿名进不了备份页", app17.test_client().get("/backup").status_code == 302)
check("匿名也导不出数据", app17.test_client().get("/backup/export").status_code == 302)
bk_page = bx.get("/backup").get_data(as_text=True)
check("备份页能打开", "数据备份" in bk_page)
check("备份页有导出按钮", 'href="/backup/export"' in bk_page)
check("备份页有导入表单（multipart）",
      'action="/backup/import"' in bk_page and "multipart/form-data" in bk_page)
check("备份页有立即备份按钮", 'action="/backup/now"' in bk_page)
check("备份目录听配置的（没写进真实 instance/）",
      backup.backup_dir(app17) == BK_DIR, backup.backup_dir(app17))

# --- 导出 ---
r = bx.get("/backup/export")
check("导出返回 zip",
      r.status_code == 200 and r.mimetype == "application/zip",
      f"{r.status_code} {r.mimetype}")
check("下载头带了文件名", "attachment" in r.headers.get("Content-Disposition", ""),
      r.headers.get("Content-Disposition"))
zf = zipfile.ZipFile(io.BytesIO(r.data))
zip_names = zf.namelist()
raw_json = zf.read("data.json").decode("utf-8")
data = json.loads(raw_json)
check("压缩包里有 data.json", "data.json" in zip_names)
check("标记了来源和格式版本", data.get("app") == "谷仓" and data.get("format") == 1)
check("四件谷子都导出了",
      {i["name"] for i in data["items"]}
      == {"带照片的色纸", "在途的立牌", "出掉的吧唧", "想要的挂件"},
      [i["name"] for i in data["items"]])
check("分类以名字导出（导入时好对齐）",
      any(i["category_type"] == "吧唧" and i["category_ip"] == "某某"
          for i in data["items"]))
check("在途字段导出齐全",
      any(i["channel"] == "xianyu" and i["order_no"] == "SF123"
          and i["price"] == 268 and i["expect_date"] == "2026-12-31"
          for i in data["items"]))
check("已出字段导出齐全",
      any(i["sold_price"] == 168 and i["sold_method"] == "sell"
          and i["sold_at"] == "2026-09-01" for i in data["items"]))
check("心愿字段导出齐全",
      any(i["want_level"] == "hot" and i["budget"] == 300
          and i["want_note"] == "只收原画" for i in data["items"]))
check("提醒也导出了",
      len(data["reminders"]) == 1 and data["reminders"][0]["title"] == "星野再贩",
      data["reminders"])
check("换谷信息也一并导出（虽然功能关着）",
      len(data["exchanges"]) == 1 and data["exchanges"][0]["want"] == "白兔立牌",
      data["exchanges"])
photo_item = [i for i in data["items"] if i["name"] == "带照片的色纸"][0]
check("照片文件跟着打包", f"photos/{photo_item['image']}" in zip_names, zip_names)
check("导出文件里不含任何密码散列",
      not any(k in raw_json for k in ("pbkdf2", "scrypt", "password_hash", "set_password")))
check("别人的谷子没被导出", "B的谷子" not in raw_json)
check("个人资料也导出了（地区 / 签名 / 头像）",
      data.get("region") == "广东 深圳 南山"
      and data.get("signature") == "导出用签名-XYZ"
      and data.get("avatar") == export_avatar,
      (data.get("region"), data.get("signature"), data.get("avatar")))
check("头像文件也打包了（放在 avatar/ 下）",
      f"avatar/{export_avatar}" in zip_names,
      [n for n in zip_names if n.startswith("avatar/")])
check("别人的照片也没被导出",
      all(n.startswith("photos/") and photo_item["image"] in n for n in zip_names
          if n.startswith("photos/")), zip_names)

# --- 导入到另一个账号 ---
dx = app17.test_client()
dx.post("/register", data={"username": "bk_c", "password": "abc123"})
dx.post("/items/add", data={"name": "C原有的", "count": "1", "status": "displaying"})
dx.post("/categories/add", data={"kind": "type", "name": "吧唧"})   # 与备份重名
zip_bytes = r.data


def do_import(client, payload, filename="backup.zip"):
    return client.post("/backup/import",
                       data={"file": (io.BytesIO(payload), filename)},
                       content_type="multipart/form-data")


r = do_import(dx, zip_bytes)
check("导入 302", r.status_code == 302, r.status_code)
import_page = dx.get("/backup").get_data(as_text=True)
check("导入后有汇总提示", "导入完成" in import_page)
check("提示里写明了个人资料也恢复了",
      "个人资料恢复了" in import_page and "头像" in import_page,
      [line.strip() for line in import_page.splitlines() if "导入完成" in line][:1])
# 属性（尤其关系属性）必须在 app context 里读出来，出了上下文对象就 detached 了
with app17.app_context():
    u_c = User.query.filter_by(username="bk_c").first()
    got = {i.name: i for i in u_c.items}
    cats_c = [c.name for c in u_c.categories]
    rem_c = [r.title for r in u_c.reminders]
    ex_c = [e.title for e in u_c.exchanges]

    def info(item_name):
        it = got.get(item_name)
        if it is None:
            return None
        return {
            "cat_type": it.category_name("type"), "cat_ip": it.category_name("ip"),
            "count": it.count, "status": it.status, "image": it.image,
            "channel": it.channel, "order_no": it.order_no, "price": it.price,
            "has_expect": it.expect_date is not None,
            "sold_price": it.sold_price, "sold_method": it.sold_method,
            "has_sold_at": it.sold_at is not None, "profit": it.profit,
            "want_level": it.want_level, "budget": it.budget,
            "want_note": it.want_note,
        }

    a_info, h_info = info("带照片的色纸"), info("在途的立牌")
    s_info, w_info = info("出掉的吧唧"), info("想要的挂件")
    c17 = User.query.filter_by(username="bk_c").first()
    c_profile = (c17.region, c17.signature, c17.avatar)

check("原有谷子一件没少（导入只增不删）", "C原有的" in got, sorted(got))
check("四件都导进来了",
      {"带照片的色纸", "在途的立牌", "出掉的吧唧", "想要的挂件"} <= set(got),
      sorted(got))
check("重名分类没有重复建", cats_c.count("吧唧") == 1, cats_c)
check("备份里没有的分类会新建（作品IP 某某）", "某某" in cats_c, cats_c)
check("分类挂回了对应的谷子",
      a_info and (a_info["cat_type"], a_info["cat_ip"]) == ("吧唧", "某某"),
      a_info and (a_info["cat_type"], a_info["cat_ip"]))
check("件数和状态还原",
      a_info and a_info["count"] == 3 and a_info["status"] == "displaying",
      a_info and (a_info["count"], a_info["status"]))
check("配图也还原了，文件真的写进磁盘",
      a_info and a_info["image"]
      and os.path.exists(os.path.join(up_dir, a_info["image"])),
      a_info and a_info["image"])
check("还原的图是有效图片",
      a_info and a_info["image"]
      and PILImage.open(os.path.join(up_dir, a_info["image"])).format == "PNG")
check("在途字段还原",
      h_info and h_info["status"] == "in_transit" and h_info["channel"] == "xianyu"
      and h_info["order_no"] == "SF123" and h_info["price"] == 268
      and h_info["has_expect"], h_info)
check("已出字段还原",
      s_info and s_info["sold_price"] == 168 and s_info["sold_method"] == "sell"
      and s_info["has_sold_at"] and s_info["profit"] is not None, s_info)
check("心愿字段还原",
      w_info and w_info["want_level"] == "hot" and w_info["budget"] == 300
      and w_info["want_note"] == "只收原画", w_info)
check("提醒也还原了", rem_c == ["星野再贩"], rem_c)
check("换谷信息也还原了（功能关着数据照样在）", ex_c == ["换 星野吧唧"], ex_c)
check("个人资料也恢复了：地区", c_profile[0] == "广东 深圳 南山", c_profile)
check("个人资料也恢复了：签名", c_profile[1] == "导出用签名-XYZ", c_profile)
check("个人资料也恢复了：头像文件真的落盘",
      bool(c_profile[2]) and os.path.exists(os.path.join(up_dir, c_profile[2])),
      c_profile[2])
check("恢复的头像和导出的不是同一个文件名（重新存了一份，不会互相覆盖）",
      c_profile[2] and c_profile[2] != export_avatar, (c_profile[2], export_avatar))
check("导入没有动到别人的数据",
      cx17.get("/api/items").get_json() and
      {i["name"] for i in cx17.get("/api/items").get_json()} == {"B的谷子"})

# 追加语义：再导一次就会重样（件数 = 原有 + 备份里的件数，而不是被覆盖）
before_n = len(got)
backup_items = len(data["items"])
do_import(dx, zip_bytes)
with app17.app_context():
    after_n = User.query.filter_by(username="bk_c").first().items.count()
check("再导入一次是追加、不是覆盖（件数 = 原有 + 备份 4 件）",
      after_n == before_n + backup_items, (before_n, after_n, backup_items))

# 已经有资料的账号再导一次：个人资料不能被覆盖（导入的语义是只追加）
with app17.app_context():
    a_before = (User.query.filter_by(username="bk_a").first().signature,
                User.query.filter_by(username="bk_a").first().avatar)
do_import(bx, zip_bytes)
with app17.app_context():
    a_after = (User.query.filter_by(username="bk_a").first().signature,
               User.query.filter_by(username="bk_a").first().avatar)
check("导入到已有资料的账号，不覆盖它现有的签名/头像",
      a_before == a_after == ("导出用签名-XYZ", export_avatar),
      (a_before, a_after))

# --- 坏文件都要被友好拒绝，且不许动数据 ---
def import_and_read(client, payload, filename="bad.zip"):
    do_import(client, payload, filename)
    return client.get("/backup").get_data(as_text=True)


def count_c():
    with app17.app_context():
        return User.query.filter_by(username="bk_c").first().items.count()


n_before = count_c()
check("不是 zip 的文件被拒绝", "不是备份包" in import_and_read(dx, b"this is not a zip"))
check("缺 data.json 被拒绝",
      "没有 data.json" in import_and_read(dx, _zip_of({"hello.txt": "hi"})))
check("不是谷仓导出被拒绝",
      "不是谷仓导出" in import_and_read(
          dx, _zip_of({"data.json": json.dumps({"app": "别的程序", "format": 1})})))
check("更高版本的备份被拒绝",
      "更新版本" in import_and_read(
          dx, _zip_of({"data.json": json.dumps({"app": "谷仓", "format": 99})})))
check("被拒绝的导入一件都没进来", count_c() == n_before, (n_before, count_c()))

bad_zip = _zip_of({
    "data.json": json.dumps({"app": "谷仓", "format": 1, "items": [
        {"name": "带坏图的谷子", "image": "bad.png", "count": "2",
         "status": "displaying"}]}),
    "photos/bad.png": "这不是图片",
})
do_import(dx, bad_zip)
with app17.app_context():
    bad_item = User.query.filter_by(username="bk_c").first().items.filter_by(
        name="带坏图的谷子").first()
check("坏图不会让整包导入失败", bad_item is not None)
check("坏图自动回落 emoji（没写进磁盘）",
      bad_item is not None and bad_item.image is None and bad_item.emoji == "🎁",
      bad_item and (bad_item.image, bad_item.emoji))

# --- 自动备份：立即备份 / 轮转 / 每天一次 / 启动一次 ---
for name in os.listdir(BK_DIR):
    os.remove(os.path.join(BK_DIR, name))
bx.post("/backup/now", data={})
check("「立刻备份」真的产生了文件", len(backup.list_backups(app17)) == 1,
      backup.list_backups(app17))
check("备份文件是完整的 SQLite 库",
      open(os.path.join(BK_DIR, backup.list_backups(app17)[0][0]), "rb"
           ).read(16) == b"SQLite format 3\x00")
check("备份页列出了这份备份",
      backup.list_backups(app17)[0][0] in bx.get("/backup").get_data(as_text=True))

for name in os.listdir(BK_DIR):
    os.remove(os.path.join(BK_DIR, name))
for i in range(5):
    path = backup.make_db_backup(app17, reason=f"rot{i}")
    os.utime(path, (1_700_000_000 + i * 60, 1_700_000_000 + i * 60))  # 让时间明确可排
check("连做 5 次就有 5 份", len(backup.list_backups(app17)) == 5,
      len(backup.list_backups(app17)))
removed = backup.rotate_backups(app17, keep=2)
left = [n for n, _s, _m in backup.list_backups(app17)]
check("轮转只留最近 2 份", len(left) == 2, left)
check("轮转删掉了 3 份", removed == 3, removed)
check("留下的是最新的两份（rot3 / rot4）",
      all(n.endswith(("rot3.db", "rot4.db")) for n in left), left)

backup._last_daily_check = None
for name in os.listdir(BK_DIR):
    os.remove(os.path.join(BK_DIR, name))
app17.config["TESTING"] = False               # 临时打开，让每日逻辑真的跑
try:
    first = backup.ensure_daily_backup(app17)
    second = backup.ensure_daily_backup(app17)
finally:
    app17.config["TESTING"] = True
check("每天第一次会备份", first and os.path.exists(first), first)
check("同一天不会重复备份", second is None, second)

start_dir = os.path.join(TEST_DB_DIR, "sect23-start")
app18 = create_app({"WTF_CSRF_ENABLED": False, "BACKUP_FOLDER": start_dir,
                    "SQLALCHEMY_DATABASE_URI": db_uri("verify_startbk.db")})
start_files = os.listdir(start_dir) if os.path.isdir(start_dir) else []
check("启动时自动备份一份", any(n.endswith("-start.db") for n in start_files),
      start_files)
check("启动备份也是完整库",
      any(open(os.path.join(start_dir, n), "rb").read(16) == b"SQLite format 3\x00"
          for n in start_files), start_files)

# 关掉开关就不该写任何备份文件
no_bk_dir = os.path.join(TEST_DB_DIR, "sect23-nobackup")
_app19 = create_app({"TESTING": True, "AUTO_BACKUP": False,
                     "BACKUP_FOLDER": no_bk_dir,
                     "SQLALCHEMY_DATABASE_URI": db_uri("verify_nobk.db")})
check("AUTO_BACKUP=False 时不备份", not os.path.exists(no_bk_dir))

# --- 下载：只有主账号能拿整库备份 ---
one_name = backup.list_backups(app17)[0][0] if backup.list_backups(app17) else None
bx.post("/backup/now", data={})
one_name = backup.list_backups(app17)[0][0]
check("主账号能下载整库备份", bx.get(f"/backup/download/{one_name}").status_code == 200)
check("备份页给主账号显示下载按钮",
      f"/backup/download/{one_name}" in bx.get("/backup").get_data(as_text=True))
check("第二个账号拿不到整库备份（404，不然能顺走别人数据）",
      cx17.get(f"/backup/download/{one_name}").status_code == 404)
check("第二个账号页面上也不显示下载按钮",
      "/backup/download/" not in cx17.get("/backup").get_data(as_text=True))
check("路径穿越被挡", bx.get("/backup/download/..%2Fgu.db").status_code == 404)
check("不存在的备份 404", bx.get("/backup/download/nope.db").status_code == 404)

print("\n=== 24. 备份路径解析（线上踩过的坑） ===")


class _FakeApp:
    """只为测路径解析：db_file_path 只用到 config 和 instance_path。"""

    def __init__(self, uri, instance_path):
        self.config = {"SQLALCHEMY_DATABASE_URI": uri}
        self.instance_path = instance_path


_fake_inst = os.path.join(TEST_DB_DIR, "fake-instance")
check("相对 sqlite 路径按 instance/ 解析（就是线上备份静默失败那个 bug）",
      backup.db_file_path(_FakeApp("sqlite:///gu.db", _fake_inst))
      == os.path.join(_fake_inst, "gu.db"),
      backup.db_file_path(_FakeApp("sqlite:///gu.db", _fake_inst)))
_abs = os.path.join(TEST_DB_DIR, "abs.db")
check("绝对路径原样使用",
      backup.db_file_path(_FakeApp("sqlite:///" + _abs.replace("\\", "/"), _fake_inst))
      == _abs)
check("非 sqlite 的数据库不备份",
      backup.db_file_path(_FakeApp("postgresql://x/y", _fake_inst)) is None)
check("没配 URI 也不炸", backup.db_file_path(_FakeApp("", _fake_inst)) is None)

print("\n=== 25. 个人信息 + 账号与安全 ===")


def flash_of(client, path="/profile"):
    """把页面上的 flash 文案抠出来（操作结果靠它反馈）。"""
    page = client.get(path).get_data(as_text=True)
    return " ".join(re.findall(r'<div class="flash[^"]*">([^<]*)</div>', page))


app20 = fresh_app(db_uri("verify_account.db"), WTF_CSRF_ENABLED=False,
                  LOGIN_LOCK_SECONDS=2, LOGIN_MAX_FAILS=3)
ax = app20.test_client()                     # 「这台设备」
ax.post("/register", data={"username": "sec_a", "password": "abc123"})
bx20 = app20.test_client()                   # 「另一台设备」
bx20.post("/login", data={"username": "sec_a", "password": "abc123"})
cx20 = app20.test_client()                   # 用来占一个「别人的用户名」
cx20.post("/register", data={"username": "sec_b", "password": "abc123"})

# --- 个人信息页：白底只读列表（像微信资料页）---
check("匿名进不了个人信息页",
      app20.test_client().get("/profile").status_code == 302)
profile = ax.get("/profile").get_data(as_text=True)
check("个人信息页能打开且显示用户名",
      "个人信息" in profile and "sec_a" in profile)
check("页面是白底列表版式（不是一堆圆角卡片）",
      'class="phone plain"' in profile and 'class="wx-group"' in profile)
check("主页面没有任何表单，也没有保存按钮",
      "<form" not in profile and "<button" not in profile)
check("列表正好五行（头像 / 名字 / 签名 / 地区 + 账号与数据）",
      profile.count('class="wx-item"') == 5, profile.count('class="wx-item"'))
check("每一行都是「左标签 + 右内容 + 箭头」",
      profile.count('class="wx-label"') == 5 and profile.count('class="wx-value') == 5
      and profile.count('class="wx-arrow"') == 5,
      (profile.count('class="wx-label"'), profile.count('class="wx-value'),
       profile.count('class="wx-arrow"')))
check("三行分别跳到头像 / 名字 / 地区编辑页",
      re.search(r'href="/profile/avatar"[^>]*id="itemAvatar"', profile) is not None
      and re.search(r'href="/profile/username"[^>]*id="itemUsername"', profile) is not None
      and re.search(r'href="/profile/region"[^>]*id="itemRegion"', profile) is not None,
      re.findall(r'<a class="wx-item"[^>]*>', profile)[:3])
check("名字那行右侧显示当前用户名",
      re.search(r'id="itemUsername".*?wx-value is-set">sec_a<', profile, re.S) is not None)
check("没填地区时右侧显示「未填写」",
      "未填写" in profile)
check("头像那行右侧是缩略图", 'class="wx-thumb"' in profile)
check("页面顶部保留标题和返回首页按钮",
      "个人信息" in profile and profile.count('aria-label="返回首页"') == 1)
check("还有一条去「账号与数据」的行",
      re.search(r'href="/account-data"[^>]*id="itemAccountData"', profile) is not None,
      re.findall(r'<a class="wx-item"[^>]*>', profile))

# --- 三个子编辑页 ---
for path_, title, form_action in (("/profile/avatar", "修改头像", "/profile/avatar"),
                                  ("/profile/username", "修改名字", "/profile/username"),
                                  ("/profile/region", "选择地区", "/profile/region")):
    resp_ = ax.get(path_)
    body_ = resp_.get_data(as_text=True)
    check(f"{path_} 能打开（{title}）",
          resp_.status_code == 200 and title in body_, resp_.status_code)
    check(f"{path_} 也是白底列表版式", 'class="phone plain"' in body_)
    check(f"{path_} 的返回键回个人信息页",
          'class="back-btn" href="/profile"' in body_)
    check(f"{path_} 里有表单且带 CSRF token",
          f'action="{form_action}"' in body_ and 'name="csrf_token"' in body_)
    check(f"{path_} 只有返回、没有第二个「返回首页」",
          body_.count('aria-label="返回首页"') == 0)

avatar_page = ax.get("/profile/avatar").get_data(as_text=True)
check("头像编辑页保留选文件 + 保存按钮 + 提示文字",
      'name="avatar"' in avatar_page and "multipart/form-data" in avatar_page
      and 'id="avatarSaveBtn"' in avatar_page and "png / jpg / gif / webp" in avatar_page)
check("还没头像时不显示删除按钮（没得删）",
      'id="avatarRemoveBtn"' not in avatar_page)

name_page = ax.get("/profile/username").get_data(as_text=True)
check("名字编辑页保留输入框 + 提示 + 按钮",
      'id="newUsername"' in name_page and 'id="renameBtn"' in name_page
      and "登录名" in name_page)

region_page = ax.get("/profile/region").get_data(as_text=True)
check("地区页是四级下拉：国家 → 省 → 市 → 区/县",
      all(f'name="{f}"' in region_page for f in ("country", "province", "city", "area"))
      and region_page.count("<select") == 4,
      region_page.count("<select"))
check("国家下拉里有中国",
      re.search(r'<option value="中国"\s+selected', region_page) is not None
      or 'value="中国"' in region_page)
check("省下拉里是真实的省名（广东省）", 'value="广东省"' in region_page)
check("有「其他（手动填写）」兜底",
      '__manual__' in region_page and "其他（手动填写）" in region_page)
check("有「用当前位置」按钮和放坐标的隐藏字段",
      'id="locateBtn"' in region_page and 'id="locateLat"' in region_page
      and 'id="locateLon"' in region_page)
check("页面上说明了顺序和定位限制",
      "国家 → 省 → 市 → 区/县" in region_page and "市级近似" in region_page)
check("地区数据是单独一份静态文件，没有整份内嵌进页面",
      "/static/regions.json" in region_page
      and '"宁夏回族自治区":{"银川市"' not in region_page)

# --- 同城判据（地区能选到区/县之后，同城必须按「省 + 市」算）---
check("同城判据：同一个市的不同区算同城（不能被区拆开）",
      regions.is_same_city("广东 深圳 南山", "广东 深圳 福田"))
check("同城判据：只选到市 vs 选到区，也算同城",
      regions.is_same_city("广东 深圳", "广东 深圳 南山"))
check("同城判据：不同市不算同城",
      not regions.is_same_city("广东 深圳", "广东 广州"))
check("同城判据：直辖市「市 区」两级写法也对",
      regions.is_same_city("上海 徐汇", "上海 静安"))
check("同城判据：有一个没填地区就不算同城",
      not regions.is_same_city("", "广东 深圳")
      and not regions.is_same_city("广东 深圳", None))
check("同区判据：直辖市取第二级、普通省取第三级、只有两级时为空",
      regions.district_key("上海 徐汇") == "徐汇"
      and regions.district_key("广东 深圳 南山") == "南山"
      and regions.district_key("广东 深圳") == "")
check("手动填的自定义地区按整串比",
      regions.is_same_city("火星 环形山", "火星 环形山")
      and not regions.is_same_city("火星 环形山", "火星 陨石坑"))

# --- 地区：选到区/县 ---
ax.post("/profile/region", data={"country": "中国", "province": "广东省",
                                 "city": "深圳市", "area": "南山区",
                                 "next": "/profile/region"})
check("选到区/县能保存，且存成短名「广东 深圳 南山」",
      "地区已设为「广东 深圳 南山」" in flash_of(ax, "/profile/region"),
      flash_of(ax, "/profile/region"))
with app20.app_context():
    check("库里就是短名写法",
          User.query.filter_by(username="sec_a").first().region == "广东 深圳 南山")
region_page = ax.get("/profile/region").get_data(as_text=True)
check("回填时四级都选中",
      re.search(r'<option value="广东省"\s+selected', region_page) is not None
      and re.search(r'<option value="深圳市"\s+selected', region_page) is not None
      and re.search(r'<option value="南山区"\s+selected', region_page) is not None,
      [x.strip() for x in region_page.splitlines() if "selected" in x][:4])
check("个人信息页的地区那一行也同步显示",
      "广东 深圳 南山" in ax.get("/profile").get_data(as_text=True))

# 只选到市（区/县可以不选）
ax.post("/profile/region", data={"country": "中国", "province": "广东省",
                                 "city": "深圳市", "area": "",
                                 "next": "/profile/region"})
check("区/县可以不选，只存到市",
      "地区已设为「广东 深圳」" in flash_of(ax, "/profile/region"))

# 直辖市：数据里的「市辖区」这一层会被跳过
ax.post("/profile/region", data=SHANGHAI_XUHUI | {"next": "/profile/region"})
check("直辖市不会存成「上海 市辖区 徐汇」",
      "地区已设为「上海 徐汇」" in flash_of(ax, "/profile/region"),
      flash_of(ax, "/profile/region"))

# 假路径要被挡下来
ax.post("/profile/region", data={"country": "中国", "province": "广东省",
                                 "city": "深圳市", "area": "不存在区",
                                 "next": "/profile/region"})
check("数据里没有的区会被拒", "不在数据里" in flash_of(ax, "/profile/region"))
ax.post("/profile/region", data={"country": "中国", "province": "",
                                 "next": "/profile/region"})
check("连省都没选会被拒", "至少选到省份" in flash_of(ax, "/profile/region"))
with app20.app_context():
    check("被拒的两次都没改掉库里的地区",
          User.query.filter_by(username="sec_a").first().region == "上海 徐汇",
          User.query.filter_by(username="sec_a").first().region)

# --- 手动填写兜底 ---
ax.post("/profile/region", data={"country": regions.MANUAL_VALUE,
                                 "region_manual": "火星 环形山",
                                 "next": "/profile/region"})
check("选「其他」+ 手动填写也能存",
      "地区已设为「火星 环形山」" in flash_of(ax, "/profile/region"))
manual_page = ax.get("/profile/region").get_data(as_text=True)
check("自定义写法会走「其他」并回填手动输入框",
      re.search(r'<option value="__manual__"\s+selected', manual_page) is not None
      and 'value="火星 环形山"' in manual_page)
ax.post("/profile/region", data={"country": regions.MANUAL_VALUE,
                                 "region_manual": "  ", "next": "/profile/region"})
check("手动填写为空会被拒", "请填写地区" in flash_of(ax, "/profile/region"))

# --- 定位：挑最近的市 ---
ax.post("/profile/region", data={"lat": "22.60", "lon": "114.10",
                                 "next": "/profile/region"})
# flash 读一次就没了，所以先存下来再断言
locate_flash = flash_of(ax, "/profile/region")
check("定位到深圳附近 → 判为「广东 深圳」", "广东 深圳" in locate_flash)
check("定位的提示里说明了是市级近似、区县自己补",
      "最近的城市" in locate_flash and "公里" in locate_flash, locate_flash)
ax.post("/profile/region", data={"lat": "39.90", "lon": "116.40",
                                 "next": "/profile/region"})
check("定位到北京 → 判为「北京」",
      "北京" in flash_of(ax, "/profile/region"))
ax.post("/profile/region", data={"lat": "abc", "lon": "1", "next": "/profile/region"})
check("坐标不合法会被拒", "坐标不合法" in flash_of(ax, "/profile/region"))
ax.post("/profile/region", data={"lat": "999", "lon": "999", "next": "/profile/region"})
check("超范围的坐标也会被拒", "坐标不合法" in flash_of(ax, "/profile/region"))

# 老数据兼容：以前手工填的「广东  深圳」（两个空格）也要能认出来并回填到下拉
with app20.app_context():
    u_tmp = User.query.filter_by(username="sec_a").first()
    u_tmp.region = "广东  深圳"
    db.session.commit()
region_page = ax.get("/profile/region").get_data(as_text=True)
check("老写法「广东  深圳」能被认出来（省/市预选上，没掉进手动填写）",
      re.search(r'<option value="广东省"\s+selected', region_page) is not None
      and re.search(r'<option value="深圳市"\s+selected', region_page) is not None
      and re.search(r'<option value="__manual__"\s+selected', region_page) is None,
      [x.strip() for x in region_page.splitlines() if "selected" in x][:4])
check("个人信息页显示的也是标准写法",
      "广东 深圳" in ax.get("/profile").get_data(as_text=True))

# --- 签名 ---
check("个人信息页有「签名」这一行",
      re.search(r'href="/profile/signature"[^>]*id="itemSignature"',
                ax.get("/profile").get_data(as_text=True)) is not None)
sig_page = ax.get("/profile/signature")
check("签名编辑页能打开", sig_page.status_code == 200)
sig_body = sig_page.get_data(as_text=True)
check("签名页有输入框 + 保存按钮 + 字数上限",
      'id="signatureInput"' in sig_body and 'id="signatureSaveBtn"' in sig_body
      and 'maxlength="60"' in sig_body)
check("签名页是白底 + 返回个人信息",
      'class="phone plain"' in sig_body
      and 'class="back-btn" href="/profile"' in sig_body)
check("还没签名时个人信息页显示「未填写」",
      re.search(r'id="signatureValue">\s*未填写', ax.get("/profile").get_data(as_text=True))
      is not None)

ax.post("/profile/signature", data={"signature": "签名测试用文本-ABCD", "next": "/profile/signature"})
check("保存签名有提示", "签名已更新" in flash_of(ax, "/profile/signature"))
with app20.app_context():
    check("签名进库了",
          User.query.filter_by(username="sec_a").first().signature == "签名测试用文本-ABCD")
check("个人信息页显示签名",
      "签名测试用文本-ABCD" in ax.get("/profile").get_data(as_text=True))
signed_dash = ax.get("/").get_data(as_text=True)
check("首页问候语下面也显示签名",
      'class="greet-sign"' in signed_dash
      and "签名测试用文本-ABCD" in signed_dash)
check("签名页回填当前签名",
      'value="签名测试用文本-ABCD"' in ax.get("/profile/signature").get_data(as_text=True))

ax.post("/profile/signature", data={"signature": "x" * 200, "next": "/profile/signature"})
with app20.app_context():
    saved = User.query.filter_by(username="sec_a").first().signature
check("签名超长会被截到 60 字（不会写进超长数据）", len(saved) == 60, len(saved))
ax.post("/profile/signature", data={"signature": "", "next": "/profile/signature"})
check("清空保存就是删除签名", "签名已清空" in flash_of(ax, "/profile/signature"))
with app20.app_context():
    check("库里签名已清空",
          User.query.filter_by(username="sec_a").first().signature is None)
final_dash = ax.get("/").get_data(as_text=True)
check("清空后首页不再显示签名",
      'class="greet-sign"' not in final_dash
      and "签名测试用文本" not in final_dash)


# --- 头像（在子编辑页里操作）---
with app20.app_context():
    up_dir20 = os.path.join(TEST_DB_DIR, "uploads")
ax.post("/profile/avatar",
        data={"avatar": (make_image("PNG"), "me.png"), "next": "/profile/avatar"},
        content_type="multipart/form-data")
check("上传头像后有提示", "头像已更新" in flash_of(ax, "/profile/avatar"),
      flash_of(ax, "/profile/avatar"))
with app20.app_context():
    u_a = User.query.filter_by(username="sec_a").first()
    avatar_name = u_a.avatar
check("头像文件名入库了", bool(avatar_name), avatar_name)
check("头像文件真的写进磁盘",
      avatar_name and os.path.exists(os.path.join(up_dir20, avatar_name)))
check("头像能当图片打开",
      avatar_name and PILImage.open(os.path.join(up_dir20, avatar_name)).format == "PNG")
check("头像编辑页显示这张图",
      f"/static/uploads/{avatar_name}" in ax.get("/profile/avatar").get_data(as_text=True))
check("个人信息页那一行也显示成头像缩略图",
      f"/static/uploads/{avatar_name}" in ax.get("/profile").get_data(as_text=True))
check("有头像后编辑页出现删除按钮",
      'id="avatarRemoveBtn"' in ax.get("/profile/avatar").get_data(as_text=True))
check("首页顶栏也换成头像了",
      f"/static/uploads/{avatar_name}" in ax.get("/").get_data(as_text=True))
check("顶栏头像就是个人信息入口",
      'class="avatar-btn"' in ax.get("/").get_data(as_text=True)
      and 'href="/profile"' in ax.get("/").get_data(as_text=True))

ax.post("/profile/avatar",
        data={"avatar": (io.BytesIO(b"not an image"), "fake.png"),
              "next": "/profile/avatar"},
        content_type="multipart/form-data")
check("伪造的图片会被拒（不写盘）",
      "格式不支持" in flash_of(ax, "/profile/avatar"))
with app20.app_context():
    check("被拒后头像还是原来那张",
          User.query.filter_by(username="sec_a").first().avatar == avatar_name)
ax.post("/profile/avatar", data={"next": "/profile/avatar"},
        content_type="multipart/form-data")
check("没选文件就提交会被提示", "请先选一张图片" in flash_of(ax, "/profile/avatar"))

old_avatar = avatar_name
ax.post("/profile/avatar",
        data={"avatar": (make_image("JPEG"), "me2.jpg"), "next": "/profile/avatar"},
        content_type="multipart/form-data")
with app20.app_context():
    new_avatar = User.query.filter_by(username="sec_a").first().avatar
check("换头像会换成新文件",
      new_avatar and new_avatar != old_avatar, (old_avatar, new_avatar))
check("旧头像文件被清掉了（不留垃圾）",
      not os.path.exists(os.path.join(up_dir20, old_avatar)))

ax.post("/profile/avatar", data={"remove_avatar": "1", "next": "/profile/avatar"})
check("可以删除头像", "头像已删除" in flash_of(ax, "/profile/avatar"))
with app20.app_context():
    check("删干净了：库里没记录、盘上没文件",
          User.query.filter_by(username="sec_a").first().avatar is None
          and not os.path.exists(os.path.join(up_dir20, new_avatar)))

# --- 用户名（在子编辑页里改）---
ax.post("/profile/username", data={"username": "ab", "next": "/profile/username"})
check("用户名太短会被拒", "用户名长度" in flash_of(ax, "/profile/username"))
ax.post("/profile/username", data={"username": "x" * 40, "next": "/profile/username"})
check("用户名太长会被拒（不是悄悄截断）",
      "最多" in flash_of(ax, "/profile/username"))
with app20.app_context():
    check("超长的那次没有改名（还是 sec_a）",
          User.query.filter_by(username="sec_a").first() is not None)
ax.post("/profile/username", data={"username": "sec_a", "next": "/profile/username"})
check("改成和现在一样的会被拒", "没改" in flash_of(ax, "/profile/username"))
ax.post("/profile/username", data={"username": "sec_b", "next": "/profile/username"})
check("占用别人的用户名会被拒", "已被占用" in flash_of(ax, "/profile/username"))
ax.post("/profile/username", data={"username": "sec_a2", "next": "/profile/username"})
check("改用户名成功并有提示", "改为" in flash_of(ax, "/profile/username"))
with app20.app_context():
    renamed = User.query.filter_by(username="sec_a2").first()
    check("库里用户名已更新", renamed is not None)
    check("改名字不动会话令牌（不会把自己踢下线）",
          renamed is not None and not renamed.session_token)
check("新用户名 + 原密码能登录",
      app20.test_client().post(
          "/login", data={"username": "sec_a2", "password": "abc123"},
          follow_redirects=True).status_code == 200)

# --- 账号与数据：独立一页，只放两块入口 ---
check("匿名进不了账号与数据页",
      app20.test_client().get("/account-data").status_code == 302)
data_page = ax.get("/account-data").get_data(as_text=True)
check("账号与数据页能打开", "账号与数据" in data_page)
check("页面上分别有账号与安全 / 数据备份两个入口",
      'id="toAccount"' in data_page and 'href="/account"' in data_page
      and 'id="toBackup"' in data_page and 'href="/backup"' in data_page)
check("这一页自己不做事（没有表单）",
      "<form" not in data_page and 'type="password"' not in data_page)
check("说明了个人信息在另一页",
      'href="/profile"' in data_page and "个人信息" in data_page)
check("账号与数据页只留一个返回首页入口",
      data_page.count('aria-label="返回首页"') == 1,
      data_page.count('aria-label="返回首页"'))
check("账号与安全 / 数据备份的下级页都返回到这一页",
      'href="/account-data"' in ax.get("/account").get_data(as_text=True)
      and 'href="/account-data"' in ax.get("/backup").get_data(as_text=True))

# --- 账号与安全：只是菜单 ---
check("匿名进不了账号与安全",
      app20.test_client().get("/account").status_code == 302)
menu = ax.get("/account").get_data(as_text=True)
check("账号与安全是选项列表（点进去才做事）",
      'id="toPassword"' in menu and 'href="/account/password"' in menu
      and 'id="toSessions"' in menu and 'href="/account/sessions"' in menu)
check("菜单页本身没有密码输入框（保持简单）",
      'type="password"' not in menu and 'name="old_password"' not in menu)
check("菜单页没有再放改名/头像（那些在个人信息里）",
      "/profile/username" not in menu and "/profile/avatar" not in menu)
check("菜单页写了这个程序怎么保护账号",
      "散列" in menu and "CSRF" in menu)

# --- 修改密码（下钻页） ---
pwd_page = ax.get("/account/password").get_data(as_text=True)
check("修改密码页能打开，有三个密码框",
      pwd_page.count('type="password"') == 3, pwd_page.count('type="password"'))
check("密码框标了 autocomplete",
      'autocomplete="current-password"' in pwd_page
      and pwd_page.count('autocomplete="new-password"') == 2)


def try_password(client, old, new, confirm=None):
    return client.post("/account/password",
                       data={"old_password": old, "new_password": new,
                             "confirm_password": confirm if confirm is not None else new})


def flash_in(resp):
    """校验失败时是「直接重新渲染表单」，flash 就在这次响应里，
    不会留到下一次 GET——所以要当场读，不能再去 GET 一遍。"""
    return " ".join(re.findall(r'<div class="flash[^"]*">([^<]*)</div>',
                               resp.get_data(as_text=True)))


r = try_password(ax, "wrong-one", "newpass1")
check("当前密码不对会被拒", "当前密码不对" in flash_in(r), flash_in(r))
r = try_password(ax, "abc123", "abc")
check("新密码太短会被拒", "至少" in flash_in(r), flash_in(r))
r = try_password(ax, "abc123", "newpass1", "newpass2")
check("两次新密码不一致会被拒", "不一致" in flash_in(r), flash_in(r))
r = try_password(ax, "abc123", "abc123")
check("新密码和当前密码相同会被拒", "一样" in flash_in(r), flash_in(r))
with app20.app_context():
    check("被拒的几次都没改掉密码",
          User.query.filter_by(username="sec_a2").first().check_password("abc123"))
try_password(ax, "abc123", "newpass1")
check("改密码成功后跳回账号与安全页",
      "密码已修改" in flash_of(ax, "/account"), flash_of(ax, "/account"))
check("这台设备还登录着（没把自己踢下线）",
      ax.get("/account/password").status_code == 200)
check("另一台设备被踢下线（关键安全行为）",
      bx20.get("/account").status_code == 302)
check("新密码能登录",
      app20.test_client().post(
          "/login", data={"username": "sec_a2", "password": "newpass1"},
          follow_redirects=True).status_code == 200)
with app20.app_context():
    tok = User.query.filter_by(username="sec_a2").first().session_token
check("库里会话令牌已轮换（不为空）", bool(tok))

# --- 登录与设备（下钻页） ---
bx20 = app20.test_client()
bx20.post("/login", data={"username": "sec_a2", "password": "newpass1"})
sess_page = ax.get("/account/sessions").get_data(as_text=True)
check("登录与设备页能打开且显示上次登录",
      "上次登录" in sess_page
      and re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", sess_page) is not None)
check("页面上有退出其它设备按钮",
      'id="logoutAllBtn"' in sess_page and 'action="/account/sessions"' in sess_page)
check("退出其它设备要二次确认", 'data-confirm' in sess_page)
ax.post("/account/sessions", data={})
check("退出其它设备后有提示，并跳回账号与安全",
      "已退出其它设备" in flash_of(ax, "/account"), flash_of(ax, "/account"))
check("这台仍登录着", ax.get("/account/sessions").status_code == 200)
check("另一台被踢下线", bx20.get("/account").status_code == 302)
with app20.app_context():
    tok2 = User.query.filter_by(username="sec_a2").first().session_token
check("令牌又换了一次", tok2 and tok2 != tok)

# --- 升级兼容：库里令牌为空时不校验 ---
dx20 = app20.test_client()
dx20.post("/register", data={"username": "sec_c", "password": "abc123"})
check("新注册的账号能正常用", dx20.get("/profile").status_code == 200)
with app20.app_context():
    User.query.filter_by(username="sec_c").first().session_token = None
    db.session.commit()
check("库里令牌为空时，老登录态不被踢（升级不打断使用）",
      dx20.get("/profile").status_code == 200)

# --- 登录失败限流 ---
lock = app20.test_client()
for _ in range(3):
    lock.post("/login", data={"username": "sec_b", "password": "wrong"})
blocked = lock.post("/login", data={"username": "sec_b", "password": "abc123"})
check("连续失败到上限后，连正确密码也进不去",
      "次数太多" in blocked.get_data(as_text=True), "没触发锁定")
check("被锁期间确实没登录进去", lock.get("/profile").status_code == 302)
time.sleep(2.1)                          # 测试里把锁定时间调成了 2 秒
ok_login = lock.post("/login", data={"username": "sec_b", "password": "abc123"},
                     follow_redirects=True)
check("锁定时间过了就能正常登录",
      "次数太多" not in ok_login.get_data(as_text=True)
      and lock.get("/profile").status_code == 200)

print("\n=== 26. 谷柜页：搜索 / 筛选 / 排序 / 分页 ===")
app21 = fresh_app(db_uri("verify_search.db"), WTF_CSRF_ENABLED=False)
sx = app21.test_client()
sx.post("/register", data={"username": "searchuser", "password": "abc123"})
sx.post("/categories/add", data={"kind": "type", "name": "吧唧"})
sx.post("/categories/add", data={"kind": "ip", "name": "某某"})
with app21.app_context():
    stype = Category.query.filter_by(kind="type", name="吧唧").first().id
    sip = Category.query.filter_by(kind="ip", name="某某").first().id

# 造 30 件：数量、入手价、分类都有规律，方便验排序和筛选
for i in range(1, 31):
    sx.post("/items/add", data={
        "name": f"谷子{i:02d}", "emoji": "🎁", "count": str(i),
        "status": "displaying", "price": str(i * 10),
        "category_type_id": str(stype) if i % 2 else "",
        "category_ip_id": str(sip) if i % 3 == 0 else "",
        "order_no": "SF0007" if i == 7 else "",
        "want_note": "只收原画" if i == 8 else "",
    })


def grid_names(html):
    """卡片上的名字，按渲染顺序（就是列表顺序）。"""
    return re.findall(r'data-name="([^"]*)"', html)


def first_card(html):
    names = grid_names(html)
    return names[0] if names else None


board = sx.get("/items").get_data(as_text=True)
check("默认先显示一屏 24 件（不是全量渲染）",
      len(grid_names(board)) == 24, len(grid_names(board)))
check("顶栏写着总数", "共 30 件" in board)
check("还有剩的时候给「显示更多」（带剩余件数）",
      'id="showMore"' in board and "还有 6 件" in board)
check("默认排序是最新添加（第 30 件在最前）",
      first_card(board) == "谷子30", first_card(board))
more = sx.get("/items?n=48").get_data(as_text=True)
check("n 变大后能一次显示全部 30 件", len(grid_names(more)) == 30,
      len(grid_names(more)))
check("全显示完了就不再给「显示更多」", 'id="showMore"' not in more)
with app21.test_request_context("/items?n=9999"):
    check("每页上限有封顶（防止一次渲染几千件）",
          routes._current_page_size() == routes.MAX_PAGE_SIZE,
          routes._current_page_size())
with app21.test_request_context("/items?n=abc"):
    check("n 传脏值退回默认", routes._current_page_size() == routes.PAGE_SIZE)
with app21.test_request_context("/items?sort=不存在的"):
    check("sort 传脏值退回默认", routes._current_sort() == "new")

# --- 搜索 ---
hit = sx.get("/items?q=谷子07").get_data(as_text=True)
check("按名字搜只出那一件", grid_names(hit) == ["谷子07"], grid_names(hit))
check("搜到之后显示「找到 N 件」", "找到 1 件" in hit)
check("搜索状态下有清除入口", 'id="clearFilters"' in hit)
check("按订单号也能搜到（搜的不只是名字）",
      grid_names(sx.get("/items?q=SF0007").get_data(as_text=True)) == ["谷子07"])
check("按备注也能搜到",
      grid_names(sx.get("/items?q=只收原画").get_data(as_text=True)) == ["谷子08"])
miss = sx.get("/items?q=不存在的名字").get_data(as_text=True)
check("搜不到时给空状态提示", "没找到含「不存在的名字」的谷子" in miss)
check("空状态里也能一键清除筛选", 'id="emptyClear"' in miss)
xss = sx.get("/items?q=<script>alert(1)</script>").get_data(as_text=True)
check("关键词里的尖括号被转义，不会变成真标签",
      "<script>alert(1)</script>" not in xss and "&lt;script&gt;" in xss)

# --- 筛选 ---
by_type = sx.get(f"/items?type={stype}").get_data(as_text=True)
check("按品类筛选只出奇数件（15 件）", len(grid_names(by_type)) == 15,
      len(grid_names(by_type)))
check("筛选后顶栏显示「筛选后 N 件」", "筛选后 15 件" in by_type)
by_ip = sx.get(f"/items?ip={sip}").get_data(as_text=True)
check("按作品IP筛选只出 3 的倍数（10 件）", len(grid_names(by_ip)) == 10,
      len(grid_names(by_ip)))
both = sx.get(f"/items?type={stype}&ip={sip}").get_data(as_text=True)
check("两个筛选条件是「且」（奇数且 3 的倍数：3/9/15/21/27，5 件）",
      len(grid_names(both)) == 5, len(grid_names(both)))
check("筛选组合起来仍然是最新在前（这批里最新的是谷子27）",
      first_card(both) == "谷子27", first_card(both))
combo = grid_names(sx.get(f"/items?type={stype}&q=谷子0").get_data(as_text=True))
check("筛选能和搜索叠加（奇数且名字含谷子0）",
      combo == ["谷子09", "谷子07", "谷子05", "谷子03", "谷子01"], combo)
with app21.app_context():
    other = User(username="other_owner")
    other.set_password("abc123")
    db.session.add(other)
    db.session.commit()
    other_cat = Category(user_id=other.id, kind="type", name="别人的分类")
    db.session.add(other_cat)
    db.session.commit()
    other_cat_id = other_cat.id
check("拿别人的分类 id 来筛等于没筛（不能借它探测别的东西）",
      len(grid_names(sx.get(f"/items?type={other_cat_id}").get_data(as_text=True)))
      == 24)
check("乱填的筛选参数不报错", sx.get("/items?type=abc&ip=-1").status_code == 200)

# --- 排序 ---
check("按数量排：最多的一件在最前",
      first_card(sx.get("/items?sort=count").get_data(as_text=True)) == "谷子30")
check("按入手价排：最贵的一件在最前",
      first_card(sx.get("/items?sort=price").get_data(as_text=True)) == "谷子30")
check("按名字排：谷子01 在最前",
      first_card(sx.get("/items?sort=name").get_data(as_text=True)) == "谷子01")
check("最早添加：谷子01 在最前",
      first_card(sx.get("/items?sort=old").get_data(as_text=True)) == "谷子01")
check("快到货的（大多没填日期）也不报错",
      sx.get("/items?sort=expect").status_code == 200)

# --- 分组 / 翻页都要带着当前条件 ---
seg = sx.get("/items?q=谷子0&sort=name").get_data(as_text=True)
seg_links = re.findall(r'class="seg-btn[^"]*"\s+href="([^"]+)"', seg)
check("切换分组时不丢搜索和排序",
      bool(seg_links) and all("q=" in link and "sort=name" in link
                              for link in seg_links), seg_links)
more_link = re.search(r'id="showMore" href="([^"]+)"', board)
check("「显示更多」也带着当前条件", more_link is not None
      and "group=" in more_link.group(1))
grouped = sx.get(f"/items?group=type&type={stype}").get_data(as_text=True)
check("筛选之后按品类分组，只出现被筛出来的那一组",
      grouped.count("group-head") == 1, grouped.count("group-head"))
check("筛选条件不会被记住（重新进谷柜还是全部 30 件）",
      "共 30 件" in sx.get("/items").get_data(as_text=True)
      and len(grid_names(sx.get("/items?n=100").get_data(as_text=True))) == 30)

print("\n" + "=" * 46)
if FAILS:
    print(f"❌ {len(FAILS)} 项失败：")
    for f in FAILS:
        print("   -", f)
    sys.exit(1)
print("✅ 全部检查通过")
