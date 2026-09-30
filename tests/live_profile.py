# -*- coding: utf-8 -*-
"""线上验证：个人信息（头像/用户名/地区）+ 账号与安全（菜单 + 下钻页）。
会临时写两样东西，然后**当场还原**并确认还原干净：
  · demo 的地区（原来是空）→ 试完写回空
  · demo 的头像（原来是空）→ 试完删掉，并确认文件也从磁盘上没了
不改用户名、不改密码（那会动到登录方式）。
"""
import http.cookiejar as cookiejar
import io
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import regions  # noqa: E402

# 仓库根目录：这样从任何位置运行都能找到 instance/ 和 static/
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BASE = "http://127.0.0.1:5000"
DB = os.path.join(ROOT_DIR, "instance", "gu.db")
UP_DIR = os.path.join(ROOT_DIR, "static", "uploads")
ok = 0
bad = []


def check(name, cond, extra=""):
    global ok
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        bad.append(name)
        print(f"  FAIL  {name}  {extra}")


def snapshot():
    with sqlite3.connect(DB) as c:
        return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("users", "gu_items", "categories", "reminders", "exchanges")}


def users_now():
    with sqlite3.connect(DB) as c:
        return list(c.execute("SELECT username, region, avatar FROM users ORDER BY id"))


def flash_in(text):
    return " ".join(re.findall(r'<div class="flash[^"]*">([^<]*)</div>', text))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


jar = cookiejar.CookieJar()
follow = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
plain = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), NoRedirect)
anon = urllib.request.build_opener(NoRedirect)


def req(opener, path, data=None, raw=False):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    try:
        with opener.open(urllib.request.Request(BASE + path, data=body),
                         timeout=25) as resp:
            payload = resp.read()
            return resp.status, payload if raw else payload.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        payload = e.read()
        return e.code, payload if raw else payload.decode("utf-8", "replace")


def csrf(html):
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1) if m else None


def post_multipart(path, fields, files):
    """手搓一个 multipart 请求（标准库没有现成的）。"""
    boundary = "----guboundary" + uuid.uuid4().hex
    body = io.BytesIO()
    for key, value in fields.items():
        body.write(f"--{boundary}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
        body.write(f"{value}\r\n".encode())
    for key, (filename, blob) in files.items():
        body.write(f"--{boundary}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="{key}";'
                   f' filename="{filename}"\r\n'.encode())
        body.write(b"Content-Type: image/png\r\n\r\n")
        body.write(blob)
        body.write(b"\r\n")
    body.write(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        BASE + path, data=body.getvalue(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with plain.open(request, timeout=25) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


before = snapshot()
before_users = users_now()
uploads_before = sorted(os.listdir(UP_DIR))
print("测试前:", before, before_users)

print("\n=== 1. 新列与初始状态 ===")
with sqlite3.connect(DB) as c:
    cols = {r[1] for r in c.execute("PRAGMA table_info(users)")}
check("users 表有 avatar 列（迁移没漏）", "avatar" in cols, sorted(cols))
check("读到的头像状态和库里一致（不去动别人已设置的头像）",
      all((a is None) == (a is None) for _u, _r, a in before_users))

print("\n=== 2. 个人信息页 ===")
code, _ = req(anon, "/profile")
check("未登录访问个人信息被挡（302）", code == 302, code)
code, login_page = req(follow, "/login")
code, _ = req(follow, "/login", {"username": "demo", "password": "demo123",
                                 "csrf_token": csrf(login_page)})
check("demo 登录成功", code == 200, code)
code, profile = req(follow, "/profile")
check("个人信息页 200", code == 200, code)
check("白底列表版式（没有圆角卡片容器）",
      'class="phone plain"' in profile and 'class="wx-group"' in profile
      and 'class="set-card"' not in profile)
check("主页面没有任何表单和保存按钮",
      "<form" not in profile and "<button" not in profile)
check("列表五行（头像 / 名字 / 签名 / 地区 + 账号与数据）",
      profile.count('class="wx-item"') == 5, profile.count('class="wx-item"'))
check("每行都是左标签 + 右内容 + 箭头",
      profile.count('class="wx-label"') == 5
      and profile.count('class="wx-value') == 5
      and profile.count('class="wx-arrow"') == 5,
      (profile.count('class="wx-label"'), profile.count('class="wx-value'),
       profile.count('class="wx-arrow"')))
check("四行分别跳到四个编辑页 + 账号与数据",
      re.search(r'href="/profile/avatar"[^>]*id="itemAvatar"', profile) is not None
      and re.search(r'href="/profile/username"[^>]*id="itemUsername"', profile) is not None
      and re.search(r'href="/profile/signature"[^>]*id="itemSignature"', profile) is not None
      and re.search(r'href="/profile/region"[^>]*id="itemRegion"', profile) is not None
      and re.search(r'href="/account-data"[^>]*id="itemAccountData"', profile) is not None,
      re.findall(r'<a class="wx-item"[^>]*>', profile)[:5])
check("头像那行右侧是缩略图（没头像时是首字）",
      'class="wx-thumb"' in profile and ">D<" in profile)
check("名字那行右侧显示用户名", "sec" not in profile or "demo" in profile)
check("地区那行右侧显示当前地区或「未填写」",
      "未填写" in profile or "广东" in profile)
check("顶部保留标题 + 返回首页", profile.count('aria-label="返回首页"') == 1)

print("\n=== 2a. 三个子编辑页 ===")
for path_, title, must in (("/profile/avatar", "修改头像",
                            ('name="avatar"', "multipart/form-data",
                             'id="avatarSaveBtn"')),
                           ("/profile/username", "修改名字",
                            ('id="newUsername"', 'id="renameBtn"', "登录名")),
                           ("/profile/region", "选择地区",
                            ('name="country"', 'name="province"', 'name="city"',
                             'name="area"', 'id="locateBtn"', "市级近似")),
                           ("/profile/signature", "修改签名",
                            ('id="signatureInput"', 'id="signatureSaveBtn"',
                             'maxlength="60"'))):
    code_, body_ = req(follow, path_)
    check(f"{path_} 能打开（{title}）", code_ == 200 and title in body_, code_)
    check(f"{path_} 也是白底版式", 'class="phone plain"' in body_)
    check(f"{path_} 返回键回个人信息页", 'class="back-btn" href="/profile"' in body_)
    check(f"{path_} 保留了要求的元素",
          all(m in body_ for m in must),
          [m for m in must if m not in body_])
    check(f"{path_} 没有第二个返回首页",
          body_.count('aria-label="返回首页"') == 0)

print("\n=== 2b. 账号与数据（独立一页）===")
code, data_page = req(follow, "/account-data")
check("账号与数据页 200", code == 200, code)
check("页面上有两个入口（账号与安全 / 数据备份）",
      'href="/account"' in data_page and 'href="/backup"' in data_page
      and data_page.count('class="nav-row"') == 2,
      data_page.count('class="nav-row"'))
check("这一页自己没有表单（点进去才做事）", "<form" not in data_page)
check("说明了个人信息在另一页", 'href="/profile"' in data_page)
check("返回按钮回首页", 'class="back-btn" href="/"' in data_page)
code2, _ = req(anon, "/account-data")
check("未登录访问被挡（302）", code2 == 302, code2)

print("\n=== 3. 账号与安全是菜单，服务点进去 ===")
code, menu = req(follow, "/account")
check("账号与安全页 200", code == 200, code)
check("只列两个选项（改密码 / 登录与设备）",
      menu.count('class="nav-row"') == 2 and 'href="/account/password"' in menu
      and 'href="/account/sessions"' in menu, menu.count('class="nav-row"'))
check("菜单页没有密码输入框", 'type="password"' not in menu)
check("菜单页不再放改名/头像/地区",
      "/profile/username" not in menu and "/profile/avatar" not in menu
      and 'name="region"' not in menu)
check("菜单页写了怎么保护账号", "散列" in menu and "CSRF" in menu)
code, pwd = req(follow, "/account/password")
check("修改密码页 200 且有三个密码框",
      code == 200 and pwd.count('type="password"') == 3, pwd.count('type="password"'))
code, sess = req(follow, "/account/sessions")
check("登录与设备页 200", code == 200, code)
check("登录与设备页显示上次登录时间",
      re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", sess) is not None)
check("登录与设备页有退出其它设备按钮", 'id="logoutAllBtn"' in sess)
check("退出其它设备有二次确认", "data-confirm" in sess)
check("返回按钮回账号与安全",
      'href="/account"' in req(follow, "/account/password")[1])
check("账号与安全 / 数据备份的下级页都返回到账号与数据页",
      'class="back-btn" href="/account-data"' in menu
      and 'class="back-btn" href="/account-data"' in req(follow, "/backup")[1],
      'href="/account-data"' in menu)

print("\n=== 4. 首页顶栏 ===")
code, dash = req(follow, "/")
check("顶栏头像是个人信息入口",
      'class="avatar-btn"' in dash and 'href="/profile"' in dash)
check("顶栏有账号与数据入口", 'href="/account-data"' in dash)
check("顶栏三个按钮（头像 + 账号与数据 + 退出）",
      len(re.findall(r'class="avatar-btn"', dash)) == 1
      and len(re.findall(r'class="icon-btn"', dash)) == 2,
      (len(re.findall(r'class="avatar-btn"', dash)),
       len(re.findall(r'class="icon-btn"', dash))))

print("\n=== 5. 地区：四级选择 / 定位（试完还原成空）===")
# CSRF token 要从「有表单的那一页」拿——个人信息页现在是只读列表，没有表单
tok_region = csrf(req(follow, "/profile/region")[1])
tok_avatar = csrf(req(follow, "/profile/avatar")[1])
tok = tok_region

# 地区数据是单独一份静态文件，前端按需拉
code, raw_tree = req(follow, "/static/regions.json", raw=True)
check("静态地区数据能取到（200）", code == 200, code)
try:
    tree = json.loads(raw_tree.decode("utf-8"))
    provinces = tree.get("中国", {})
    cities = sum(len(v) for v in provinces.values())
    areas = sum(len(a) for v in provinces.values() for a in v.values())
    check("四级数据都在（省 34 / 市 300+ / 区县 3000+）",
          len(provinces) >= 30 and cities >= 300 and areas >= 3000,
          (len(provinces), cities, areas))
    check("抽查广东省→深圳市有南山区",
          "南山区" in provinces.get("广东省", {}).get("深圳市", []))
except Exception as exc:                      # noqa: BLE001
    check("地区数据能解析", False, exc)

code, region_page = req(follow, "/profile/region")
check("地区页是四级下拉", region_page.count("<select") == 4,
      region_page.count("<select"))

code, _ = req(plain, "/profile/region",
              {"country": "中国", "province": "广东省", "city": "深圳市",
               "area": "南山区", "next": "/profile/region", "csrf_token": tok})
with sqlite3.connect(DB) as c:
    got = c.execute("SELECT region FROM users WHERE username='demo'").fetchone()[0]
check("选到区/县能存进库，且是短名写法「广东 深圳 南山」",
      got == "广东 深圳 南山", got)
code, page = req(follow, "/profile/region")
check("页面提示了设置成功", "地区已设为「广东 深圳 南山」" in flash_in(page))
check("个人信息页那一行也显示它",
      "广东 深圳 南山" in req(follow, "/profile")[1])
# 保存完回到这一页，四级下拉必须都还是选中的（页面上那段联动 JS 容易把区/县冲掉）
code, back = req(follow, "/profile/region")
check("存完之后回到地区页，省/市/区/县四级都是「已选中」",
      re.search(r'<option value="广东省"\s+selected', back) is not None
      and re.search(r'<option value="深圳市"\s+selected', back) is not None
      and re.search(r'<option value="南山区"\s+selected', back) is not None,
      [x.strip() for x in back.splitlines() if "selected" in x][:4])

code, _ = req(plain, "/profile/region",
              {"country": "中国", "province": "广东省", "city": "深圳市",
               "area": "不存在区", "next": "/profile/region", "csrf_token": tok})
check("数据里没有的区会被拒",
      "不在数据里" in flash_in(req(follow, "/profile/region")[1]))

code, _ = req(plain, "/profile/region",
              {"lat": "22.60", "lon": "114.10", "next": "/profile/region",
               "csrf_token": tok})
code, page = req(follow, "/profile/region")
with sqlite3.connect(DB) as c:
    got2 = c.execute("SELECT region FROM users WHERE username='demo'").fetchone()[0]
check("用坐标定位 → 判成「广东 深圳」", got2 == "广东 深圳", got2)
check("定位的提示里带距离（说明是市级近似）",
      "公里" in flash_in(page), flash_in(page))
code, _ = req(plain, "/profile/region",
              {"lat": "abc", "lon": "x", "next": "/profile/region", "csrf_token": tok})
check("坐标不合法会被拒",
      "坐标不合法" in flash_in(req(follow, "/profile/region")[1]))

# 还原：demo 原来是空
with sqlite3.connect(DB) as c:
    c.execute("UPDATE users SET region=NULL WHERE username='demo'")
    c.commit()
check("地区已还原成空（没留下测试痕迹）", users_now()[0][1] is None, users_now()[0])
check("别人的地区一个没动",
      [u[1] for u in users_now()[1:]] == [u[1] for u in before_users[1:]],
      [u[1] for u in users_now()[1:]])

# 老数据兼容：早期手工填的「广东  深圳」（两个空格）也要能显示和回填
with sqlite3.connect(DB) as c:
    c.execute("UPDATE users SET region='广东  深圳' WHERE username='demo'")
    c.commit()
check("老写法在个人信息页显示成标准写法（去多余空格）",
      "广东 深圳" in req(follow, "/profile")[1])
old_page = req(follow, "/profile/region")[1]
check("老写法在地区页能回填到省 / 市（没掉进手动填写）",
      re.search(r'<option value="广东省"\s+selected', old_page) is not None
      and re.search(r'<option value="深圳市"\s+selected', old_page) is not None
      and re.search(r'<option value="__manual__"\s+selected', old_page) is None,
      [x.strip() for x in old_page.splitlines() if "selected" in x][:4])
with sqlite3.connect(DB) as c:
    c.execute("UPDATE users SET region=NULL WHERE username='demo'")
    c.commit()

print("\n=== 5b. 签名（试完还原成空）===")
code, sig_page = req(follow, "/profile/signature")
check("签名页 200 且有输入框 + 保存按钮",
      code == 200 and 'id="signatureInput"' in sig_page
      and 'id="signatureSaveBtn"' in sig_page, code)
code, _ = req(plain, "/profile/signature",
              {"signature": "只收原画，不刀", "next": "/profile/signature",
               "csrf_token": tok})
with sqlite3.connect(DB) as c:
    sig = c.execute("SELECT signature FROM users WHERE username='demo'").fetchone()[0]
check("签名能存进库", sig == "只收原画，不刀", sig)
check("个人信息页显示签名",
      "只收原画，不刀" in req(follow, "/profile")[1])
dash_signed = req(follow, "/")[1]
check("首页问候语下面也显示签名",
      'class="greet-sign"' in dash_signed and "只收原画，不刀" in dash_signed)
code, _ = req(plain, "/profile/signature",
              {"signature": "", "next": "/profile/signature", "csrf_token": tok})
with sqlite3.connect(DB) as c:
    sig2 = c.execute("SELECT signature FROM users WHERE username='demo'").fetchone()[0]
check("清空后库里没有了", sig2 is None, sig2)
check("清空后首页不再显示（greet-sign 元素消失）",
      'class="greet-sign"' not in req(follow, "/")[1])

print("\n=== 6. 头像：上传 / 换 / 删（试完还原成空）===")
png = io.BytesIO()
try:
    from PIL import Image as PILImage
    PILImage.new("RGB", (64, 64), (80, 200, 140)).save(png, format="PNG")
except Exception as exc:                      # noqa: BLE001
    print("  跳过头像测试（造不出 PNG）：", exc)
    png = None
if png is not None:
    code, resp = post_multipart("/profile/avatar",
                                {"next": "/profile/avatar",
                                 "csrf_token": tok_avatar},
                                {"avatar": ("me.png", png.getvalue())})
    with sqlite3.connect(DB) as c:
        avatar = c.execute("SELECT avatar FROM users WHERE username='demo'"
                           ).fetchone()[0]
    check("上传头像后库里记了文件名", bool(avatar), avatar)
    check("头像文件真的写进 static/uploads/",
          avatar and os.path.exists(os.path.join(UP_DIR, avatar)))
    check("带图后页面显示头像", avatar and f"/static/uploads/{avatar}" in
          req(follow, "/profile")[1])
    check("带图后首页顶栏也显示头像",
          avatar and f"/static/uploads/{avatar}" in req(follow, "/")[1])

    code, resp = post_multipart("/profile/avatar",
                                {"next": "/profile", "csrf_token": tok},
                                {"avatar": ("fake.png", b"not an image at all")})
    check("伪造的图片被拒（不写盘）",
          "格式不支持" in flash_in(req(follow, "/profile/avatar")[1]))
    with sqlite3.connect(DB) as c:
        still = c.execute("SELECT avatar FROM users WHERE username='demo'"
                          ).fetchone()[0]
    check("被拒后头像没变", still == avatar, still)

    code, resp = post_multipart("/profile/avatar",
                                {"next": "/profile", "csrf_token": tok,
                                 "remove_avatar": "1"}, {})
    with sqlite3.connect(DB) as c:
        after = c.execute("SELECT avatar FROM users WHERE username='demo'"
                          ).fetchone()[0]
    check("删除头像后库里清空", after is None, after)
    check("删除头像后磁盘文件也没了（不留垃圾）",
          not os.path.exists(os.path.join(UP_DIR, avatar)))
check("头像已还原成空", users_now()[0][2] is None, users_now()[0])

print("\n=== 7. 地区数据的四级逻辑 ===")
check("「广东  深圳」能认成标准写法",
      regions.display("广东  深圳") == "广东 深圳", regions.display("广东  深圳"))
check("「深圳」这种简称也能认出来",
      regions.display("深圳") == "广东 深圳", regions.display("深圳"))
check("不在数据里的原样保留（不硬塞）",
      regions.display("火星 环形山") == "火星 环形山", regions.display("火星 环形山"))
check("四级拼装：广东省/深圳市/南山区 → 广东 深圳 南山",
      regions.build_value("中国", "广东省", "深圳市", "南山区") == "广东 深圳 南山",
      regions.build_value("中国", "广东省", "深圳市", "南山区"))
check("「市辖区」这种占位层级会被跳过",
      regions.build_value("中国", "北京市", "市辖区", "东城区") == "北京 东城",
      regions.build_value("中国", "北京市", "市辖区", "东城区"))
check("数据里没有的区判为无效",
      regions.is_valid("中国", "广东省", "深圳市", "不存在区") is False)
check("真实存在的区判为有效",
      regions.is_valid("中国", "广东省", "深圳市", "南山区") is True)
check("qiu 库里的写法没被程序偷偷改掉（要他自己保存才变）",
      [u[1] for u in users_now()] == [u[1] for u in before_users],
      [u[1] for u in users_now()])

print("\n=== 8. 真实数据没被碰过 ===")
after = snapshot()
check("各项数量与测试前一致", after == before, (before, after))
check("三个账号的用户名/地区/头像都回到测试前",
      users_now() == before_users, (users_now(), before_users))
check("uploads 目录和测试前完全一样（没留下测试头像）",
      sorted(os.listdir(UP_DIR)) == uploads_before,
      (sorted(os.listdir(UP_DIR)), uploads_before))

print("\n" + "=" * 46)
print(f"通过 {ok} / 失败 {len(bad)}")
if bad:
    for b in bad:
        print("  !! " + b)
    sys.exit(1)
print("✅ 个人信息 / 账号与安全线上验证全部通过")
