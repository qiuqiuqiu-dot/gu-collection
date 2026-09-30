"""初始化数据库并写入种子数据。

注意：会先 drop_all()，现有数据全部清空。
用法：python init_db.py
"""
import sys
from datetime import timedelta

from app import create_app
from models import (Category, Exchange, GuItem, Reminder, User, db,
                    local_now, local_today, utcnow)

SEED_ITEMS = [
    # 名称, emoji, 渐变起, 渐变止, 数量, 状态, 周边品类, 作品IP
    ("白兔 吧唧", "🐰", "#EAF3FF", "#D9E9FF", 12, "displaying", "吧唧", "白兔"),
    ("抹茶 挂件", "🍵", "#EAF8EE", "#D6F0DF", 8, "displaying", "挂件", "抹茶"),
    ("樱花 立牌", "🌸", "#FDEDF4", "#FBDFEA", 15, "displaying", "立牌", "樱花"),
    ("星野 徽章", "⭐", "#FFF7E6", "#FFEDCC", 6, "in_transit", "徽章", "星野"),
    ("星野 色纸", "✨", "#FFF7E6", "#FFEDCC", 2, "in_transit", "色纸", "星野"),
    ("缎带 发饰", "🎀", "#F4EEFF", "#E8DCFF", 9, "sold", "发饰", "缎带"),
    ("蝴蝶 挂饰", "🦋", "#E8F8F6", "#D5F0EC", 4, "wishlist", "挂饰", "蝴蝶"),
]

# 在途示例：名称 -> (预计到货偏移天数, 渠道 key, 单号, 金额)
# 一条还在路上、一条已超期，方便看到两种状态
SEED_TRANSIT = {
    "星野 徽章": (5, "daigou", "SF1234567890", 268.00),
    "星野 色纸": (-3, "pintuan", "YT9988776655", 129.50),
}

# 已出示例：名称 -> (入手价, 成交价, 出手方式, 出手平台, 出手日期偏移)
# 成交价高于入手价，顺便演示「赚了」的绿色标签
SEED_SOLD = {
    "缎带 发饰": (140.00, 168.00, "sell", "xianyu", -10),
}

# 部分已出示例：名称 -> (入手价, 已出件数, 成交价, 出手方式, 出手平台, 出手日期偏移)
# 共 12 件出了 2 件 → 谷柜里显示 ×10，并标注「已出 2 / 共 12」
SEED_PARTIAL = {
    "白兔 吧唧": (18.00, 2, 40.00, "sell", "xianyu", -6),
}

# 心愿单示例：名称 -> (想要程度, 预算, 参考链接或店铺, 备注)
SEED_WISH = {
    "蝴蝶 挂饰": ("hot", 120.00, "闲鱼「蝴蝶社」", "等再贩，别买高仿"),
}

SEED_REMINDERS = [
    # 内容, 说明, 几天后, 优先级, 抢购链接或店铺, 几点
    ("星野系列 亚克力立牌", "官方再贩", 1, "hot",
     "https://example.com/xingye", 10),
    ("白兔 限定吧唧", "预约开启", 6, "normal", "", 12),
    ("抹茶 毛绒挂件", "补货", 13, "mute", "闲鱼「抹茶铺」", 20),
]

# 换谷信息：(emoji, 渐变起, 渐变止, 求换什么, 能换什么, 距离km, 面交地点, 联系方式, 说明)
SEED_EXCHANGES = [
    ("🐰", "#EAF3FF", "#D9E9FF", "求换 星野吧唧", "白兔立牌", 1.2,
     "地铁 2 号线 xx 站内", "微信 gu_01", "只换原画，可当面验货"),
    ("🌸", "#FDEDF4", "#FBDFEA", "求换 樱花色纸", "抹茶挂件", 2.4,
     "周六下午 商场一楼", "闲鱼同号", ""),
    ("🦋", "#E8F8F6", "#D5F0EC", "求换 蝴蝶徽章", "星野吧唧", 3.6,
     "快递互寄也行", "QQ 123456", "不刀"),
]

# 演示同城换谷用的第二个账号：和 demo 同地区，这样换谷板上能看到「同城」标签
SEED_NEIGHBOR = {
    "username": "nene",
    "password": "nene123",
    "region": "上海 徐汇",
    "exchanges": [
        ("🍵", "#EAF8EE", "#D6F0DF", "求换 抹茶吧唧", "樱花立牌", 0.8,
         "小区门口自提", "微信 nene_1", "同城可送到地铁站"),
        ("⭐", "#FFF7E6", "#FFEDCC", "求换 星野立牌", "蝴蝶挂饰", 5.2,
         "", "站内信联系", ""),
    ],
}


def _force_utf8_stdout():
    """Windows 控制台代码页是 936 时，输出里的 emoji 会抛 UnicodeEncodeError。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError, ValueError):
        pass


def seed():
    app = create_app()
    with app.app_context():
        db.drop_all()
        db.create_all()

        user = User(username="demo", region="上海 徐汇")
        user.set_password("demo123")
        db.session.add(user)
        db.session.flush()

        # 两个维度的分类先建好，再挂到谷子上
        cats = {}
        for kind, names in (("type", [i[6] for i in SEED_ITEMS]),
                            ("ip", [i[7] for i in SEED_ITEMS])):
            for name in names:
                if name not in cats.setdefault(kind, {}):
                    cat = Category(user_id=user.id, kind=kind, name=name)
                    db.session.add(cat)
                    db.session.flush()
                    cats[kind][name] = cat.id

        today = local_today()
        for name, emoji, c1, c2, count, status, type_name, ip_name in SEED_ITEMS:
            transit = {}
            if name in SEED_TRANSIT:
                days, channel, order_no, price = SEED_TRANSIT[name]
                transit = {"expect_date": today + timedelta(days=days),
                           "channel": channel, "order_no": order_no, "price": price}
            sold = {}
            if name in SEED_SOLD:
                cost, price, method, channel, days = SEED_SOLD[name]
                sold = {"price": cost, "sold_price": price, "sold_method": method,
                        "sold_channel": channel, "sold_count": count,
                        "sold_at": today + timedelta(days=days)}
            if name in SEED_PARTIAL:
                cost, qty, price, method, channel, days = SEED_PARTIAL[name]
                sold = {"price": cost, "sold_count": qty, "sold_price": price,
                        "sold_method": method, "sold_channel": channel,
                        "sold_at": today + timedelta(days=days)}
            wish = {}
            if name in SEED_WISH:
                level, budget, link, note = SEED_WISH[name]
                wish = {"want_level": level, "budget": budget,
                        "want_link": link, "want_note": note}
            db.session.add(GuItem(
                user_id=user.id, name=name, emoji=emoji,
                color_start=c1, color_end=c2,
                count=count, status=status,
                category_type_id=cats["type"][type_name],
                category_ip_id=cats["ip"][ip_name],
                **transit, **sold, **wish,
            ))

        now = utcnow()
        for title, sub, days, pri, link, hour in SEED_REMINDERS:
            # 提醒时间是用户视角的本地时间，所以用 local_now 推，并且落在整点
            when = (local_now() + timedelta(days=days)).replace(
                hour=hour, minute=0, second=0, microsecond=0)
            db.session.add(Reminder(
                user_id=user.id, title=title, subtitle=sub,
                event_time=when, priority=pri, link=link or None,
            ))

        # 换谷信息：地区抄发布者当时的地区，之后他改地区不影响旧信息
        for emoji, c1, c2, title, want, dist, place, contact, note in SEED_EXCHANGES:
            db.session.add(Exchange(
                user_id=user.id, emoji=emoji,
                color_start=c1, color_end=c2,
                title=title, want=want, distance_km=dist,
                region=user.region, place=place or None,
                contact=contact or None, note=note or None,
            ))

        # 第二个账号：让换谷板上能看到「谷友」发的信息（和 demo 同地区）
        mate = User(username=SEED_NEIGHBOR["username"],
                    region=SEED_NEIGHBOR["region"])
        mate.set_password(SEED_NEIGHBOR["password"])
        db.session.add(mate)
        db.session.flush()
        for emoji, c1, c2, title, want, dist, place, contact, note in \
                SEED_NEIGHBOR["exchanges"]:
            db.session.add(Exchange(
                user_id=mate.id, emoji=emoji,
                color_start=c1, color_end=c2,
                title=title, want=want, distance_km=dist,
                region=mate.region, place=place or None,
                contact=contact or None, note=note or None,
            ))

        db.session.commit()
        print("✅ 数据库已初始化，账号 demo / demo123")
        print(f"   分类：{len(cats['type'])} 个周边品类、{len(cats['ip'])} 个作品IP")
        print(f"   在途：{len(SEED_TRANSIT)} 件（1 件在路上、1 件已超期）")
        print(f"   已出：{len(SEED_SOLD)} 件（带成交价，可看盈亏）")
        print(f"   部分已出：{len(SEED_PARTIAL)} 件（谷柜里只算剩余件数）")
        print(f"   心愿单：{len(SEED_WISH)} 件（不占谷柜，带想要程度和预算）")
        print(f"   换谷：demo 发 {len(SEED_EXCHANGES)} 条 + 谷友 "
              f"{SEED_NEIGHBOR['username']} 发 {len(SEED_NEIGHBOR['exchanges'])} 条"
              f"（账号 {SEED_NEIGHBOR['username']} / {SEED_NEIGHBOR['password']}）")


if __name__ == "__main__":
    _force_utf8_stdout()
    seed()
