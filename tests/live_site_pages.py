# -*- coding: utf-8 -*-
"""线上验证：统一的 + 号（自己选添加什么）。
只增删自己造的数据：临时加一件在途谷子 → 改个名 → 删掉，最后确认库里没留下东西。
"""
import os
import http.cookiejar as cookiejar
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request

# 仓库根目录：这样从任何位置运行都能找到 instance/ 和 static/
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BASE = "http://127.0.0.1:5000"
DB = os.path.join(ROOT_DIR, "instance", "gu.db")
PAGES = ["/", "/items", "/transit", "/sold", "/wishlist", "/reminders"]
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


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


jar = cookiejar.CookieJar()
follow = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
plain = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), NoRedirect)


def req(opener, path, data=None):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    try:
        with opener.open(urllib.request.Request(BASE + path, data=body),
                         timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def csrf(html):
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1) if m else None


def post_loc(path, data):
    """POST 并返回 (状态码, Location)——用来验「回到哪个页面」。"""
    body = urllib.parse.urlencode(data).encode()
    try:
        with plain.open(urllib.request.Request(BASE + path, data=body),
                        timeout=15) as r:
            return r.status, r.headers.get("Location", "")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Location", "")


with sqlite3.connect(DB) as c:
    items_before = {r[0]: r[1] for r in c.execute("SELECT id, name FROM gu_items")}
    cats = [r[0] for r in c.execute(
        "SELECT name FROM categories WHERE user_id=1 ORDER BY id")]

code, html = req(follow, "/login")
code, html = req(follow, "/login", {"username": "demo", "password": "demo123",
                                   "csrf_token": csrf(html)})
check("demo 登录成功", code == 200, code)

print("\n=== 1. 每个页面都是同一个「+」号入口 ===")
pages_html = {}
for path in PAGES:
    code, h = req(follow, path)
    pages_html[path] = h
    check(f"{path} 200", code == 200, code)
check("所有页面都只有一个 + 号",
      all(h.count('class="fab"') == 1 for h in pages_html.values()),
      {p: h.count('class="fab"') for p, h in pages_html.items()})
check("所有页面的 + 号都打开选择面板",
      all('data-modal-open="addSheet"' in h for h in pages_html.values()))
check("+ 号不再直接绑死「添加谷子」",
      all('data-modal-open="addModal"' not in h for h in pages_html.values()))
check("所有页面都有五种可选的添加 + 分类管理",
      all(all(k in h for k in ('data-add-kind="displaying"', 'data-add-kind="in_transit"',
                               'data-add-kind="sold"', 'data-add-kind="wishlist"',
                               'data-reminder-mode="add"'))
          for h in pages_html.values()))
check("换谷已关掉：所有页面都没有换谷入口和换谷弹窗",
      all('data-exchange-mode="add"' not in h and 'id="exchangeModal"' not in h
          for h in pages_html.values()),
      [p for p, h in pages_html.items()
       if 'data-exchange-mode="add"' in h or 'id="exchangeModal"' in h])
for mid in ("addSheet", "addModal", "reminderModal", "catModal"):
    dup = {p: h.count(f'id="{mid}"') for p, h in pages_html.items()
           if h.count(f'id="{mid}"') != 1}
    check(f"每页有且只有一个 {mid}（没有重复 include）", not dup, dup)

# 面板里也能直接进分类管理（只看面板那一块，别把页面别处的入口算进来）
def sheet_of(html):
    return html.split('id="addSheet"')[1].split('id="addModal"')[0]


check("+ 面板里有分类管理入口",
      'data-modal-open="catModal"' in sheet_of(pages_html["/"])
      and "分类管理" in sheet_of(pages_html["/"]))
check("每个页面的面板里都有分类管理入口",
      all('data-modal-open="catModal"' in sheet_of(h) for h in pages_html.values()),
      [p for p, h in pages_html.items()
       if 'data-modal-open="catModal"' not in sheet_of(h)])

print("\n=== 2. 选项在没显式传参的页面也有（真实分类） ===")
check("在途页的添加表单也带分类下拉",
      'data-cat-select="type"' in pages_html["/transit"]
      and 'data-cat-select="ip"' in pages_html["/transit"])
check("在途页的提醒弹窗也有重要程度选项", 'id="remPriority"' in pages_html["/transit"])
if cats:
    missing = [c for c in cats if c not in pages_html["/transit"]]
    check(f"真实的分类（{len(cats)} 个）都出现在在途页的下拉里", not missing, missing)
else:
    print("  (demo 还没有分类，跳过这条)")

print("\n=== 3. 从在途页添加 → 回到在途页 ===")
check("添加表单带 next（回到原页面）",
      'name="next" value="/transit' in pages_html["/transit"])
code, loc = post_loc("/items/add", {
    "name": "临时测试的在途", "emoji": "🧪", "count": "1", "status": "in_transit",
    "expect_date": "2026-12-31", "channel": "taobao", "next": "/transit",
    "csrf_token": csrf(pages_html["/transit"]),
})
check("添加后 302 回到 /transit", code == 302 and loc.startswith("/transit"),
      f"{code} {loc}")
with sqlite3.connect(DB) as c:
    row = c.execute("SELECT id, name, status, expect_date FROM gu_items"
                    " WHERE name='临时测试的在途'").fetchone()
check("新谷子入库且状态是在途", row is not None and row[2] == "in_transit", row)
temp_id = row[0]
code, h = req(follow, "/transit")
check("在途页能看到刚加的这件", "临时测试的在途" in h)

print("\n=== 4. 详情弹窗里改一下 → 也回到在途页 ===")
code, loc = post_loc(f"/items/{temp_id}/edit", {
    "name": "临时测试的在途（改）", "emoji": "🧪", "count": "1",
    "status": "in_transit", "expect_date": "2026-12-31", "next": "/transit",
    "csrf_token": csrf(h),
})
check("编辑 302 且也回到 /transit", code == 302 and loc.startswith("/transit"),
      f"{code} {loc}")
with sqlite3.connect(DB) as c:
    name_now = c.execute("SELECT name FROM gu_items WHERE id=?",
                         (temp_id,)).fetchone()[0]
check("改动生效", name_now == "临时测试的在途（改）", name_now)

print("\n=== 5. 清掉测试数据 ===")
code, h = req(follow, "/transit")
code, loc = post_loc(f"/items/{temp_id}/delete", {"csrf_token": csrf(h),
                                                 "next": "/transit"})
check("删除 302", code == 302, code)
with sqlite3.connect(DB) as c:
    items_after = {r[0]: r[1] for r in c.execute("SELECT id, name FROM gu_items")}
check("测试谷子已删除", temp_id not in items_after, items_after.keys())
check("其余谷子一件没变", items_after == items_before,
      {k: (items_before.get(k), v) for k, v in items_after.items()
       if items_before.get(k) != v})

print("\n=== 6. 分类管理入口够不够显眼 ===")
code, h = req(follow, "/items")
check("谷柜页的分类管理换成了按钮（旧的小灰字没了）",
      'class="cat-manage"' in h and 'class="seg-more"' not in h)
check("按钮带图标和文字", "<svg" in h and "分类管理" in h)
code, dash = req(follow, "/")
check("首页不再有这个按钮了（改用 + 面板）",
      'class="cat-manage"' not in dash and 'class="sec-manage"' not in dash)
check("首页点 + 号仍能进分类管理（面板里有入口）",
      "分类管理" in dash.split('id="addSheet"')[1].split('id="addModal"')[0])
check("添加表单里的分类管理也做成小胶囊", 'class="cat-link"' in h)
with sqlite3.connect(DB) as c:
    real_cats = c.execute("SELECT COUNT(*) FROM categories WHERE user_id=1").fetchone()[0]
m = re.search(r'class="cat-manage".*?<b>(\d+)</b>', h, re.S)
check(f"角标数量 = 库里真实分类数（{real_cats}）",
      m is not None and int(m.group(1)) == real_cats,
      m.group(1) if m else "没找到角标")

print("\n=== 7. 同城换谷：线上确认已关掉，数据还在 ===")
code, h404 = req(follow, "/exchanges")
check("GET /exchanges 返回 404", code == 404, code)
code, _ = req(plain, "/exchanges/add", {"title": "偷发", "csrf_token": csrf(pages_html["/"])})
check("POST /exchanges/add 也 404", code == 404, code)
code, dash7 = req(follow, "/")
check("首页看不到换谷板块",
      "同城换谷" not in dash7 and 'id="exchangeLink"' not in dash7)
check("首页 + 面板里也没有换谷", 'data-exchange-mode="add"' not in dash7)
with sqlite3.connect(DB) as c:
    ex_rows = list(c.execute("SELECT id, title, want, region FROM exchanges ORDER BY id"))
check("库里的换谷数据一条没少（只是不显示，随时能开回来）",
      len(ex_rows) == 3, ex_rows)
check("换谷数据字段也没被改",
      ex_rows == [(1, "求换 星野吧唧", "白兔立牌", None),
                  (2, "求换 樱花色纸", "抹茶挂件", None),
                  (3, "求换 蝴蝶徽章", "星野吧唧", None)], ex_rows)

print("\n=== 8. 返回首页按钮 + 统计块可点（线上真机） ===")
code, dash8 = req(follow, "/")
for sid, href, label in [("statDisplaying", "/items", "展示中"),
                         ("statInTransit", "/transit", "在途"),
                         ("statSold", "/sold", "已出"),
                         ("statWishlist", "/wishlist", "心愿单")]:
    check(f"统计块「{label}」链到 {href}",
          f'id="{sid}" href="{href}"' in dash8,
          [line.strip() for line in dash8.splitlines() if sid in line][:1])
    code, page = req(follow, href)
    check(f"从「{label}」点进去 {href} 能打开（且有返回首页入口）",
          code == 200 and 'class="back-btn"' in page, code)

check("五个专区页顶栏都有带文字的返回首页按钮",
      all('class="back-btn"' in pages_html[p] and "<span>返回首页</span>" in pages_html[p]
          for p in ("/items", "/transit", "/sold", "/wishlist", "/reminders")),
      [p for p in ("/items", "/transit", "/sold", "/wishlist", "/reminders")
       if 'class="back-btn"' not in pages_html[p]])
check("每页只有一个返回首页入口（页脚那个已去掉）",
      all(pages_html[p].count('aria-label="返回首页"') == 1
          and 'class="back-link"' not in pages_html[p]
          for p in ("/items", "/transit", "/sold", "/wishlist", "/reminders")),
      {p: pages_html[p].count('aria-label="返回首页"')
       for p in ("/items", "/transit", "/sold", "/wishlist", "/reminders")})
check("专区页不再用只有小箭头的 icon-btn 当返回",
      all('class="icon-btn" href="/"' not in pages_html[p]
          for p in ("/items", "/transit", "/sold", "/wishlist", "/reminders")))

print("\n" + "=" * 46)
print(f"通过 {ok} / 失败 {len(bad)}")
if bad:
    for b in bad:
        print("  !! " + b)
    sys.exit(1)
print("✅ 统一添加入口线上验证全部通过")
