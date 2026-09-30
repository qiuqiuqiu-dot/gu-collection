# -*- coding: utf-8 -*-
"""对着真正跑起来的服务器验一遍同城换谷（真实 HTTP + CSRF，只用标准库）。
原则：只增删自己造的数据。线上库里 pre-existing 的信息一条都不改
（demo 的地区会被临时设上，最后还原）。
"""
import http.cookiejar as cookiejar
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from init_db import SEED_EXCHANGES  # noqa: E402

# 仓库根目录：这样从任何位置运行都能找到 instance/ 和 static/
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BASE = "http://127.0.0.1:5000"
DB = os.path.join(ROOT_DIR, "instance", "gu.db")
VIEWER = ("swaptester", "swaptest123")   # 临时谷友账号，用完删掉
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


def make_session():
    jar = cookiejar.CookieJar()
    return (urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)),
            urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar),
                                        NoRedirect))


def req(opener, path, data=None):
    """返回 (状态码, 正文)；4xx 也当结果返回，不抛异常。"""
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


def login(username, password, path="/login"):
    follow, plain = make_session()
    _, html = req(follow, path)
    code, html = req(follow, path, {"username": username, "password": password,
                                    "csrf_token": csrf(html)})
    return follow, plain, code, html


def blocks(html):
    """按行的开头标记切块。不能直接找标题——flash 提示里也会有标题。"""
    parts = html.split('<div class="side-item')
    return ['<div class="side-item' + p for p in parts[1:]]


def row_of(html, title):
    for b in blocks(html):
        if title in b:
            return b
    return ""


def is_board(html):
    """登录后才有「我的地区」这一栏，用它区分真页面和登录页。"""
    return "我的地区" in html


def rows_db():
    with sqlite3.connect(DB) as c:
        return list(c.execute(
            "SELECT id, user_id, emoji, title, want, distance_km, region, place,"
            " contact, note, color_start, color_end FROM exchanges ORDER BY id"))


seed_rows = [(i, 1, e, t, w, d, None, None, None, None, c1, c2)
             for i, (e, c1, c2, t, w, d, _p, _c, _n)
             in enumerate(SEED_EXCHANGES, start=1)]
before = rows_db()
demo_region_before = sqlite3.connect(DB).execute(
    "SELECT region FROM users WHERE username='demo'").fetchone()[0]

print("=== 0. 起手：线上库的老数据 = 种子原值 ===")
check("测试前 3 条老信息，值就是 init_db 的种子值", before == seed_rows, before)
check("测试前 demo 没设地区", demo_region_before is None, demo_region_before)

follow, plain, code, html = login("demo", "demo123")
check("demo 登录成功（状态 200）", code == 200, code)

# 换谷开关关着的时候整段跳过：/exchanges 会直接 404，
# 这时的用例跑下去只会全红，不如明确说一句「当前没开」。
probe_code, _ = req(plain, "/exchanges")
if probe_code == 404:
    print("\n⏭  同城换谷功能当前是关闭的（routes.DEFAULT_EXCHANGE_ENABLED=False），"
          "跳过线上验证。\n"
          "   功能本身的用例在 smoke_test.py 第 19 节，那边是开着开关跑的。")
    sys.exit(0)

print("\n=== 1. 老信息（region 是 NULL）显示 ===")
code, html = req(follow, "/exchanges")
check("GET /exchanges 200（修前这里是 500）", code == 200, code)
check("确实是换谷板（不是被踢回登录页）", is_board(html), html[:150])
check("3 条老信息都渲染出来了", len(blocks(html)) == 3, len(blocks(html)))
check("3 条都是「我发的」", sum("我发的" in b for b in blocks(html)) == 3,
      sum("我发的" in b for b in blocks(html)))
r1 = row_of(html, "求换 星野吧唧")
check("第一条显示可换内容", "白兔立牌" in r1, r1[:200])
check("第一条显示距离", "1.2km" in r1, r1[:200])
check("第一条有编辑/撤下按钮",
      "/exchanges/1/edit" in r1 and "/exchanges/1/delete" in r1, r1[:300])

print("\n=== 2. 设置地区 ===")
code, _ = req(plain, "/profile/region",
              {"region": "上海 徐汇", "csrf_token": csrf(html)})
check("设置地区 302", code == 302, code)
check("地区存进库了", sqlite3.connect(DB).execute(
    "SELECT region FROM users WHERE username='demo'").fetchone()[0] == "上海 徐汇")
code, html = req(follow, "/exchanges")
check("地区栏回显当前地区", 'value="上海 徐汇"' in html)

print("\n=== 3. 发布一条求换（完整字段） ===")
code, _ = req(plain, "/exchanges/add", {
    "title": "换 白兔吧唧", "want": "想要 某某色纸", "place": "徐家汇地铁站",
    "contact": "QQ 123456", "note": "周末面交", "emoji": "🐰",
    "distance_km": "2.5", "csrf_token": csrf(html),
})
check("发布 302", code == 302, code)
with sqlite3.connect(DB) as c:
    row = c.execute("SELECT id, title, region, place, contact, note, distance_km,"
                    " created_at, user_id FROM exchanges ORDER BY id DESC LIMIT 1"
                    ).fetchone()
new_id = row[0]
check("新信息入库且是我的", row[1] == "换 白兔吧唧" and row[8] == 1, row)
check("地区按发布人自动带上", row[2] == "上海 徐汇", row[2])
check("面交地点存了", row[3] == "徐家汇地铁站", row[3])
check("联系方式存了", row[4] == "QQ 123456", row[4])
check("说明存了", row[5] == "周末面交", row[5])
check("新信息有 created_at（就是刚才漏补的那一列）", row[7] is not None, row[7])

code, html = req(follow, "/exchanges")
check("板上变成 4 条", len(blocks(html)) == 4, len(blocks(html)))
mine = row_of(html, "换 白兔吧唧")
check("自己那条显示「我发的」", "我发的" in mine, mine[:200])
check("自己那条排在第一条（我发的优先）", "换 白兔吧唧" in blocks(html)[0],
      blocks(html)[0][:120])
check("自己那条第二行带地区", "上海 徐汇" in mine, mine[:300])
check("自己那条显示可换内容", "想要 某某色纸" in mine, mine[:300])
check("自己那条显示面交地点", "徐家汇地铁站" in mine)
check("自己那条显示联系方式", "QQ 123456" in mine)
check("自己那条显示说明", "周末面交" in mine)
check("自己那条有编辑/撤下按钮",
      f"/exchanges/{new_id}/edit" in mine and f"/exchanges/{new_id}/delete" in mine,
      mine[:400])

print("\n=== 4. 编辑 ===")
code, _ = req(plain, f"/exchanges/{new_id}/edit", {
    "title": "换 白兔吧唧（改）", "want": "想要 台风眼色纸", "place": "人民广场",
    "contact": "wx: abcd", "note": "改过了", "emoji": "🐰",
    "distance_km": "1.0", "csrf_token": csrf(html),
})
check("编辑 302", code == 302, code)
with sqlite3.connect(DB) as c:
    row = c.execute("SELECT title, want, place, contact, note, region, distance_km"
                    " FROM exchanges WHERE id=?", (new_id,)).fetchone()
check("标题和可换改了", row[0] == "换 白兔吧唧（改）" and row[1] == "想要 台风眼色纸",
      row)
check("面交地点改了", row[2] == "人民广场", row[2])
check("联系方式改了", row[3] == "wx: abcd", row[3])
check("说明改了", row[4] == "改过了", row[4])
check("距离改了", row[6] == 1.0, row[6])
check("地区不变", row[5] == "上海 徐汇", row[5])
code, html = req(follow, "/exchanges")
check("页面同步显示改后的内容", "换 白兔吧唧（改）" in html)
check("首页换谷板块也能看到", "换 白兔吧唧（改）" in req(follow, "/")[1])

print("\n=== 5. 谷友视角（临时账号，没设地区） ===")
f2, p2, code, html2 = login(*VIEWER, path="/register")
code, html2 = req(f2, "/exchanges")
check("临时谷友注册并进了换谷板", code == 200 and is_board(html2), code)
check("谷友看到 4 条（3 条老信息 + 我新发的）", len(blocks(html2)) == 4,
      len(blocks(html2)))
check("谷友看到我新发的信息", "换 白兔吧唧（改）" in html2)
q_row = row_of(html2, "换 白兔吧唧（改）")
check("谷友看我那条带地区名", "上海 徐汇" in q_row, q_row[:300])
check("谷友看老信息显示「未填地区」", html2.count("未填地区") == 3,
      html2.count("未填地区"))
check("「未填地区」没串到我那条上", "未填地区" not in q_row, q_row[:300])
check("谷友那页没有「我发的」标签", "我发的" not in html2.replace("我发的最上面", ""))
check("谷友看不到我的撤下按钮", f"/exchanges/{new_id}/delete" not in html2)
check("谷友看不到我的编辑按钮", f"/exchanges/{new_id}/edit" not in html2)
code, _ = req(p2, f"/exchanges/{new_id}/delete", {"csrf_token": csrf(html2)})
check("谷友撤下我的信息 404", code == 404, code)
code, _ = req(p2, f"/exchanges/{new_id}/edit",
              {"title": "抢改", "csrf_token": csrf(html2)})
check("谷友编辑我的信息 404", code == 404, code)
code, _ = req(p2, "/exchanges/1/delete", {"csrf_token": csrf(html2)})
check("谷友撤下老信息 404", code == 404, code)
check("我的信息没被别人动过", sqlite3.connect(DB).execute(
    "SELECT title FROM exchanges WHERE id=?", (new_id,)).fetchone()[0]
    == "换 白兔吧唧（改）")
check("老信息也没被动", rows_db()[0] == seed_rows[0], rows_db()[0])

print("\n=== 6. 老信息（没地区的）编辑一次会自动补上地区 ===")
code, _ = req(plain, "/exchanges/add", {
    "title": "换 抹茶立牌", "want": "想要 樱花立牌", "place": "静安寺",
    "contact": "QQ 999", "note": "", "emoji": "🍵",
    "distance_km": "3", "csrf_token": csrf(html),
})
with sqlite3.connect(DB) as c:
    heal_id = c.execute("SELECT MAX(id) FROM exchanges").fetchone()[0]
    # 模拟「加地区字段之前」的老数据：把地区抹成 NULL
    c.execute("UPDATE exchanges SET region=NULL WHERE id=?", (heal_id,))
    c.commit()
check("造出一条没地区的信息（模拟老数据）", sqlite3.connect(DB).execute(
    "SELECT region FROM exchanges WHERE id=?", (heal_id,)).fetchone()[0] is None)
code, html = req(follow, "/exchanges")
check("没地区也能显示，不崩", code == 200 and "换 抹茶立牌" in html)
code, _ = req(plain, f"/exchanges/{heal_id}/edit", {
    "title": "换 抹茶立牌", "want": "想要 樱花立牌", "place": "静安寺",
    "contact": "QQ 999", "note": "", "emoji": "🍵",
    "distance_km": "3", "csrf_token": csrf(html),
})
check("编辑这条 302", code == 302, code)
check("编辑后补上了地区", sqlite3.connect(DB).execute(
    "SELECT region FROM exchanges WHERE id=?", (heal_id,)).fetchone()[0]
    == "上海 徐汇")

print("\n=== 7. 撤下（清掉两条测试数据） ===")
code, html = req(follow, "/exchanges")
for xid in (new_id, heal_id):
    code, _ = req(plain, f"/exchanges/{xid}/delete", {"csrf_token": csrf(html)})
    check(f"撤下 {xid} 返回 302", code == 302, code)
check("两条测试数据都没了", sorted(r[0] for r in rows_db()) == [1, 2, 3],
      [r[0] for r in rows_db()])
code, html = req(follow, "/exchanges")
check("板上回到 3 条", len(blocks(html)) == 3, len(blocks(html)))

print("\n=== 8. 还原现场 ===")
with sqlite3.connect(DB) as c:
    c.execute("DELETE FROM users WHERE username=?", (VIEWER[0],))
    c.execute("UPDATE users SET region=? WHERE username='demo'", (demo_region_before,))
    c.commit()
check("老信息一条没变（还是种子原值）", rows_db() == seed_rows, rows_db())
check("demo 的地区还原", sqlite3.connect(DB).execute(
    "SELECT region FROM users WHERE username='demo'").fetchone()[0]
    == demo_region_before)
check("临时谷友账号已删除", sqlite3.connect(DB).execute(
    "SELECT COUNT(*) FROM users WHERE username=?", (VIEWER[0],)).fetchone()[0] == 0)
check("账号还是 demo / qiu 两个", sorted(r[0] for r in sqlite3.connect(DB).execute(
    "SELECT username FROM users")) == ["demo", "qiu"])

print("\n" + "=" * 46)
print(f"通过 {ok} / 失败 {len(bad)}")
if bad:
    for b in bad:
        print("  !! " + b)
    sys.exit(1)
print("✅ 同城换谷线上验证全部通过")
