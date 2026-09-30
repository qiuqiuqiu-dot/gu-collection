# -*- coding: utf-8 -*-
"""线上验证：数据备份（导出 / 自动备份 / 权限），对真实数据只读。
唯一会"写"的是程序自己启动时的自动备份（这本来就是功能的一部分）。
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
import zipfile

# 仓库根目录：这样从任何位置运行都能找到 instance/ 和 static/
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BASE = "http://127.0.0.1:5000"
DB = os.path.join(ROOT_DIR, "instance", "gu.db")
BK_DIR = os.path.join(ROOT_DIR, "instance", "backups")
BEFORE = None
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


def snapshot(path):
    with sqlite3.connect(path) as c:
        return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("users", "gu_items", "categories", "reminders", "exchanges")}


def mine(table, where="user_id=1"):
    """demo（user_id=1）自己的数量——页面和导出都是按账号算的，不是全库。"""
    with sqlite3.connect(DB) as c:
        return c.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


jar = cookiejar.CookieJar()
follow = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
plain = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), NoRedirect)


def req(opener, path, data=None, raw=False):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    try:
        with opener.open(urllib.request.Request(BASE + path, data=body),
                         timeout=30) as resp:
            payload = resp.read()
            return resp.status, payload if raw else payload.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        payload = e.read()
        return e.code, payload if raw else payload.decode("utf-8", "replace")


def csrf(html):
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1) if m else None


before = snapshot(DB)
with sqlite3.connect(DB) as _c:
    before_user = _c.execute("SELECT username, password_hash, region, created_at"
                             " FROM users WHERE id=1").fetchone()
print("测试前:", before)

code, html = req(follow, "/login")
code, html = req(follow, "/login", {"username": "demo", "password": "demo123",
                                    "csrf_token": csrf(html)})
check("demo 登录成功", code == 200, code)

print("\n=== 1. 启动时自动备份 ===")
files = sorted(os.listdir(BK_DIR)) if os.path.isdir(BK_DIR) else []
check("instance/backups/ 里有备份文件", bool(files), files)
newest = os.path.join(BK_DIR, files[-1]) if files else None
if newest:
    with open(newest, "rb") as f:
        head = f.read(16)
    check("最新的备份是完整 SQLite 库", head == b"SQLite format 3\x00", head)
    snap = snapshot(newest)
    check("备份里含真实数据（和当前库一致）", snap == before, (snap, before))
check("备份目录不在代码目录之外乱放",
      os.path.abspath(BK_DIR) == os.path.abspath(os.path.join("instance", "backups")))

print("\n=== 2. 备份页 ===")
code, page = req(follow, "/backup")
check("备份页 200", code == 200, code)
check("页面显示的是「我这个账号」的谷子数",
      f"{mine('gu_items')} 件谷子" in page, f"期望 {mine('gu_items')} 件谷子")
check("有导出按钮", 'href="/backup/export"' in page)
check("有导入表单（multipart）",
      'action="/backup/import"' in page and "multipart/form-data" in page)
check("列出了自动备份文件", files and files[-1] in page, files[-3:])
check("主账号有下载按钮", files and f"/backup/download/{files[-1]}" in page)
anon_plain = urllib.request.build_opener(NoRedirect)   # 无 cookie，且不跟随跳转
code, _ = req(anon_plain, "/backup")
check("未登录访问备份页被挡（跳登录 302）", code == 302, code)
code, _ = req(anon_plain, "/backup/export")
check("未登录也导不出数据", code == 302, code)

print("\n=== 3. 导出真实数据 ===")
code, blob = req(follow, "/backup/export", raw=True)
check("导出 200 且是 zip", code == 200 and blob[:2] == b"PK", code)
zf = zipfile.ZipFile(io.BytesIO(blob))
names = zf.namelist()
raw_json = zf.read("data.json").decode("utf-8")
data = json.loads(raw_json)
check("压缩包里有 data.json", "data.json" in names)
check("导出件数 = 我账号里的件数",
      len(data["items"]) == mine("gu_items"),
      (len(data["items"]), mine("gu_items")))
check("导出提醒数 = 我账号里的提醒数",
      len(data["reminders"]) == mine("reminders"),
      (len(data["reminders"]), mine("reminders")))
check("导出分类数 = 我账号里的分类数",
      len(data["categories"]) == mine("categories"),
      (len(data["categories"]), mine("categories")))
photo_item_count = mine("gu_items", "user_id=1 AND image IS NOT NULL")
photos = [n for n in names if n.startswith("photos/")]
check("我带照片的谷子数量 = 打包的照片数量",
      len(photos) == photo_item_count, (len(photos), photo_item_count))
check("照片文件名和记录对得上",
      all(i["image"] is None or f"photos/{i['image']}" in names for i in data["items"]))
check("导出里没有密码散列",
      not any(k in raw_json for k in ("pbkdf2", "scrypt", "password_hash")))
check("只导出了自己的数据（不含别的账号的谷子）",
      all(i["name"] in {r[0] for r in sqlite3.connect(DB).execute(
          "SELECT name FROM gu_items WHERE user_id=1")} for i in data["items"]))
with sqlite3.connect(DB) as c:
    other = {r[0] for r in c.execute("SELECT name FROM gu_items WHERE user_id<>1")}
check("别的账号的谷子确实不在导出里",
      not any(n in raw_json for n in other), other)

print("\n=== 4. 真实数据没被这次改动碰到 ===")
after = snapshot(DB)
check("各项数量与测试前完全一致", after == before, (before, after))
# 不再做整文件字节比对：登录现在会（正当地）写一次 last_login_at。
# 改为逐字段确认「账号本身」没被动，只有该写的元数据变了。
with sqlite3.connect(DB) as c:
    now_user = c.execute("SELECT username, password_hash, region, created_at"
                         " FROM users WHERE id=1").fetchone()
check("demo 的用户名/密码散列/地区/注册时间都没被改",
      now_user == before_user, (now_user[:1] + now_user[2:], before_user[:1] + before_user[2:]))

print("\n" + "=" * 46)
print(f"通过 {ok} / 失败 {len(bad)}")
if bad:
    for b in bad:
        print("  !! " + b)
    sys.exit(1)
print("✅ 数据备份功能线上验证全部通过")
