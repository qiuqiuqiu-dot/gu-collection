# -*- coding: utf-8 -*-
"""线上验证：注销账号真的把账号和数据（含磁盘图片）一起删干净。

**绝不动 demo / qiu / xin**：注册一个临时账号，把三道保险、真注销、
以及「别的账号分毫未动」都验一遍，最后把临时账号和它带出来的文件清掉。
清场注册在 atexit 上，脚本中途崩了也不会留垃圾。
"""
import atexit
import http.cookiejar as cookiejar
import io
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

# 仓库根目录：这样从任何位置运行都能找到 instance/ 和 static/
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BASE = "http://127.0.0.1:5000"
DB = os.path.join(ROOT_DIR, "instance", "gu.db")
UP_DIR = os.path.join(ROOT_DIR, "static", "uploads")
TEMP_USER = "del_probe"
TEMP_PASS = "probe123456"
REAL_USERS = ("demo", "qiu", "xin")
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


def counts():
    with sqlite3.connect(DB) as c:
        return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("users", "gu_items", "categories", "reminders", "exchanges")}


def user_row(name):
    with sqlite3.connect(DB) as c:
        return c.execute("SELECT id, region, signature, avatar FROM users"
                         " WHERE username=?", (name,)).fetchone()


def temp_ids():
    row = user_row(TEMP_USER)
    return row[0] if row else None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def make_session():
    jar = cookiejar.CookieJar()
    return (urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)),
            urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar),
                                        NoRedirect))


def req(opener, path, data=None):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    try:
        with opener.open(urllib.request.Request(BASE + path, data=body),
                         timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def post_multipart(opener, path, fields, files):
    boundary = "----del" + uuid.uuid4().hex
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
        with opener.open(request, timeout=40) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def csrf(html):
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1) if m else None


def png_bytes(color=(80, 200, 140)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, format="PNG")
    return buf.getvalue()


before = counts()
uploads_before = set(os.listdir(UP_DIR))
print("测试前:", before, "| uploads:", len(uploads_before), "个文件")
check("真实账号都在（绝不会动它们）",
      all(user_row(u) for u in REAL_USERS), [u for u in REAL_USERS if not user_row(u)])
real_snapshot = {u: user_row(u) for u in REAL_USERS}


def cleanup():
    """删掉临时账号、它的行、它带出来的文件（注册在 atexit 上，崩了也清）。"""
    uid = temp_ids()
    with sqlite3.connect(DB) as c:
        if uid:
            for table in ("gu_items", "reminders", "exchanges", "categories"):
                c.execute(f"DELETE FROM {table} WHERE user_id=?", (uid,))
            c.execute("DELETE FROM users WHERE id=?", (uid,))
        for table in ("gu_items", "reminders", "exchanges", "categories"):
            c.execute(f"DELETE FROM {table}"
                      " WHERE user_id NOT IN (SELECT id FROM users)")
        c.commit()
    for name in set(os.listdir(UP_DIR)) - uploads_before:
        try:
            os.remove(os.path.join(UP_DIR, name))
        except OSError:
            pass


atexit.register(cleanup)

# ---------- 准备：注册临时账号，塞进一点数据 ----------
probe, probe_plain = make_session()
code, page = req(probe, "/register")
code, _ = req(probe, "/register", {"username": TEMP_USER, "password": TEMP_PASS,
                                   "csrf_token": csrf(page)})
check("临时账号注册成功", code == 200, code)
tok = csrf(req(probe, "/profile/avatar")[1])
req(probe_plain, "/profile/signature",
    {"signature": "待注销的签名", "csrf_token": tok, "next": "/profile"})
post_multipart(probe, "/profile/avatar", {"csrf_token": tok, "next": "/profile"},
               {"avatar": ("me.png", png_bytes((200, 90, 90)))})
tok_item = csrf(req(probe, "/items")[1])
post_multipart(probe, "/items/add",
               {"csrf_token": tok_item, "name": "待注销的谷子", "count": "2",
                "status": "displaying", "next": "/items"},
               {"image": ("a.png", png_bytes())})
req(probe_plain, "/categories/add",
    {"kind": "type", "name": "待删分类", "csrf_token": tok_item})
req(probe_plain, "/reminders/add",
    {"title": "待删提醒", "event_time": "2099-01-01T10:00", "priority": "hot",
     "csrf_token": tok_item})

row = user_row(TEMP_USER)
check("临时账号有头像、有签名", row and row[2] == "待注销的签名" and row[3], row)
temp_photo = None
with sqlite3.connect(DB) as c:
    temp_photo = c.execute("SELECT image FROM gu_items WHERE user_id=?",
                           (row[0],)).fetchone()[0]
check("临时账号的谷子带图，且文件已落盘",
      bool(temp_photo) and os.path.exists(os.path.join(UP_DIR, temp_photo)),
      temp_photo)
new_files = set(os.listdir(UP_DIR)) - uploads_before
check("临时账号一共写进来 2 张图（谷子照片 + 头像）", len(new_files) == 2,
      sorted(new_files))

# ---------- 三道保险，缺一不可 ----------
del_page = req(probe, "/account/delete")[1]
check("注销页把要失去的东西列出来了（谷子 1 件 / 照片 2 张）",
      "谷子 <b>1</b> 件" in del_page and "含实物照片 2 张" in del_page,
      [ln.strip() for ln in del_page.splitlines() if "实物照片" in ln][:1])
check("注销页有用户名 / 密码 / 勾选三个控件",
      'id="confirmName"' in del_page and 'id="confirmPassword"' in del_page
      and 'id="confirmAgree"' in del_page)
check("注销按钮是危险样式（红），不是平时的绿按钮",
      'class="danger-btn"' in del_page)

tok_del = csrf(del_page)


def try_delete(**over):
    payload = {"csrf_token": tok_del, "username": TEMP_USER, "password": TEMP_PASS,
               "agree": "yes"}
    payload.update(over)
    code, body = req(probe_plain, "/account/delete", payload)
    return code, body


check("用户名打错 → 被拒", "用户名没对上" in try_delete(username="DEL_PROBE")[1])
check("没勾选 → 被拒", "请先勾选" in try_delete(agree="")[1])
check("密码打错 → 被拒", "当前密码不对" in try_delete(password="wrong-pass")[1])
check("被拒三次之后，账号和数据都还在",
      temp_ids() is not None and os.path.exists(os.path.join(UP_DIR, temp_photo)))

# ---------- 真注销 ----------
code, _ = try_delete()
check("完整三步后跳走（302 回登录页）", code == 302, code)
check("库里查不到这个账号了", temp_ids() is None)
with sqlite3.connect(DB) as c:
    left = {t: c.execute(f"SELECT COUNT(*) FROM {t} WHERE user_id=(SELECT id FROM users"
                         " WHERE username=?)", (TEMP_USER,)).fetchone()[0]
            for t in ("gu_items", "categories", "reminders", "exchanges")}
check("它的谷子 / 分类 / 提醒 / 换谷信息都删干净了",
      all(v == 0 for v in left.values()), left)
check("磁盘上的谷子照片和头像都删了（不留垃圾）",
      not any(os.path.exists(os.path.join(UP_DIR, n)) for n in new_files),
      [n for n in new_files if os.path.exists(os.path.join(UP_DIR, n))])

check("注销后原来的登录态失效（用不跟随跳转的会话看，就是 302）",
      req(probe_plain, "/")[0] == 302, req(probe_plain, "/")[0])
code, body = req(probe_plain, "/login", {"username": TEMP_USER, "password": TEMP_PASS,
                                         "csrf_token": csrf(req(probe, "/login")[1])})
check("用原来的用户名密码也登不上了", "用户名或密码错误" in body, code)

# ---------- 真实账号分毫未动 ----------
after = counts()
real_after = {u: user_row(u) for u in REAL_USERS}
check("真实三个账号的资料一字未改", real_after == real_snapshot,
      [(u, real_snapshot[u], real_after[u]) for u in REAL_USERS
       if real_snapshot[u] != real_after[u]])
check("users 数量和测试前一致", after["users"] == before["users"],
      (before["users"], after["users"]))
check("谷子 / 分类 / 提醒 / 换谷 的数量都回到测试前",
      all(after[t] == before[t] for t in ("gu_items", "categories", "reminders",
                                          "exchanges")),
      {t: (before[t], after[t]) for t in ("gu_items", "categories", "reminders",
                                          "exchanges")})
check("uploads 目录回到测试前（不多不少）",
      set(os.listdir(UP_DIR)) == uploads_before,
      sorted(set(os.listdir(UP_DIR)) ^ uploads_before)[:5])
check("孤儿行 0 条", all(
    sqlite3.connect(DB).execute(
        f"SELECT COUNT(*) FROM {t} WHERE user_id NOT IN (SELECT id FROM users)"
    ).fetchone()[0] == 0
    for t in ("gu_items", "categories", "reminders", "exchanges")))

print("\n" + "=" * 46)
print(f"通过 {ok} / 失败 {len(bad)}")
if bad:
    for b in bad:
        print("  !! " + b)
    sys.exit(1)
print("✅ 注销账号线上验证全部通过")
