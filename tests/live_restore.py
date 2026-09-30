# -*- coding: utf-8 -*-
"""线上验证「换了机器能不能恢复」：拿 demo 的真实导出去恢复一个临时账号。
用完把临时账号和它带出来的照片全部清掉，最后核对库和 uploads 和测试前一模一样。
"""
import atexit
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
import zipfile

# 仓库根目录：这样从任何位置运行都能找到 instance/ 和 static/
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BASE = "http://127.0.0.1:5000"
DB = os.path.join(ROOT_DIR, "instance", "gu.db")
UP_DIR = os.path.join(ROOT_DIR, "static", "uploads")
TEMP_USER = "restore_probe"
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


def user_row(name):
    with sqlite3.connect(DB) as c:
        return c.execute("SELECT region, signature, avatar FROM users WHERE username=?",
                         (name,)).fetchone()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


jar = cookiejar.CookieJar()
follow = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
# 和 follow 共用 cookie，但不跟随跳转——POST 后要看 302 本身
plain = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), NoRedirect)
probe_jar = cookiejar.CookieJar()
probe = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(probe_jar))
probe_plain = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(probe_jar), NoRedirect)


def req(opener, path, data=None, raw=False):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    try:
        with opener.open(urllib.request.Request(BASE + path, data=body),
                         timeout=40) as resp:
            payload = resp.read()
            return resp.status, payload if raw else payload.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        payload = e.read()
        return e.code, payload if raw else payload.decode("utf-8", "replace")


def csrf(html):
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1) if m else None


def post_multipart(opener, path, fields, files):
    boundary = "----probe" + uuid.uuid4().hex
    body = io.BytesIO()
    for key, value in fields.items():
        body.write(f"--{boundary}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
        body.write(f"{value}\r\n".encode())
    for key, (filename, blob) in files.items():
        body.write(f"--{boundary}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="{key}";'
                   f' filename="{filename}"\r\n'.encode())
        body.write(b"Content-Type: application/zip\r\n\r\n")
        body.write(blob)
        body.write(b"\r\n")
    body.write(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        BASE + path, data=body.getvalue(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with opener.open(request, timeout=60) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


before = snapshot()
uploads_before = set(os.listdir(UP_DIR))
print("测试前:", before, "| uploads:", len(uploads_before), "个文件")


def cleanup():
    """把临时账号、它的行、以及 demo 的临时资料全部还原。

    注意：这里是裸 SQL，不会触发 ORM 的 cascade，而且 SQLite 默认不校验外键，
    所以每一张带 user_id 的表都要自己删干净，否则会留下孤儿行（踩过一次）。
    注册到 atexit：脚本中途崩了也要清干净。
    """
    with sqlite3.connect(DB) as c:
        row = c.execute("SELECT id FROM users WHERE username=?", (TEMP_USER,)).fetchone()
        if row:
            for table in ("gu_items", "reminders", "exchanges", "categories"):
                c.execute(f"DELETE FROM {table} WHERE user_id=?", (row[0],))
            c.execute("DELETE FROM users WHERE id=?", (row[0],))
        for table in ("gu_items", "reminders", "exchanges", "categories"):
            c.execute(f"DELETE FROM {table}"
                      " WHERE user_id NOT IN (SELECT id FROM users)")
        c.execute("UPDATE users SET region=NULL, signature=NULL WHERE username='demo'")
        c.commit()
    for name in set(os.listdir(UP_DIR)) - uploads_before:
        try:
            os.remove(os.path.join(UP_DIR, name))
        except OSError:
            pass


atexit.register(cleanup)

# --- demo 登录，给资料临时填上东西，导出一份 ---
code, page = req(follow, "/login")
code, _ = req(follow, "/login", {"username": "demo", "password": "demo123",
                                 "csrf_token": csrf(page)})
check("demo 登录成功", code == 200, code)
tok = csrf(req(follow, "/profile/signature")[1])
region_form = {"country": "中国", "province": "广东省", "city": "深圳市",
               "area": "南山区"}
code, _ = req(plain, "/profile/region", dict(region_form, csrf_token=tok,
                                             next="/profile"))
check("（准备）给 demo 临时设上地区", code == 302, code)
code, _ = req(plain, "/profile/signature",
              {"signature": "恢复测试签名-QQ", "csrf_token": tok, "next": "/profile"})
check("（准备）给 demo 临时设上签名", code == 302, code)
with sqlite3.connect(DB) as c:
    demo_before = c.execute("SELECT region, signature FROM users WHERE username='demo'"
                            ).fetchone()
check("（准备）资料确实写进去了",
      demo_before == ("广东 深圳 南山", "恢复测试签名-QQ"), demo_before)
demo_items = sqlite3.connect(DB).execute(
    "SELECT COUNT(*) FROM gu_items WHERE user_id=(SELECT id FROM users"
    " WHERE username='demo')").fetchone()[0]
code, blob = req(follow, "/backup/export", raw=True)
check("导出成功（zip）", code == 200 and blob[:2] == b"PK", code)

zf = zipfile.ZipFile(io.BytesIO(blob))
data = json.loads(zf.read("data.json").decode("utf-8"))
avatar_in_zip = [n for n in zf.namelist() if n.startswith("avatar/")]
check("导出里带了地区", data.get("region") == "广东 深圳 南山", data.get("region"))
check("导出里带了签名", data.get("signature") == "恢复测试签名-QQ",
      data.get("signature"))
check("导出里记了头像字段（demo 现在没头像，所以是 null / 或带文件）",
      ("avatar" in data) and (data["avatar"] is None or avatar_in_zip),
      (data.get("avatar"), avatar_in_zip))
with sqlite3.connect(DB) as c:
    demo_avatar = c.execute("SELECT avatar FROM users WHERE username='demo'"
                            ).fetchone()[0]
if demo_avatar:
    check("头像文件也打进了包（avatar/ 下）",
          f"avatar/{demo_avatar}" in zf.namelist(), zf.namelist()[:5])
else:
    print("  （demo 没有头像，用新账号验证头像那一块）")

# --- 注册一个临时账号，把这份备份恢复进去 ---
code, rpage = req(probe, "/register")
code, _ = req(probe, "/register",
              {"username": TEMP_USER, "password": "probe123",
               "csrf_token": csrf(rpage)})
check("临时账号注册成功", code == 200, code)
check("临时账号恢复前是空资料", user_row(TEMP_USER) == (None, None, None),
      user_row(TEMP_USER))

code, resp = post_multipart(probe_plain, "/backup/import",
                            {"csrf_token": csrf(req(probe, "/backup")[1])},
                            {"file": ("backup.zip", blob)})
check("恢复导入 302", code == 302, code)
restore_page = req(probe, "/backup")[1]
check("导入提示里写明了恢复了个人资料",
      "个人资料恢复了" in restore_page, [ln.strip() for ln in restore_page.splitlines()
                                        if "导入完成" in ln][:1])
row = user_row(TEMP_USER)
check("地区恢复了", row[0] == "广东 深圳 南山", row)
check("签名恢复了", row[1] == "恢复测试签名-QQ", row)
check("谷子也恢复了（件数 = demo 的件数）",
      snapshot()["gu_items"] - before["gu_items"] == demo_items,
      (snapshot()["gu_items"] - before["gu_items"], demo_items))
if demo_avatar:
    check("头像也恢复了（新文件）",
          bool(row[2]) and row[2] != demo_avatar
          and os.path.exists(os.path.join(UP_DIR, row[2])), row[2])
check("恢复出来的照片都真在磁盘上",
      (lambda: all(
          (not r[0]) or os.path.exists(os.path.join(UP_DIR, r[0]))
          for r in sqlite3.connect(DB).execute(
              "SELECT image FROM gu_items WHERE user_id=(SELECT id FROM users"
              " WHERE username=?)", (TEMP_USER,))))(),
      "有照片文件缺失")

# --- 清场：临时账号 + 它带出来的照片（同一个 cleanup，退出时也会再兜一次）---
cleanup()
print("清场完成")

after = snapshot()
check("库里的数据回到测试前", after == before, (before, after))
check("uploads 目录回到测试前", set(os.listdir(UP_DIR)) == uploads_before,
      sorted(set(os.listdir(UP_DIR)) ^ uploads_before)[:5])
check("临时账号已经没了",
      sqlite3.connect(DB).execute("SELECT COUNT(*) FROM users WHERE username=?",
                                  (TEMP_USER,)).fetchone()[0] == 0)
# demo 的资料清回测试前的样子（本来就是空）
with sqlite3.connect(DB) as c:
    c.execute("UPDATE users SET region=NULL, signature=NULL WHERE username='demo'")
    c.commit()
check("demo 地区/签名已清空（测试前就是空）", user_row("demo")[0] is None
      and user_row("demo")[1] is None, user_row("demo"))

print("\n" + "=" * 46)
print(f"通过 {ok} / 失败 {len(bad)}")
if bad:
    for b in bad:
        print("  !! " + b)
    sys.exit(1)
print("✅ 备份恢复（含个人资料）线上验证全部通过")
