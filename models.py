"""SQLAlchemy 数据模型：用户 / 分类 / 谷子 / 再贩提醒 / 同城换谷。"""
from datetime import datetime, timezone

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


def utcnow():
    """返回 naive UTC 时间。

    统一用 naive UTC：SQLite 里存的 DateTime 不带时区，如果一边存 aware 一边存
    naive，比较时会抛 TypeError。顺带避开 3.12+ 已弃用的 datetime.utcnow()。
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def local_today():
    """本地日期。到货倒计时要按用户本地日期算，直接用 UTC 日期会差一天。"""
    return datetime.now(timezone.utc).astimezone().date()


def local_now():
    """本地时间（naive）。

    提醒的「事件时间」是用户按本地时间填的，所以判断有没有过点也要用本地时间，
    不能拿 UTC 去比——否则北京时间早上 8 点前的提醒会显示成已经过期。
    """
    return datetime.now()


def _local_text(value):
    """把 naive UTC 的时间戳转成本地时间文本；空值返回空串。"""
    if not value:
        return ""
    return (value.replace(tzinfo=timezone.utc).astimezone()
            .strftime("%Y-%m-%d %H:%M"))


class User(UserMixin, db.Model):
    __tablename__ = "users"

    REGION_MAX = 64
    SIGNATURE_MAX = 60

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(32), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    # 所在地区：国家→省→市→区/县 里选出来的短名拼接，如「广东 深圳 南山」。
    # 同城换谷按这个匹配，可以随时改。
    region = db.Column(db.String(64))
    # 个性签名，一句话介绍自己
    signature = db.Column(db.String(60))
    # 头像文件名（在 static/uploads/ 里，和谷子照片同一套校验与命名规则）
    avatar = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, default=utcnow)
    last_login_at = db.Column(db.DateTime)
    # 会话令牌：改密码 / 「退出所有设备」时轮换它，其它设备上的登录态随即失效。
    # 为空表示还没轮换过（升级前就存在的登录态不受影响）。
    session_token = db.Column(db.String(64))

    items = db.relationship("GuItem", backref="owner", lazy="dynamic",
                            cascade="all, delete-orphan")
    reminders = db.relationship("Reminder", backref="owner", lazy="dynamic",
                                cascade="all, delete-orphan")
    exchanges = db.relationship("Exchange", backref="owner", lazy="dynamic",
                                cascade="all, delete-orphan")
    categories = db.relationship("Category", backref="owner", lazy="dynamic",
                                 cascade="all, delete-orphan")

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)

    @property
    def created_text(self):
        """入库是 naive UTC，显示要转本地时区，否则差 8 小时。"""
        return _local_text(self.created_at)

    @property
    def last_login_text(self):
        return _local_text(self.last_login_at) or "—"

    def get_stats(self):
        month_start = utcnow().replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        base = self.items
        transit = base.filter_by(status="in_transit")
        sold = base.filter_by(status="sold")
        amount = (db.session.query(func.coalesce(func.sum(GuItem.price), 0.0))
                  .filter(GuItem.user_id == self.id,
                          GuItem.status == "in_transit").scalar())
        # 回血/盈亏按「有出货记录」算，所以出掉一部分的也算进去
        income = (db.session.query(func.coalesce(func.sum(GuItem.sold_price), 0.0))
                  .filter(GuItem.user_id == self.id,
                          GuItem.sold_count > 0).scalar())
        qty = (db.session.query(func.coalesce(func.sum(GuItem.sold_count), 0))
               .filter(GuItem.user_id == self.id).scalar())
        wish_budget = (db.session.query(func.coalesce(func.sum(GuItem.budget), 0.0))
                       .filter(GuItem.user_id == self.id,
                               GuItem.status == "wishlist").scalar())
        # 盈亏只算「成交价和入手价都有」的那些
        profit = (db.session.query(
                      func.coalesce(func.sum(GuItem.sold_price - GuItem.price), 0.0))
                  .filter(GuItem.user_id == self.id,
                          GuItem.sold_count > 0,
                          GuItem.sold_price.isnot(None),
                          GuItem.price.isnot(None)).scalar())
        return {
            "total": base.count(),
            "month_add": base.filter(GuItem.created_at >= month_start).count(),
            "displaying": base.filter_by(status="displaying").count(),
            "in_transit": transit.count(),
            "sold": sold.count(),
            "wishlist": base.filter_by(status="wishlist").count(),
            # 在途专属：超期件数 + 金额合计
            "transit_overdue": transit.filter(
                GuItem.expect_date.isnot(None),
                GuItem.expect_date < local_today()).count(),
            "transit_amount": round(float(amount or 0), 2),
            # 已出专属：累计出掉几件 + 回血合计 + 盈亏合计
            "sold_qty": int(qty or 0),
            "sold_income": round(float(income or 0), 2),
            "sold_profit": round(float(profit or 0), 2),
            # 心愿单专属：总预算
            "wishlist_budget": round(float(wish_budget or 0), 2),
        }


class Category(db.Model):
    """用户自定义分类，两个维度共用一张表，靠 kind 区分：

    kind='type' → 周边品类（吧唧、色纸……）
    kind='ip'   → 作品IP（某某、破云……）

    一个谷子在每个维度上最多属于一个分类，两个维度互不影响，
    所以视图可以在两种划分方式之间随时切换。
    """
    __tablename__ = "categories"

    KINDS = ("type", "ip")
    KIND_LABELS = {"type": "周边品类", "ip": "作品IP"}
    KIND_SHORT = {"type": "品类", "ip": "作品IP"}
    NAME_MAX = 32
    UNCATEGORIZED = "未分类"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"),
                        nullable=False, index=True)
    kind = db.Column(db.String(8), nullable=False, index=True)
    name = db.Column(db.String(32), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    # 同一个用户、同一维度下不允许重名
    __table_args__ = (
        db.UniqueConstraint("user_id", "kind", "name",
                            name="uq_category_owner_kind_name"),
    )


class GuItem(db.Model):
    __tablename__ = "gu_items"

    NAME_MAX = 64          # 和 db.Column(String(64)) 对齐，导入时用来截断
    DEFAULT_STATUS = "displaying"
    STATUS_LABELS = {
        "displaying": "展示中",
        "in_transit": "在途",
        "sold": "已出",
        "wishlist": "心愿单",
    }

    # 在途专属：购买渠道（存 key，显示用 label）
    CHANNELS = ("daigou", "pintuan", "xianyu", "weidian", "official", "other")
    CHANNEL_LABELS = {
        "daigou": "代购",
        "pintuan": "拼团",
        "xianyu": "闲鱼",
        "weidian": "微店",
        "official": "官网",
        "other": "其他",
    }
    PRICE_MAX = 999999.0

    # 已出专属：出手方式 / 出手平台
    SOLD_METHODS = ("sell", "swap", "gift")
    SOLD_METHOD_LABELS = {"sell": "卖出", "swap": "换出", "gift": "赠送"}
    SALE_CHANNELS = ("xianyu", "weidian", "qun", "offline", "other")
    SALE_CHANNEL_LABELS = {
        "xianyu": "闲鱼",
        "weidian": "微店",
        "qun": "群内",
        "offline": "线下",
        "other": "其他",
    }

    # 心愿单专属：想要程度
    WANT_LEVELS = ("hot", "normal", "maybe")
    WANT_LEVEL_LABELS = {"hot": "很想要", "normal": "一般", "maybe": "随缘"}
    WANT_SORT = {"hot": 0, "normal": 1, "maybe": 2}   # 心愿单列表排序用

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"),
                        nullable=False, index=True)
    name = db.Column(db.String(64), nullable=False)
    emoji = db.Column(db.String(8), default="🎁")
    # 只存文件名（uuid 生成），不存路径/URL：以后换存储位置不用改数据。
    # 为空表示没上传图片，前端回落到 emoji + 渐变色。
    image = db.Column(db.String(120))
    color_start = db.Column(db.String(16), default="#EAF3FF")
    color_end = db.Column(db.String(16), default="#D9E9FF")
    count = db.Column(db.Integer, default=1, nullable=False)
    # 已出件数：0 < sold_count < count 表示「部分已出」，谷柜里仍展出剩余件数；
    # sold_count == count 时状态自动变成已出，整件从谷柜消失。
    sold_count = db.Column(db.Integer, default=0, nullable=False, server_default="0")
    status = db.Column(db.String(16), default=DEFAULT_STATUS)
    created_at = db.Column(db.DateTime, default=utcnow)

    # 两个维度各一个可空外键：没分类就是 NULL
    category_type_id = db.Column(db.Integer, db.ForeignKey("categories.id"))
    category_ip_id = db.Column(db.Integer, db.ForeignKey("categories.id"))

    # 在途专属字段。只有 status=in_transit 时才在界面上展开，但数据一直保留：
    # 确认到货后不主动清空，方便回看「当时预计什么时候到」。
    expect_date = db.Column(db.Date)        # 预计到货日期
    channel = db.Column(db.String(16))      # 购买渠道（CHANNELS 里的 key）
    order_no = db.Column(db.String(64))     # 订单号 / 物流单号
    price = db.Column(db.Float)             # 入手价（元）；也是算盈亏的成本
    arrived_at = db.Column(db.DateTime)     # 确认到货时间

    # 已出专属字段。同样只在 status=sold 时展开，撤回后仍保留（出手记录）
    sold_price = db.Column(db.Float)        # 成交价（元）
    sold_method = db.Column(db.String(8))   # 卖出 / 换出 / 赠送
    sold_channel = db.Column(db.String(16))  # 出手平台
    sold_at = db.Column(db.Date)            # 出手日期

    # 心愿单专属字段。只在 status=wishlist 时展开
    want_level = db.Column(db.String(8))    # 很想要 / 一般 / 随缘
    budget = db.Column(db.Float)            # 预算价（元）
    want_link = db.Column(db.String(255))   # 参考链接或店铺名
    want_note = db.Column(db.String(120))   # 备注

    # 同一张表有两个外键，必须显式指明 foreign_keys，否则 SQLAlchemy 无法消歧
    category_type = db.relationship("Category", foreign_keys=[category_type_id])
    category_ip = db.relationship("Category", foreign_keys=[category_ip_id])

    @property
    def status_text(self):
        return self.STATUS_LABELS.get(self.status, "未分类")

    # ---------- 件数 / 部分已出 ----------
    @property
    def sold_qty(self):
        """已出件数（防 NULL）。"""
        return self.sold_count or 0

    @property
    def remaining(self):
        """谷柜里还剩几件 = 总数 − 已出件数。"""
        return max((self.count or 0) - self.sold_qty, 0)

    @property
    def partially_sold(self):
        """部分已出：出掉了一些，但还没出完。"""
        return 0 < self.sold_qty < (self.count or 0)

    @property
    def fully_sold(self):
        """整件出掉了，谷柜不再展出。"""
        return self.status == "sold" or self.remaining <= 0

    @property
    def count_text(self):
        """件数说明，详情里用。"""
        if self.sold_qty > 0:
            return (f"共 {self.count} 件 · 已出 {self.sold_qty} 件"
                    f" · 剩 {self.remaining} 件")
        return f"共 {self.count} 件"

    def category_for(self, kind):
        """按维度取分类对象，没分到返回 None。"""
        return self.category_ip if kind == "ip" else self.category_type

    def category_name(self, kind):
        cat = self.category_for(kind)
        return cat.name if cat else Category.UNCATEGORIZED

    @property
    def created_text(self):
        """入库时间是 naive UTC，转成本地时区再显示，否则会差 8 小时。"""
        if not self.created_at:
            return ""
        return (self.created_at.replace(tzinfo=timezone.utc)
                .astimezone().strftime("%Y-%m-%d %H:%M"))

    # ---------- 在途相关 ----------
    @property
    def days_to_arrive(self):
        """距预计到货还有几天；已超期为负数，没填日期返回 None。"""
        if not self.expect_date:
            return None
        return (self.expect_date - local_today()).days

    @property
    def overdue(self):
        """在途且已过预计到货日期。"""
        days = self.days_to_arrive
        return self.status == "in_transit" and days is not None and days < 0

    @property
    def countdown_text(self):
        """列表上的短标签。"""
        days = self.days_to_arrive
        if days is None:
            return "未填预计到货"
        if days > 1:
            return f"{days} 天后"
        if days == 1:
            return "明天到"
        if days == 0:
            return "今天到"
        return f"超期 {-days} 天"

    @property
    def expect_text(self):
        """详情里的完整文案：日期（+ 仅在途时带倒计时/超期说明）。"""
        if not self.expect_date:
            return ""
        text = self.expect_date.strftime("%Y-%m-%d")
        if self.status != "in_transit":
            return text
        days = self.days_to_arrive
        if days is None:
            return text
        if days < 0:
            return f"{text}（已超期 {-days} 天）"
        if days == 0:
            return f"{text}（今天）"
        if days == 1:
            return f"{text}（明天）"
        return f"{text}（{days} 天后）"

    @property
    def channel_text(self):
        return self.CHANNEL_LABELS.get(self.channel, "")

    @property
    def source_text(self):
        """渠道 + 单号，列表上一行显示。"""
        return " · ".join(p for p in (self.channel_text, self.order_no) if p)

    @property
    def price_text(self):
        return f"¥{self.price:,.2f}" if self.price is not None else ""

    @property
    def arrived_text(self):
        if not self.arrived_at:
            return ""
        return (self.arrived_at.replace(tzinfo=timezone.utc)
                .astimezone().strftime("%Y-%m-%d %H:%M"))

    # ---------- 已出相关 ----------
    @property
    def sold_method_text(self):
        return self.SOLD_METHOD_LABELS.get(self.sold_method, "")

    @property
    def sold_channel_text(self):
        return self.SALE_CHANNEL_LABELS.get(self.sold_channel, "")

    @property
    def sold_source_text(self):
        """出手方式 + 平台，列表上一行显示。"""
        return " · ".join(p for p in (self.sold_method_text,
                                      self.sold_channel_text) if p)

    @property
    def sold_price_text(self):
        return f"¥{self.sold_price:,.2f}" if self.sold_price is not None else ""

    @property
    def sold_at_text(self):
        return self.sold_at.strftime("%Y-%m-%d") if self.sold_at else ""

    @property
    def transit_meta_text(self):
        """在途清单第二行：件数 · 入手价 · 预计到货。"""
        parts = []
        if (self.count or 1) > 1:
            parts.append(f"共 {self.count} 件")
        if self.price_text:
            parts.append(self.price_text)
        if self.expect_date:
            parts.append(f"预计 {self.expect_date.strftime('%m-%d')}")
        return " · ".join(parts)

    @property
    def has_sale(self):
        """有出货记录（出过至少一件）——「已出」专区按这个收录，不看状态。"""
        return self.sold_qty > 0

    @property
    def sold_meta_text(self):
        """已出清单第二行：出掉几件 · 成交价 · 出手日期。

        多件才写「出 N 件」（单件写出来是废话）。出掉一部分的也只写出掉的件数，
        不再补「（剩 M 件在谷柜）」——谷柜那张卡片的角标已经写着剩余件数了。
        """
        parts = []
        if (self.count or 1) > 1:
            parts.append(f"出 {self.sold_qty} 件")
        if self.sold_price_text:
            parts.append(f"成交 {self.sold_price_text}")
        if self.sold_at:
            parts.append(f"{self.sold_at.strftime('%m-%d')} 出手")
        return " · ".join(parts) or "未填成交价和出手日期"

    # ---------- 心愿单相关 ----------
    @property
    def want_level_text(self):
        return self.WANT_LEVEL_LABELS.get(self.want_level, "")

    @property
    def budget_text(self):
        return f"¥{self.budget:,.2f}" if self.budget is not None else ""

    @property
    def want_link_href(self):
        """只有 http/https 才当成链接渲染。

        用户可以填店铺名（纯文本）也可以填链接；万一填了 javascript: 之类的东西，
        这里返回空串，模板就只当纯文本显示，不会变成可点的 <a href>。
        """
        link = (self.want_link or "").strip()
        low = link.lower()
        if low.startswith("http://") or low.startswith("https://"):
            return link
        return ""

    @property
    def want_level_state(self):
        """hot / normal / maybe / none —— 前端据此上色。"""
        return self.want_level if self.want_level in self.WANT_LEVELS else "none"

    @property
    def want_source_text(self):
        """心愿单清单上那行说明：预算 · 备注。"""
        parts = []
        if self.budget_text:
            parts.append(f"预算 {self.budget_text}")
        if self.want_note:
            parts.append(self.want_note)
        return " · ".join(parts)

    @property
    def profit(self):
        """盈亏 = 成交价 − 入手价；缺任意一个就算不出来，返回 None。"""
        if self.sold_price is None or self.price is None:
            return None
        return round(self.sold_price - self.price, 2)

    @property
    def profit_text(self):
        value = self.profit
        if value is None:
            return ""
        sign = "+" if value > 0 else ("-" if value < 0 else "")
        return f"{sign}¥{abs(value):,.2f}"

    @property
    def profit_state(self):
        """up / down / even，前端据此上色。"""
        value = self.profit
        if value is None:
            return ""
        if value > 0:
            return "up"
        if value < 0:
            return "down"
        return "even"


class Reminder(db.Model):
    """再贩提醒：某个周边在某天某时再贩/开预定，到点前去抢。"""
    __tablename__ = "reminders"

    PRIORITIES = ("hot", "normal", "mute")
    PRIORITY_LABELS = {"hot": "重点", "normal": "普通", "mute": "随缘"}
    TITLE_MAX = 80
    SUBTITLE_MAX = 120
    LINK_MAX = 255

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"),
                        nullable=False, index=True)
    title = db.Column(db.String(80), nullable=False)
    subtitle = db.Column(db.String(120))
    # 用户按本地时间填的「事件时间」，所以比较也用本地时间（见 local_now）
    event_time = db.Column(db.DateTime, nullable=False)
    priority = db.Column(db.String(8), default="normal")  # hot / normal / mute
    link = db.Column(db.String(255))                      # 抢购链接或店铺

    @property
    def priority_text(self):
        return self.PRIORITY_LABELS.get(self.priority, "")

    @property
    def days_left(self):
        """距事件还有几天（按自然日），已过期为负数。"""
        return (self.event_time.date() - local_today()).days

    @property
    def is_past(self):
        """事件时刻已经过去了。"""
        return self.event_time < local_now()

    @property
    def tag_text(self):
        """相对天数标签。按自然日相减，避免 47 小时被算成「明天」。"""
        days = self.days_left
        if days < 0:
            return f"已过期 {-days} 天"
        if days == 0:
            return "今天"
        if days == 1:
            return "明天"
        return f"{days}天后"

    @property
    def time_text(self):
        """完整时间，列表上显示。"""
        return self.event_time.strftime("%m-%d %H:%M") if self.event_time else ""

    @property
    def full_time_text(self):
        return self.event_time.strftime("%Y-%m-%d %H:%M") if self.event_time else ""

    @property
    def link_href(self):
        """只有 http/https 才当成链接渲染（同心愿单，防 javascript: 之类的 XSS）。"""
        link = (self.link or "").strip()
        low = link.lower()
        if low.startswith("http://") or low.startswith("https://"):
            return link
        return ""


class Exchange(db.Model):
    """同城换谷：一条「求换 X，可换 Y，在哪面交」的信息。

    谷友互看——所有人发布的都在同一块板上，按「我发的 → 同地区 → 距离」排序。
    """
    __tablename__ = "exchanges"

    TITLE_MAX = 80
    WANT_MAX = 80
    PLACE_MAX = 60
    CONTACT_MAX = 60
    NOTE_MAX = 120
    REGION_MAX = 32

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"),
                        nullable=False, index=True)
    emoji = db.Column(db.String(8), default="🐰")
    color_start = db.Column(db.String(16), default="#EAF3FF")
    color_end = db.Column(db.String(16), default="#D9E9FF")
    title = db.Column(db.String(80), nullable=False)   # 求换什么
    want = db.Column(db.String(80))                    # 能换什么
    distance_km = db.Column(db.Float, default=1.0, nullable=False)
    # 发布时把发布者当时的地区抄一份进来，之后他改地区也不会让旧信息飘走
    region = db.Column(db.String(32))
    place = db.Column(db.String(60))                   # 面交地点
    contact = db.Column(db.String(60))                 # 联系方式
    note = db.Column(db.String(120))                   # 补充说明
    created_at = db.Column(db.DateTime, default=utcnow)

    @property
    def distance_text(self):
        return f"约 {self.distance_km:g}km" if self.distance_km is not None else ""

    @property
    def detail_text(self):
        """列表第二行：可换 · 距离 · 地区。"""
        parts = []
        if self.want:
            parts.append(f"可换 {self.want}")
        if self.distance_text:
            parts.append(self.distance_text)
        if self.region:
            parts.append(self.region)
        return " · ".join(parts)

    @property
    def contact_text(self):
        """列表第三行：面交地点 · 联系方式 · 说明。"""
        return " · ".join(p for p in (self.place, self.contact, self.note) if p)
