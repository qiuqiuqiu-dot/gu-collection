"""蓝图路由：认证 + 谷子 CRUD + 分类 + 在途 + REST API + 数据备份 + 账号安全。"""
import os
import secrets
import time
from datetime import datetime
from uuid import uuid4

from flask import (Blueprint, abort, current_app, flash, jsonify, redirect,
                   render_template, request, send_file, session, url_for)
from flask_login import (current_user, login_required, login_user,
                         logout_user)
from PIL import Image, ImageOps
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

import backup
import regions
from models import (Category, Exchange, GuItem, Reminder, User, db,
                    local_today, utcnow)
bp = Blueprint("main", __name__)

NAME_MAX = 64
EMOJI_MAX = 8
USERNAME_MIN, USERNAME_MAX = 3, 32
PASSWORD_MIN = 6
COUNT_MAX = 9999

# 同城换谷功能开关。
# 先关掉：界面上的入口（首页板块、+ 号面板里的「换谷信息」）都不再出现，
# /exchanges 相关路由直接 404。代码、模板和数据库里的换谷数据全部保留，
# 想恢复只要把这里改成 True（或在 create_app 里传 EXCHANGE_ENABLED=True）。
DEFAULT_EXCHANGE_ENABLED = False


def _exchange_enabled():
    """读当前 app 的开关。放 config 里是为了测试能单独把一个 app 打开成 True，
    从而继续完整地跑换谷那套用例。"""
    return bool(current_app.config.get("EXCHANGE_ENABLED",
                                       DEFAULT_EXCHANGE_ENABLED))

# 谷柜默认只展示这么多件，其余去谷柜页看
PANEL_PREVIEW = 6

# 谷柜页的分组方式：默认「全部」——先平铺看全部，再让用户自己选要不要按分类看
GROUP_MODES = ("all", "type", "ip")
GROUP_LABELS = {"all": "全部", "type": "按品类", "ip": "按作品IP"}

# 仪表盘上「在途」/「已出」板块最多列几件，其余去专区页看
# 仪表盘上「在途」/「已出」/「心愿单」/「再贩提醒」板块最多列几件，其余去专区页看
TRANSIT_PREVIEW = 3
SOLD_PREVIEW = 3
WISHLIST_PREVIEW = 3
REMINDER_PREVIEW = 3
EXCHANGE_PREVIEW = 4

# 只放光栅图。SVG 里可以内嵌 <script>，用户传上去再被直接访问就是存储型 XSS。
IMAGE_FORMATS = {"PNG": ".png", "JPEG": ".jpg", "GIF": ".gif", "WEBP": ".webp"}

# 防解压炸弹：像素总量超过这个值 Pillow 会直接拒绝。
Image.MAX_IMAGE_PIXELS = 40_000_000


# ==================== 表单清洗工具 ====================
def _clean(value, limit):
    """去空白 + 按列长度截断。

    SQLite 不强制 VARCHAR 长度，超长值会被静默写入，所以长度必须在代码里卡住。
    """
    return (value or "").strip()[:limit]


def _parse_count(raw):
    """把数量解析成 1..COUNT_MAX 的整数，任何非法输入回退为 1。"""
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return 1
    return min(max(value, 1), COUNT_MAX)


def _parse_sold_count(raw):
    """已出件数：非数字/负数都当 0，上限交给 _normalize_quantity 夹到总数。"""
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return 0
    return max(value, 0)


def _normalize_quantity(item):
    """把「总数 / 已出件数 / 状态」三者对齐，保证它们永远不互相矛盾。

    唯一的等价关系：状态「已出」 ⟺ 已出件数 = 总数。
      · 已出件数夹在 0..总数 之间
      · 全部出完 → 状态变已出（整件从谷柜消失）
      · 选了已出但没填件数 → 视为整件出掉，件数对齐到总数
      · **还有剩的 → 状态不能是已出**，否则它会被谷柜藏起来，
        而「出掉一部分、剩下的继续展出」才是我们要的
    """
    item.count = min(max(item.count or 1, 1), COUNT_MAX)
    item.sold_count = min(max(item.sold_count or 0, 0), item.count)
    if item.status == "sold" and item.sold_count == 0:
        item.sold_count = item.count          # 选了已出但没填件数 = 整件出掉
    if item.sold_count >= item.count:
        item.status = "sold"                  # 全出完
    elif item.status == "sold":
        item.status = "displaying"            # 还有剩的，回展示中


# ==================== 在途 / 已出字段解析 ====================
def _parse_date(raw):
    """解析 <input type="date"> 的 YYYY-MM-DD；空/非法都返回 None。"""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_price(raw):
    """解析金额（元）。允许 ¥ 和千分位逗号；非法/负数返回 None。"""
    text = (raw or "").replace("¥", "").replace(",", "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if value < 0:
        return None
    return round(min(value, GuItem.PRICE_MAX), 2)


def _parse_want_level(raw):
    """想要程度走白名单（很想要/一般/随缘）。"""
    value = (raw or "").strip()
    return value if value in GuItem.WANT_LEVELS else None


def _parse_datetime(raw):
    """解析 <input type="datetime-local"> 的 YYYY-MM-DDTHH:MM。

    提醒时间是用户按本地时间填的，所以原样存本地 naive 时间，不转 UTC。
    """
    text = (raw or "").strip().replace(" ", "T")
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _parse_channel(raw):
    """购买渠道走白名单，非法值当成没填。"""
    value = (raw or "").strip()
    return value if value in GuItem.CHANNELS else None


def _parse_sold_method(raw):
    """出手方式走白名单（卖出/换出/赠送）。"""
    value = (raw or "").strip()
    return value if value in GuItem.SOLD_METHODS else None


def _parse_sale_channel(raw):
    """出手平台走白名单。"""
    value = (raw or "").strip()
    return value if value in GuItem.SALE_CHANNELS else None


def _save_image(file):
    """校验并保存上传的图片，返回生成的文件名；不合法返回 None。

    扩展名以 Pillow 实际识别出的格式为准，而不是用户给的文件名：
    「把 .txt 改名成 .png」在解码阶段就被挡掉；
    「把透明 PNG 改名成 .jpg」也不会因为 RGBA 存不进 JPEG 而 500。
    """
    if not file or not file.filename:
        return None
    try:
        img = Image.open(file.stream)
        fmt = (img.format or "").upper()
        if fmt not in IMAGE_FORMATS:
            return None
        img.load()  # 真正解码一次，损坏或伪造的文件在这里抛异常
    except Exception:
        return None

    # 手机竖拍的照片带 EXIF 方向，不转正会躺着显示
    img = ImageOps.exif_transpose(img)

    max_side = current_app.config["IMAGE_MAX_SIDE"]
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side))  # 保持比例，顺带省体积

    # 文件名自己生成：一次解决路径穿越、重名覆盖、中文名乱码三个问题。
    # （secure_filename('白兔.png') 返回的是 'png'，连扩展名都不剩，不能拿来当文件名。）
    name = f"{uuid4().hex}{IMAGE_FORMATS[fmt]}"
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], name)
    try:
        img.save(path, format=fmt)  # 动图只保留首帧
    except OSError:
        if os.path.exists(path):
            os.remove(path)
        return None
    return name


def _delete_image(name):
    """删除磁盘上的图片；文件已经不在了也不让业务失败。"""
    if not name or os.path.basename(name) != name:
        return
    try:
        os.remove(os.path.join(current_app.config["UPLOAD_FOLDER"], name))
    except OSError:
        pass


# ==================== 分类工具 ====================
def _pick_category_id(raw, kind, user_id):
    """把表单里的分类 id 解析成 id。

    必须校验「合法整数 + 属于当前用户 + kind 对得上」——否则用户可以把别人的
    分类挂到自己谷子上，或者把作品IP塞进品类维度。
    """
    try:
        cid = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    cat = Category.query.filter_by(id=cid, user_id=user_id, kind=kind).first()
    return cat.id if cat else None


def _form_options(user):
    """弹窗里要用到的选项：两个维度的分类 + 在途购买渠道 + 已出的出手方式/平台。"""
    def fetch(kind):
        return (user.categories.filter_by(kind=kind)
                .order_by(Category.created_at, Category.id).all())
    return {"cats_type": fetch("type"), "cats_ip": fetch("ip"),
            "channels": GuItem.CHANNEL_LABELS,
            "sold_methods": GuItem.SOLD_METHOD_LABELS,
            "sale_channels": GuItem.SALE_CHANNEL_LABELS,
            "want_levels": GuItem.WANT_LEVEL_LABELS}


def _current_group():
    """谷柜页的分组方式：只认 ?group=，默认「全部」。

    刻意不记进 session：每次进谷柜都先平铺看全部，再由用户自己决定要不要按分类看；
    记进 session 的话第二次进来就直接是分类视图了，和「先展示全部」相反。
    """
    wanted = request.args.get("group")
    return wanted if wanted in GROUP_MODES else GROUP_MODES[0]


def _group_items(items, kind):
    """按 kind 维度分组，返回 [(分组名, [items]), ...]。

    kind 为 "all"（或任何非维度值）时不分组，返回单个无名分组 = 平铺展示。
    分组时组内保持「最新在前」；组与组之间按用户建分类的先后排，未分类永远在最后。
    这样分组顺序稳定可预期，不会因为新增了一件谷子就整体乱跳。
    每组至少一件（由构造方式保证），不会出现只有标题的空组。
    """
    if kind not in Category.KINDS:
        return [(None, items)]
    buckets = {}
    for item in items:
        cat = item.category_for(kind)
        key = cat.id if cat else None
        if key not in buckets:
            buckets[key] = {
                # 排序键：第一项先把「未分类」顶到最后，避免拿 None 和 datetime 比大小
                "order": (cat is None,
                          cat.created_at if cat else None,
                          cat.id if cat else 0),
                "label": cat.name if cat else Category.UNCATEGORIZED,
                "items": [],
            }
        buckets[key]["items"].append(item)
    ordered = sorted(buckets.values(), key=lambda b: b["order"])
    return [(b["label"], b["items"]) for b in ordered]


def _redirect_back():
    """分类是弹窗里增删的，处理完要回到原页面原维度。"""
    nxt = request.form.get("next") or ""
    # 只接受站内相对路径，挡掉 //evil.com 这类开放重定向
    if nxt.startswith("/") and not nxt.startswith("//"):
        return redirect(nxt)
    return redirect(url_for("main.index"))


def _wants_json():
    """分类弹窗用 fetch 增删，命中时返回 JSON，弹窗不关也不重载页面。"""
    return (request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or request.accept_mimetypes.best == "application/json")


def _cat_response(ok, message, status=200, **extra):
    """分类增删的统一出口：AJAX 给 JSON，普通表单给 flash + 整页回跳。"""
    if _wants_json():
        payload = {"ok": ok, "message": message}
        payload.update(extra)
        return jsonify(payload), status
    flash(message, "success" if ok else "error")
    return _redirect_back()


def _kind_count(user, kind):
    return user.categories.filter_by(kind=kind).count()


# ==================== 首页 / 仪表盘 ====================
def _items_of(user):
    """谷柜里的谷子：最新在前。

    谷柜 = 我手里有的东西，所以两类不展出：
      · 心愿单：还没入手，只在心愿单专区看
      · 整件出掉的（状态已出，或已出件数 = 总数）
    出掉一部分的仍然展出，件数按剩余算。
    """
    return (user.items.filter(GuItem.status.notin_(("sold", "wishlist")),
                              GuItem.count > GuItem.sold_count)
            .order_by(GuItem.created_at.desc(), GuItem.id.desc())
            .all())


def _wishlist_of(user):
    """心愿单：很想要 → 一般 → 随缘 → 未分级；同级别里预算低的在前（便宜的先入手），
    没填预算的排最后，最后按最新添加。"""
    items = user.items.filter_by(status="wishlist").all()
    return sorted(items, key=lambda i: (
        GuItem.WANT_SORT.get(i.want_level, 3),
        i.budget if i.budget is not None else float("inf"),
        -i.id,
    ))


def _transit_of(user):
    """在途谷子：按预计到货从近到远排，没填日期的排最后（超期的自然排最前）。"""
    return (user.items.filter_by(status="in_transit")
            .order_by(GuItem.expect_date.is_(None),
                      GuItem.expect_date.asc(),
                      GuItem.id.desc())
            .all())


def _reminders_of(user):
    """再贩提醒：还没到点的按时间从近到远在前，已经过期的排最后。"""
    items = user.reminders.all()
    return sorted(items, key=lambda r: (r.is_past, r.event_time, r.id))


def _reminder_options(user):
    """提醒弹窗要用到的选项。"""
    return {"priorities": Reminder.PRIORITY_LABELS}


@bp.app_context_processor
def inject_add_options():
    """把「添加」要用的下拉选项塞给所有模板。

    统一 + 号之后，任何页面都可能弹出添加谷子 / 提醒的表单（在途页也能直接加），
    所以选项不能再靠各个视图函数单独传——漏传一个页面就是 500。
    未登录时给空值：登录页不必白查一次库。
    顺便把「同城换谷开关」也传下去，模板据此决定显示不显示换谷入口。
    """
    enabled = _exchange_enabled()
    if not current_user.is_authenticated:
        return {"cats_type": [], "cats_ip": [], "channels": {},
                "sold_methods": {}, "sale_channels": {}, "want_levels": {},
                "priorities": {}, "exchange_enabled": enabled}
    options = _form_options(current_user)
    options.update(_reminder_options(current_user))
    options["exchange_enabled"] = enabled
    return options


@bp.before_request
def block_exchange_when_disabled():
    """换谷功能关掉时，/exchanges 这一组路由一律 404。

    放在这里而不是逐个路由加判断：开关只有一个地方，
    以后要恢复也不会漏掉某个子路由（add / edit / delete）。
    """
    if not _exchange_enabled() and request.path.startswith("/exchanges"):
        abort(404)


def _exchanges_of(user):
    """换谷板：我发的在最前，然后是同城的，再按距离从近到远。

    「同城」按「省 + 市」算，不按整串比：地区已经能选到区/县，
    如果整串比，「广东 深圳 南山」和「广东 深圳 福田」就会被判成不同城，
    跟「同城换谷」的意思正好相反。同城之后，同一个区的再排前面。
    不涉及真实经纬度，距离是发布时自己填的。
    """
    my_city = regions.city_key(user.region)
    my_district = regions.district_key(user.region)
    rows = Exchange.query.all()
    return sorted(rows, key=lambda ex: (
        0 if ex.user_id == user.id else 1,
        0 if my_city and regions.city_key(ex.region) == my_city else 1,
        0 if my_district and regions.district_key(ex.region) == my_district else 1,
        ex.distance_km if ex.distance_km is not None else float("inf"),
        -ex.id,
    ))


def _sold_of(user):
    """出货记录：出过至少一件的都收录（含只出一部分的），按出手日期从近到远。

    注意这跟谷柜是两回事：出掉一部分的谷子，谷柜里显示剩余件数，这里显示
    出掉的那几件，所以同一件会同时出现在两处。
    """
    return (user.items
            .filter(or_(GuItem.sold_count > 0, GuItem.status == "sold"))
            .order_by(GuItem.sold_at.is_(None),
                      GuItem.sold_at.desc(),
                      GuItem.id.desc())
            .all())


@bp.route("/")
@login_required
def index():
    items = _items_of(current_user)
    preview_items = items[:PANEL_PREVIEW]
    transit = _transit_of(current_user)
    sold = _sold_of(current_user)
    wishlist = _wishlist_of(current_user)
    reminders = _reminders_of(current_user)
    # 换谷关掉时不查这块板（模板也不会渲染这个板块）
    board = _exchanges_of(current_user) if _exchange_enabled() else []
    return render_template(
        "index.html",
        # 仪表盘只做「最新 6 件」预览，不分组；按分类浏览请去谷柜页。
        # 一件都没有时给空列表，模板才会渲染空状态（[(None, [])] 会让循环跑一轮）。
        groups=[(None, preview_items)] if preview_items else [],
        total_items=len(items),
        transit=transit[:TRANSIT_PREVIEW],
        transit_total=len(transit),
        sold=sold[:SOLD_PREVIEW],
        sold_total=len(sold),
        wishlist=wishlist[:WISHLIST_PREVIEW],
        wishlist_total=len(wishlist),
        reminders=reminders[:REMINDER_PREVIEW],
        reminder_total=len(reminders),
        reminder_past=sum(1 for r in reminders if r.is_past),
        # 换谷板是「谷友互看」，仪表盘上给几条预览
        exchanges=board[:EXCHANGE_PREVIEW],
        exchange_total=len(board),
        exchange_mine=sum(1 for e in board if e.user_id == current_user.id),
        stats=current_user.get_stats(),
    )


@bp.route("/transit")
@login_required
def transit():
    """在途专区：按预计到货排序，可以一键确认到货。"""
    items = _transit_of(current_user)
    return render_template(
        "transit.html",
        transit=items,
        total_items=len(items),
        stats=current_user.get_stats(),
    )


@bp.route("/sold")
@login_required
def sold():
    """已出专区：按出手日期排序，可以一键撤回收藏。"""
    items = _sold_of(current_user)
    return render_template(
        "sold.html",
        sold=items,
        total_items=len(items),
        stats=current_user.get_stats(),
    )


@bp.route("/wishlist")
@login_required
def wishlist():
    """心愿单专区：按想要程度 + 预算排序，可以一键「已入手」。"""
    items = _wishlist_of(current_user)
    return render_template(
        "wishlist.html",
        wishlist=items,
        total_items=len(items),
        stats=current_user.get_stats(),
    )


@bp.route("/items")
@login_required
def all_items():
    """完整谷柜页：这里是唯一按分类分组查看的地方。"""
    group = _current_group()
    items = _items_of(current_user)
    return render_template(
        "items.html",
        groups=_group_items(items, group),
        total_items=len(items),
        group=group,
        group_modes=GROUP_MODES,
        group_labels=GROUP_LABELS,
        stats=current_user.get_stats(),
    )


# ==================== 分类 ====================
@bp.route("/categories/add", methods=["POST"])
@login_required
def add_category():
    kind = request.form.get("kind") or ""
    if kind not in Category.KINDS:
        return _cat_response(False, "分类维度不合法", 400)

    name = _clean(request.form.get("name"), Category.NAME_MAX)
    label = Category.KIND_LABELS[kind]
    if not name:
        return _cat_response(False, "请填写分类名称", 400)
    if current_user.categories.filter_by(kind=kind, name=name).first():
        return _cat_response(False, f"「{name}」已经在{label}里了", 409)

    cat = Category(user_id=current_user.id, kind=kind, name=name)
    db.session.add(cat)
    try:
        db.session.commit()
    except IntegrityError:
        # 并发下撞到唯一约束，回滚后当成「已存在」处理
        db.session.rollback()
        return _cat_response(False, f"「{name}」已经在{label}里了", 409)

    return _cat_response(
        True, f"已添加{label}「{name}」",
        id=cat.id, kind=kind, name=cat.name, count=_kind_count(current_user, kind),
        # 删自己的接口地址由服务端给，前端不拼路由字符串
        delete_url=url_for("main.delete_category", cat_id=cat.id))


@bp.route("/categories/<int:cat_id>/delete", methods=["POST"])
@login_required
def delete_category(cat_id):
    cat = Category.query.filter_by(id=cat_id,
                                   user_id=current_user.id).first()
    if cat is None:
        # AJAX 要 JSON，普通表单还是按「不存在」处理（顺带不泄露别人分类的存在性）
        if _wants_json():
            return _cat_response(False, "分类不存在", 404)
        abort(404)

    kind = cat.kind                      # 先取出来，删掉对象后就读不到了
    kind_name = Category.KIND_LABELS.get(kind, "分类")
    name = cat.name
    column = "category_ip_id" if kind == "ip" else "category_type_id"

    # SQLite 默认不强制外键，所以手动把引用了它的谷子改成「未分类」
    affected = (GuItem.query
                .filter_by(user_id=current_user.id)
                .filter(getattr(GuItem, column) == cat.id)
                .update({column: None}, synchronize_session=False))
    db.session.delete(cat)
    db.session.commit()

    return _cat_response(
        True, f"已删除{kind_name}「{name}」，{affected} 件谷子变为未分类",
        id=cat_id, kind=kind, name=name, count=_kind_count(current_user, kind),
        affected=affected)


@bp.route("/reminders")
@login_required
def reminders():
    """再贩提醒专区：可以增删改，过期的排最后。"""
    items = _reminders_of(current_user)
    return render_template(
        "reminders.html",
        reminders=items,
        total_items=len(items),
        reminder_past=sum(1 for r in items if r.is_past),
        stats=current_user.get_stats(),
    )


# ==================== 再贩提醒 CRUD ====================
def _read_reminder_form(item):
    """把表单读进 Reminder；标题/时间必填，缺了返回错误文案。"""
    title = _clean(request.form.get("title"), Reminder.TITLE_MAX)
    if not title:
        return "请填写提醒内容"

    event_time = _parse_datetime(request.form.get("event_time"))
    if event_time is None:
        return "请选择提醒时间"

    priority = request.form.get("priority") or "normal"
    if priority not in Reminder.PRIORITIES:
        priority = "normal"

    item.title = title
    item.event_time = event_time
    item.priority = priority
    item.subtitle = _clean(request.form.get("subtitle"),
                           Reminder.SUBTITLE_MAX) or None
    item.link = _clean(request.form.get("link"), Reminder.LINK_MAX) or None
    return None


@bp.route("/reminders/add", methods=["POST"])
@login_required
def add_reminder():
    item = Reminder(user_id=current_user.id, title="", event_time=utcnow())
    error = _read_reminder_form(item)
    if error:
        flash(error, "error")
        return _redirect_back()

    db.session.add(item)
    db.session.commit()
    flash(f"已添加提醒「{item.title}」", "success")
    return _redirect_back()


@bp.route("/reminders/<int:reminder_id>/edit", methods=["POST"])
@login_required
def edit_reminder(reminder_id):
    item = Reminder.query.filter_by(id=reminder_id,
                                    user_id=current_user.id).first_or_404()
    error = _read_reminder_form(item)
    if error:
        flash(error, "error")
        return _redirect_back()

    db.session.commit()
    flash(f"已更新提醒「{item.title}」", "success")
    return _redirect_back()


@bp.route("/reminders/<int:reminder_id>/delete", methods=["POST"])
@login_required
def delete_reminder(reminder_id):
    """知道了/不再需要 → 直接删掉提醒（提醒本身是一次性的）。"""
    item = Reminder.query.filter_by(id=reminder_id,
                                    user_id=current_user.id).first_or_404()
    title = item.title
    db.session.delete(item)
    db.session.commit()
    flash(f"已删除提醒「{title}」", "success")
    return _redirect_back()


# ==================== 同城换谷 ====================
@bp.route("/exchanges")
@login_required
def exchanges():
    """换谷板：所有人的信息都在这里，我发的可以改/撤下。"""
    items = _exchanges_of(current_user)
    return render_template(
        "exchanges.html",
        exchanges=items,
        total_items=len(items),
        mine_total=sum(1 for e in items if e.user_id == current_user.id),
        same_region=sum(1 for e in items
                        if e.user_id != current_user.id
                        and regions.is_same_city(current_user.region, e.region)),
    )


def _read_exchange_form(item):
    """把表单读进 Exchange；求换内容必填。返回错误文案或 None。"""
    title = _clean(request.form.get("title"), Exchange.TITLE_MAX)
    if not title:
        return "请填写想换什么"

    item.title = title
    item.want = _clean(request.form.get("want"), Exchange.WANT_MAX) or None
    item.emoji = _clean(request.form.get("emoji"), EMOJI_MAX) or "🐰"
    # 距离填非法就退回默认 1.0，不让它变成 0 或 None
    distance = _parse_price(request.form.get("distance_km"))
    item.distance_km = distance if distance else 1.0
    item.place = _clean(request.form.get("place"), Exchange.PLACE_MAX) or None
    item.contact = _clean(request.form.get("contact"), Exchange.CONTACT_MAX) or None
    item.note = _clean(request.form.get("note"), Exchange.NOTE_MAX) or None
    # 地区跟着发布者当前的地区走（他以后改地区，旧信息仍然挂在发布时的地区上）
    item.region = (current_user.region or "").strip() or None
    return None


@bp.route("/exchanges/add", methods=["POST"])
@login_required
def add_exchange():
    item = Exchange(user_id=current_user.id, title="")
    error = _read_exchange_form(item)
    if error:
        flash(error, "error")
        return _redirect_back()

    db.session.add(item)
    db.session.commit()
    flash(f"已发布换谷信息「{item.title}」", "success")
    return _redirect_back()


@bp.route("/exchanges/<int:exchange_id>/edit", methods=["POST"])
@login_required
def edit_exchange(exchange_id):
    # 只能改自己发的
    item = Exchange.query.filter_by(id=exchange_id,
                                    user_id=current_user.id).first_or_404()
    error = _read_exchange_form(item)
    if error:
        flash(error, "error")
        return _redirect_back()

    db.session.commit()
    flash(f"已更新换谷信息「{item.title}」", "success")
    return _redirect_back()


@bp.route("/exchanges/<int:exchange_id>/delete", methods=["POST"])
@login_required
def delete_exchange(exchange_id):
    """撤下：只能撤自己发的。"""
    item = Exchange.query.filter_by(id=exchange_id,
                                    user_id=current_user.id).first_or_404()
    title = item.title
    db.session.delete(item)
    db.session.commit()
    flash(f"已撤下换谷信息「{title}」", "success")
    return _redirect_back()


# ==================== 认证 ====================
# 登录失败限流：按「用户名 + 来源 IP」计数，连续失败到上限就锁一段时间。
# 存在进程内存里：自用单进程够用，重启清零（不落库、不做多进程共享）。
_LOGIN_FAILS = {}
LOGIN_MAX_FAILS = 5
LOGIN_LOCK_SECONDS = 15 * 60


def _prune_login_fails():
    """顺手清掉过期的记录，别让这个字典无限长大。"""
    lock = current_app.config["LOGIN_LOCK_SECONDS"]
    now = time.time()
    for key in [k for k, (_n, last) in _LOGIN_FAILS.items() if now - last > lock]:
        _LOGIN_FAILS.pop(key, None)


def _login_key():
    return f"{_clean(request.form.get('username'), USERNAME_MAX)}|{request.remote_addr}"


def _login_locked_for():
    """还要等多少秒才能再试；0 表示没被锁。"""
    key = _login_key()
    entry = _LOGIN_FAILS.get(key)
    if not entry:
        return 0
    count, last = entry
    if count < current_app.config["LOGIN_MAX_FAILS"]:
        return 0
    left = current_app.config["LOGIN_LOCK_SECONDS"] - (time.time() - last)
    return int(left) + 1 if left > 0 else 0


def _note_login_fail():
    key = _login_key()
    count = _LOGIN_FAILS.get(key, (0, 0))[0]
    _LOGIN_FAILS[key] = (count + 1, time.time())
    _prune_login_fails()


def _forget_login_fails():
    _LOGIN_FAILS.pop(_login_key(), None)


def _start_session(user):
    """登录成功后：记下这次会话的令牌 + 上次登录时间。"""
    login_user(user)
    session["st"] = user.session_token or ""
    user.last_login_at = utcnow()
    db.session.commit()


def _rotate_session(user):
    """换一个新的会话令牌：其它设备上的登录态立刻失效，当前这台继续可用。"""
    user.session_token = secrets.token_hex(16)
    db.session.commit()
    session["st"] = user.session_token


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    if request.method == "POST":
        wait = _login_locked_for()
        if wait:
            flash(f"密码错误次数太多，请 {_wait_text(wait)}后再试", "error")
            return render_template("login.html")
        username = _clean(request.form.get("username"), USERNAME_MAX)
        password = request.form.get("password") or ""
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            _forget_login_fails()
            _start_session(user)
            return redirect(url_for("main.index"))
        _note_login_fail()
        # 不区分「用户不存在」和「密码错误」，避免用户名枚举。
        flash("用户名或密码错误", "error")
    return render_template("login.html")


def _wait_text(seconds):
    if seconds >= 60:
        return f"{seconds // 60} 分钟"
    return f"{seconds} 秒"


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    if request.method == "POST":
        username = _clean(request.form.get("username"), USERNAME_MAX)
        password = request.form.get("password") or ""
        raw_len = len((request.form.get("username") or "").strip())
        if not username or not password:
            flash("请填写用户名和密码", "error")
        elif raw_len > USERNAME_MAX:
            # 不能靠 _clean 的截断：那样会拿被剪短的名字注册，用户不知道
            flash(f"用户名最多 {USERNAME_MAX} 个字符", "error")
        elif not USERNAME_MIN <= len(username) <= USERNAME_MAX:
            flash(f"用户名长度需在 {USERNAME_MIN}-{USERNAME_MAX} 个字符之间", "error")
        elif len(password) < PASSWORD_MIN:
            flash(f"密码至少 {PASSWORD_MIN} 位", "error")
        elif User.query.filter_by(username=username).first():
            flash("用户名已被占用", "error")
        else:
            user = User(username=username)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            _start_session(user)          # 顺便记下首次登录时间/会话令牌
            return redirect(url_for("main.index"))
    return render_template("register.html")


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    # 只接受 POST：GET 链接会被 <img src> 之类的跨站请求顺手触发登出。
    logout_user()
    return redirect(url_for("main.login"))


# ==================== 个人信息 ====================
@bp.route("/profile")
@login_required
def profile_page():
    """个人信息：只读列表（像微信资料页），点哪一项进哪个编辑页。

    这一页刻意不放任何表单——保存动作都在子编辑页里。
    """
    return render_template(
        "profile.html",
        region_text=regions.display(current_user.region),
        item_count=current_user.items.count(),
    )


@bp.route("/profile/avatar", methods=["GET", "POST"])
@login_required
def profile_avatar():
    """头像编辑页：GET 出表单，POST 换/删。走和谷子照片完全一样的校验流程。"""
    if request.method == "POST":
        if request.form.get("remove_avatar"):
            stale, new_name = current_user.avatar, None
        else:
            upload = request.files.get("avatar")
            if not upload or not upload.filename:
                flash("请先选一张图片", "error")
                return _redirect_back()
            new_name = _save_image(upload)
            if not new_name:
                flash("图片格式不支持（仅支持 png/jpg/gif/webp），换一张试试", "error")
                return _redirect_back()
            stale = current_user.avatar
        current_user.avatar = new_name
        db.session.commit()
        # 提交成功后再删旧文件，避免回滚了文件却没了
        if stale and stale != new_name:
            _delete_image(stale)
        flash("头像已更新" if new_name else "头像已删除", "success")
        return _redirect_back()
    return render_template("profile_avatar.html")


@bp.route("/profile/username", methods=["GET", "POST"])
@login_required
def profile_username():
    """名字编辑页。"""
    if request.method == "POST":
        new = _clean(request.form.get("username"), USERNAME_MAX)
        # 长度先按用户原样输入判断：_clean 会截断，直接用它的话
        # 超长用户名会被悄悄剪短成另一个名字，用户根本不知道自己登的是什么
        raw_len = len((request.form.get("username") or "").strip())
        if not new or not USERNAME_MIN <= len(new) <= USERNAME_MAX:
            flash(f"用户名长度需在 {USERNAME_MIN}-{USERNAME_MAX} 个字符之间", "error")
        elif raw_len > USERNAME_MAX:
            flash(f"用户名最多 {USERNAME_MAX} 个字符", "error")
        elif new == current_user.username:
            flash("新用户名和现在的一样，没改", "error")
        elif User.query.filter(User.username == new,
                               User.id != current_user.id).first():
            flash("用户名已被占用", "error")
        else:
            old = current_user.username
            current_user.username = new
            db.session.commit()
            flash(f"用户名已从「{old}」改为「{new}」", "success")
        return _redirect_back()
    return render_template("profile_username.html",
                           username_min=USERNAME_MIN, username_max=USERNAME_MAX)


@bp.route("/profile/signature", methods=["GET", "POST"])
@login_required
def profile_signature():
    """个性签名编辑页。"""
    if request.method == "POST":
        text = _clean(request.form.get("signature"), User.SIGNATURE_MAX)
        current_user.signature = text or None
        db.session.commit()
        flash("签名已更新" if text else "签名已清空", "success")
        return _redirect_back()
    return render_template("profile_signature.html",
                           signature_max=User.SIGNATURE_MAX)


@bp.route("/profile/region", methods=["GET", "POST"])
@login_required
def profile_region():
    """地区选择页：国家 → 省 → 市 → 区/县 四级，或者按定位挑最近的市。

    四个下拉的选项由服务端先渲染一份（没有 JS 也能选到省市），
    前端拿到 static/regions.json 后再接管联动。
    """
    if request.method == "GET":
        resolved = regions.resolve(current_user.region)
        matched = bool(resolved["province"]) and regions.is_valid(
            resolved["country"], resolved["province"],
            resolved["city"], resolved["area"])
        manual_text = ""
        if current_user.region and not matched:
            # 以前自己写的、数据里没有的写法：放到手动填写里，别给人弄丢了
            resolved = dict(resolved, country=regions.MANUAL_VALUE)
            manual_text = current_user.region
        return render_template(
            "profile_region.html",
            region=resolved,
            manual_text=manual_text,
            manual_value=regions.MANUAL_VALUE,
            country_options=regions.countries(),
            province_options=regions.provinces(resolved["country"])
            if resolved["country"] != regions.MANUAL_VALUE else [],
            city_options=regions.cities(resolved["country"], resolved["province"]),
            area_options=regions.areas(resolved["country"], resolved["province"],
                                       resolved["city"]),
            tree_url=url_for("static", filename="regions.json"),
        )

    lat = request.form.get("lat")
    lon = request.form.get("lon")
    if lat is not None and lon is not None:
        # 浏览器定位：只收坐标，换算在服务端做（表在服务端，前端不用抄一份）
        try:
            flat, flon = float(lat), float(lon)
        except (TypeError, ValueError):
            flash("定位坐标不合法，请手动选地区", "error")
            return _redirect_back()
        if not (-90 <= flat <= 90 and -180 <= flon <= 180):
            flash("定位坐标不合法，请手动选地区", "error")
            return _redirect_back()
        path, km = regions.nearest(flat, flon)
        value = regions.build_value(path["country"], path["province"],
                                    path["city"], path["area"])
        current_user.region = value
        db.session.commit()
        flash(f"已按当前位置设为「{value}」（离你最近的城市，约 {km} 公里，"
              f"区/县可以再自己补）", "success")
        return _redirect_back()

    country = _clean(request.form.get("country"), 32)
    if country == regions.MANUAL_VALUE:
        raw = _clean(request.form.get("region_manual"), User.REGION_MAX)
        if not raw:
            flash("请填写地区", "error")
        else:
            current_user.region = raw
            db.session.commit()
            flash(f"地区已设为「{raw}」", "success")
        return _redirect_back()

    province = _clean(request.form.get("province"), 32)
    city = _clean(request.form.get("city"), 32)
    area = _clean(request.form.get("area"), 32)
    if not country or not province:
        flash("请至少选到省份；列表里没有的话，选「其他（手动填写）」自己填", "error")
    elif not regions.is_valid(country, province, city, area):
        flash("这个地区组合不在数据里，请重新选择", "error")
    else:
        value = regions.build_value(country, province, city, area)
        current_user.region = value
        db.session.commit()
        flash(f"地区已设为「{value}」", "success")
    return _redirect_back()


# ==================== 账号与数据 ====================
@bp.route("/account-data")
@login_required
def account_data_page():
    """账号与数据：账号与安全 / 数据备份两块的入口。

    和「个人信息」分开成两页：个人信息只回答「我是谁」（头像、用户名、地区），
    这里放「账号本身和数据」的事。
    """
    return render_template(
        "account_data.html",
        item_count=current_user.items.count(),
        backup_total=len(backup.list_backups()),
    )


# ==================== 账号与安全 ====================
@bp.route("/account")
@login_required
def account_page():
    """账号与安全：只列选项，具体服务点进去再做。"""
    return render_template(
        "account.html",
        max_fails=current_app.config["LOGIN_MAX_FAILS"],
        lock_minutes=current_app.config["LOGIN_LOCK_SECONDS"] // 60,
    )


@bp.route("/account/password", methods=["GET", "POST"])
@login_required
def account_password():
    """修改密码。必须验旧密码，改完把其它设备踢下线。"""
    if request.method == "POST":
        old = request.form.get("old_password") or ""
        new = request.form.get("new_password") or ""
        confirm = request.form.get("confirm_password") or ""
        if not current_user.check_password(old):
            flash("当前密码不对", "error")
        elif len(new) < PASSWORD_MIN:
            flash(f"新密码至少 {PASSWORD_MIN} 位", "error")
        elif new != confirm:
            flash("两次输入的新密码不一致", "error")
        elif new == old:
            flash("新密码和当前密码一样，没改", "error")
        else:
            current_user.set_password(new)
            # 轮换会话令牌：别的设备上还开着的页面下次点击就会被要求重新登录
            _rotate_session(current_user)
            flash("密码已修改，其它设备上的登录已失效（这台不受影响）", "success")
            return redirect(url_for("main.account_page"))
    return render_template("account_password.html", password_min=PASSWORD_MIN)


@bp.route("/account/sessions", methods=["GET", "POST"])
@login_required
def account_sessions():
    """登录与设备：看上次登录、退出其它设备。"""
    if request.method == "POST":
        _rotate_session(current_user)
        flash("已退出其它设备上的登录（这台继续用）", "success")
        return redirect(url_for("main.account_page"))
    return render_template(
        "account_sessions.html",
        max_fails=current_app.config["LOGIN_MAX_FAILS"],
        lock_minutes=current_app.config["LOGIN_LOCK_SECONDS"] // 60,
    )


# ==================== 谷子 CRUD ====================
@bp.route("/items/add", methods=["POST"])
@login_required
def add_item():
    name = _clean(request.form.get("name"), NAME_MAX)
    if not name:
        flash("请填写谷子名称", "error")
        return _redirect_back()

    status = request.form.get("status") or GuItem.DEFAULT_STATUS
    if status not in GuItem.STATUS_LABELS:
        # 白名单校验：否则库里的野状态不计入任何分类，统计面板会对不上。
        status = GuItem.DEFAULT_STATUS

    upload = request.files.get("image")
    image = _save_image(upload)

    item = GuItem(
        user_id=current_user.id,
        name=name,
        emoji=_clean(request.form.get("emoji"), EMOJI_MAX) or "🎁",
        image=image,
        count=_parse_count(request.form.get("count")),
        sold_count=_parse_sold_count(request.form.get("sold_count")),
        status=status,
        category_type_id=_pick_category_id(request.form.get("category_type_id"),
                                           "type", current_user.id),
        category_ip_id=_pick_category_id(request.form.get("category_ip_id"),
                                         "ip", current_user.id),
        # 在途专属字段：不填就是 None，填了就一直留着
        expect_date=_parse_date(request.form.get("expect_date")),
        channel=_parse_channel(request.form.get("channel")),
        order_no=_clean(request.form.get("order_no"), 64) or None,
        price=_parse_price(request.form.get("price")),
        # 已出专属字段
        sold_price=_parse_price(request.form.get("sold_price")),
        sold_method=_parse_sold_method(request.form.get("sold_method")),
        sold_channel=_parse_sale_channel(request.form.get("sold_channel")),
        sold_at=_parse_date(request.form.get("sold_at")),
        # 心愿单专属字段
        want_level=_parse_want_level(request.form.get("want_level")),
        budget=_parse_price(request.form.get("budget")),
        want_link=_clean(request.form.get("want_link"), 255) or None,
        want_note=_clean(request.form.get("want_note"), 120) or None,
    )
    # 件数/状态对齐（部分已出、整件出完的判定都在这里）
    _normalize_quantity(item)
    db.session.add(item)
    db.session.commit()

    # 图片不合法时仍然保存文字信息，只是提醒一声——不能让用户白填一遍表单。
    if upload and upload.filename and not image:
        flash(f"已添加「{name}」，但图片格式不支持（仅支持 png/jpg/gif/webp），已用 emoji 代替",
              "error")
    else:
        flash(f"已添加「{name}」", "success")
    # 加号现在每个页面都有：从哪个页面加的，就回哪个页面（表单里带着 next）
    return _redirect_back()


@bp.route("/items/<int:item_id>/edit", methods=["POST"])
@login_required
def edit_item(item_id):
    # 同样带 user_id 过滤，别人的谷子改不动。
    item = GuItem.query.filter_by(id=item_id,
                                  user_id=current_user.id).first_or_404()

    name = _clean(request.form.get("name"), NAME_MAX)
    if not name:
        flash("请填写谷子名称", "error")
        return _redirect_back()

    status = request.form.get("status") or GuItem.DEFAULT_STATUS
    if status not in GuItem.STATUS_LABELS:
        status = GuItem.DEFAULT_STATUS

    item.name = name
    item.status = status
    item.emoji = _clean(request.form.get("emoji"), EMOJI_MAX) or "🎁"
    item.count = _parse_count(request.form.get("count"))
    item.sold_count = _parse_sold_count(request.form.get("sold_count"))
    # 下拉里选空 = 未分类；选到不属于自己的分类会被 _pick_category_id 丢弃
    item.category_type_id = _pick_category_id(
        request.form.get("category_type_id"), "type", current_user.id)
    item.category_ip_id = _pick_category_id(
        request.form.get("category_ip_id"), "ip", current_user.id)
    # 在途字段：表单里是隐藏的也照样提交（JS 会把原值填进去），所以不会误清空
    item.expect_date = _parse_date(request.form.get("expect_date"))
    item.channel = _parse_channel(request.form.get("channel"))
    item.order_no = _clean(request.form.get("order_no"), 64) or None
    item.price = _parse_price(request.form.get("price"))
    # 已出字段同理
    item.sold_price = _parse_price(request.form.get("sold_price"))
    item.sold_method = _parse_sold_method(request.form.get("sold_method"))
    item.sold_channel = _parse_sale_channel(request.form.get("sold_channel"))
    item.sold_at = _parse_date(request.form.get("sold_at"))
    # 心愿单字段
    item.want_level = _parse_want_level(request.form.get("want_level"))
    item.budget = _parse_price(request.form.get("budget"))
    item.want_link = _clean(request.form.get("want_link"), 255) or None
    item.want_note = _clean(request.form.get("want_note"), 120) or None
    # 件数/状态对齐：出了几件、是不是全出完了，都在这里定
    _normalize_quantity(item)

    old_image = item.image
    upload = request.files.get("image")
    new_image = _save_image(upload)
    if new_image:
        item.image = new_image            # 换图优先
    elif upload and upload.filename:
        # 传了新图但不合法：保留原照片，别把已有图片弄丢
        flash("新图片格式不支持，已保留原来的照片", "error")
    elif request.form.get("remove_image"):
        item.image = None                 # 明确勾了「删除照片」

    stale = old_image if old_image != item.image else None
    db.session.commit()
    # 提交成功后再删旧文件，避免数据库回滚了文件却没了
    _delete_image(stale)
    flash(f"已更新「{name}」", "success")
    # 详情弹窗在哪个页面打开的就回哪个页面
    return _redirect_back()


@bp.route("/items/<int:item_id>/arrive", methods=["POST"])
@login_required
def arrive_item(item_id):
    """一键确认到货：在途 → 展示中，并记下到货时间。"""
    item = GuItem.query.filter_by(id=item_id,
                                  user_id=current_user.id).first_or_404()
    if item.status != "in_transit":
        flash(f"「{item.name}」已经不在途了", "error")
        return _redirect_back()

    item.status = "displaying"
    item.arrived_at = utcnow()
    db.session.commit()
    flash(f"「{item.name}」已确认到货，状态改为展示中", "success")
    return _redirect_back()


@bp.route("/items/<int:item_id>/restore", methods=["POST"])
@login_required
def restore_item(item_id):
    """一键撤回：已出 → 展示中，回到收藏（出手记录保留，方便以后回看）。"""
    item = GuItem.query.filter_by(id=item_id,
                                  user_id=current_user.id).first_or_404()
    if item.status != "sold" and item.sold_qty == 0:
        flash(f"「{item.name}」没有出货记录", "error")
        return _redirect_back()

    item.status = "displaying"
    # 撤回 = 这次出货作废，所以已出件数一并清零，否则它会因为「没剩」又被藏起来
    item.sold_count = 0
    db.session.commit()
    flash(f"「{item.name}」已撤回，回到展示中", "success")
    return _redirect_back()


@bp.route("/items/<int:item_id>/acquire", methods=["POST"])
@login_required
def acquire_item(item_id):
    """一键「已入手」：心愿单 → 展示中，正式进谷柜。"""
    item = GuItem.query.filter_by(id=item_id,
                                  user_id=current_user.id).first_or_404()
    if item.status != "wishlist":
        flash(f"「{item.name}」不在心愿单里", "error")
        return _redirect_back()

    item.status = "displaying"
    db.session.commit()
    flash(f"「{item.name}」已入手，进谷柜啦", "success")
    return _redirect_back()


@bp.route("/items/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_item(item_id):
    # 带 user_id 过滤，别人的谷子删不掉。
    item = GuItem.query.filter_by(id=item_id,
                                  user_id=current_user.id).first_or_404()
    name = item.name
    image = item.image
    db.session.delete(item)
    db.session.commit()
    # 提交成功后再删文件，否则数据库回滚了文件却没了。
    _delete_image(image)
    flash(f"已删除「{name}」", "success")
    return redirect(url_for("main.index"))


# ==================== 数据备份 / 导出 / 导入 ====================
@bp.before_request
def auto_daily_backup():
    """每天自动备份一次数据库（同一天只在第一个请求时做）。"""
    backup.ensure_daily_backup()


@bp.route("/backup")
@login_required
def backup_page():
    """数据安全页：导出、导入、立即备份、看已有的备份。"""
    rows = backup.list_backups()
    owner = User.query.order_by(User.id).first()
    return render_template(
        "backup.html",
        backups=[{"name": name, "size": size,
                  "time_text": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")}
                 for name, size, mtime in rows],
        can_download=bool(owner) and current_user.id == owner.id,
        backup_total=len(rows),
        keep_backups=backup.KEEP_BACKUPS,
        item_count=current_user.items.count(),
        photo_count=current_user.items.filter(
            GuItem.image.isnot(None)).count(),
    )


@bp.route("/backup/export")
@login_required
def export_data():
    """把当前账号的数据 + 照片打包下载。"""
    buf, filename = backup.build_export(current_user)
    return send_file(buf, mimetype="application/zip",
                     as_attachment=True, download_name=filename)


@bp.route("/backup/import", methods=["POST"])
@login_required
def import_data():
    """导入备份包：只追加，不删除、不覆盖已有数据。"""
    # 导出包里带着照片，可能远超图片上传那 5MB，所以只给这一个请求放宽上限。
    # 必须写在读 request.files 之前：请求体是在第一次访问时才解析的。
    request.max_content_length = 200 * 1024 * 1024
    upload = request.files.get("file")
    try:
        result = backup.import_export(current_user, upload, _save_image)
    except ValueError as exc:
        flash(str(exc), "error")
        return _redirect_back()
    except Exception:                             # noqa: BLE001 —— 坏包不该 500
        db.session.rollback()
        current_app.logger.exception("导入备份失败")
        flash("导入失败：备份包读不出来，已保持原样", "error")
        return _redirect_back()

    profile = result.get("profile") or []
    extra = f"，个人资料恢复了：{'、'.join(profile)}" if profile else ""
    flash(f"导入完成：{result['items']} 件谷子（含 {result['photos']} 张照片）、"
          f"{result['categories']} 个分类、{result['reminders']} 条提醒、"
          f"{result['exchanges']} 条换谷信息{extra}。原有数据没有被改动。", "success")
    return _redirect_back()


@bp.route("/backup/now", methods=["POST"])
@login_required
def backup_now():
    """手动立刻备份一份。"""
    path = backup.make_db_backup(reason="manual")
    if path:
        flash(f"已备份：{os.path.basename(path)}", "success")
    else:
        flash("备份失败，请看看控制台日志", "error")
    return _redirect_back()


@bp.route("/backup/download/<name>")
@login_required
def download_backup(name):
    """下载某一份整库备份。

    整库备份里含所有账号的数据，所以只允许主账号（最早注册的那个）下载，
    别的账号一律 404——不然同一个实例里换个账号就能把别人的数据拿走。
    自己的数据可以用「导出」拿到，那份只含自己的。
    """
    owner = User.query.order_by(User.id).first()
    if not owner or current_user.id != owner.id:
        abort(404)
    if os.path.basename(name) != name:
        abort(404)
    allowed = {row[0] for row in backup.list_backups()}
    if name not in allowed:
        abort(404)
    return send_file(os.path.join(backup.backup_dir(), name),
                     mimetype="application/octet-stream",
                     as_attachment=True, download_name=name)


# ==================== REST API ====================
@bp.route("/api/stats")
@login_required
def api_stats():
    return jsonify(current_user.get_stats())


@bp.route("/api/items")
@login_required
def api_items():
    items = current_user.items.order_by(GuItem.created_at.desc()).all()
    return jsonify([
        {
            "id": i.id,
            "name": i.name,
            "emoji": i.emoji,
            "image": i.image,
            "image_url": (url_for("static", filename=f"uploads/{i.image}")
                          if i.image else None),
            "count": i.count,
            "sold_count": i.sold_qty,
            "remaining": i.remaining,
            "partially_sold": i.partially_sold,
            "visible": not i.fully_sold,
            "status": i.status,
            "status_text": i.status_text,
            "category_type_id": i.category_type_id,
            "category_type": i.category_name("type"),
            "category_ip_id": i.category_ip_id,
            "category_ip": i.category_name("ip"),
            # 在途信息
            "expect_date": (i.expect_date.isoformat() if i.expect_date else None),
            "days_to_arrive": i.days_to_arrive,
            "overdue": i.overdue,
            "channel": i.channel,
            "channel_text": i.channel_text,
            "order_no": i.order_no,
            "price": i.price,
            "arrived_at": (i.arrived_at.isoformat() if i.arrived_at else None),
            # 已出信息
            "sold_price": i.sold_price,
            "sold_method": i.sold_method,
            "sold_method_text": i.sold_method_text,
            "sold_channel": i.sold_channel,
            "sold_channel_text": i.sold_channel_text,
            "sold_at": (i.sold_at.isoformat() if i.sold_at else None),
            "profit": i.profit,
            "profit_text": i.profit_text,
            # 心愿单信息
            "want_level": i.want_level,
            "want_level_text": i.want_level_text,
            "budget": i.budget,
            "want_link": i.want_link,
            "want_note": i.want_note,
        }
        for i in items
    ])
