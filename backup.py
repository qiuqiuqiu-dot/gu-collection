"""数据安全：数据库自动备份 + 导出 / 导入。

三件事：

1. **自动备份**：用 SQLite 官方的 backup API 复制到 instance/backups/，
   不是直接拷文件——正在写的时候裸拷有可能拿到写了一半的库。
   启动时做一次，之后每天一次，只保留最近 KEEP 份。

2. **导出**：当前账号的谷子 / 分类 / 提醒 / 换谷信息 + 照片，打成一个 zip 下载。
   照片只带上他自己的（别人照片不进来）。

3. **导入**：只做「追加」。绝不删除、绝不覆盖已有数据——
   恢复账号是低频高危操作，宁可多出来几条让人自己删，也不能悄悄吃掉现有数据。
"""
import io
import json
import os
import sqlite3
import zipfile
from datetime import datetime

from flask import current_app
from models import (Category, Exchange, GuItem, Reminder, User, db, utcnow)
from werkzeug.datastructures import FileStorage

BACKUP_DIRNAME = "backups"
KEEP_BACKUPS = 14          # 自动备份最多留几份
FORMAT_VERSION = 1         # 导出文件格式版本
MAX_IMPORT_ITEMS = 5000    # 一次最多导入多少件，防止塞爆
MAX_ENTRY_BYTES = 12 * 1024 * 1024      # 单个照片解压后的大小上限
MAX_TOTAL_BYTES = 200 * 1024 * 1024     # 整个压缩包解压后的总大小上限


def backup_dir(app=None):
    """备份目录。默认 instance/backups；测试里用 BACKUP_FOLDER 指到临时目录，
    免得测试往真实数据目录里写东西。"""
    app = app or current_app
    path = app.config.get("BACKUP_FOLDER") or os.path.join(app.instance_path,
                                                           BACKUP_DIRNAME)
    os.makedirs(path, exist_ok=True)
    return path


def db_file_path(app=None):
    """把 SQLALCHEMY_DATABASE_URI 解析成本地数据库文件路径。

    关键点：像 `sqlite:///gu.db` 这样的相对路径，Flask-SQLAlchemy 是相对
    instance/ 目录解析的（真实文件是 instance/gu.db）。这里必须跟着同样规则，
    否则会去找 CWD 下的 gu.db，找不到就静默不备份——线上踩过这个坑。
    非 sqlite（或没配）返回 None。
    """
    app = app or current_app
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if not uri.startswith("sqlite:///"):
        return None                      # 换成别的数据库时这套就不适用了
    path = uri[len("sqlite:///"):].replace("/", os.sep)
    if not os.path.isabs(path):
        path = os.path.join(app.instance_path, path)
    return path


def make_db_backup(app=None, reason="auto"):
    """把当前数据库备份一份，返回备份文件路径；失败返回 None。

    调用方（启动 / 每日钩子）不应该因为备份失败就崩掉整个请求，
    所以这里把异常咽掉，只留一条日志。
    """
    app = app or current_app
    src_path = db_file_path(app)
    if not src_path or not os.path.exists(src_path):
        return None

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(backup_dir(app), f"gu-{stamp}-{reason}.db")
    try:
        # 官方 backup API：会在源库上加共享锁、分页拷贝，比裸拷安全
        src = sqlite3.connect(src_path)
        try:
            dst = sqlite3.connect(dest)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
    except Exception as exc:                      # noqa: BLE001 —— 备份失败不该影响主流程
        app.logger.warning("数据库备份失败：%s", exc)
        if os.path.exists(dest):
            os.remove(dest)
        return None

    rotate_backups(app)
    return dest


def list_backups(app=None):
    """返回 [(文件名, 字节数, 修改时间), ...]，新的在前。"""
    app = app or current_app
    out = []
    for name in os.listdir(backup_dir(app)):
        if not name.endswith(".db"):
            continue
        path = os.path.join(backup_dir(app), name)
        if os.path.isfile(path):
            out.append((name, os.path.getsize(path), os.path.getmtime(path)))
    out.sort(key=lambda row: row[2], reverse=True)
    return out


def rotate_backups(app=None, keep=KEEP_BACKUPS):
    """只留最近 keep 份，多的删掉。返回删了几个。"""
    app = app or current_app
    removed = 0
    for name, _size, _mtime in list_backups(app)[keep:]:
        try:
            os.remove(os.path.join(backup_dir(app), name))
            removed += 1
        except OSError:
            pass
    return removed


#: 记住「今天已经检查过自动备份了」，避免每个请求都去 stat 一遍磁盘
_last_daily_check = None


def ensure_daily_backup(app=None):
    """每天至少有一份备份；同一天里只在第一次请求时检查。"""
    global _last_daily_check
    app = app or current_app
    if app.config.get("TESTING") or not app.config.get("AUTO_BACKUP", True):
        return None
    today = datetime.now().strftime("%Y%m%d")
    if _last_daily_check == today:
        return None
    _last_daily_check = today
    stamp = datetime.now().strftime("%Y%m%d")
    if any(name.startswith(f"gu-{stamp}-") for name, _s, _m in list_backups(app)):
        return None                      # 今天已经有了
    return make_db_backup(app, reason="daily")


# ==================== 导出 ====================

def _dt(value):
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else None


def _day(value):
    return value.strftime("%Y-%m-%d") if value else None


def _minutes(value):
    return value.strftime("%Y-%m-%d %H:%M") if value else None


def build_export(user):
    """打包当前账号的数据 + 照片，返回 (BytesIO, 建议文件名)。"""
    cats = {c.id: c.name for c in user.categories}

    items = []
    for it in user.items.order_by(GuItem.id).all():
        items.append({
            "name": it.name,
            "emoji": it.emoji,
            "image": it.image,
            "count": it.count,
            "sold_count": it.sold_count,
            "status": it.status,
            "category_type": cats.get(it.category_type_id),
            "category_ip": cats.get(it.category_ip_id),
            "expect_date": _day(it.expect_date),
            "channel": it.channel,
            "order_no": it.order_no,
            "price": it.price,
            "sold_price": it.sold_price,
            "sold_method": it.sold_method,
            "sold_channel": it.sold_channel,
            "sold_at": _day(it.sold_at),
            "want_level": it.want_level,
            "budget": it.budget,
            "want_link": it.want_link,
            "want_note": it.want_note,
            "color_start": it.color_start,
            "color_end": it.color_end,
            "created_at": _dt(it.created_at),
        })

    data = {
        "app": "谷仓",
        "format": FORMAT_VERSION,
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "username": user.username,
        # 个人资料也要带上：不然换台电脑恢复，头像和签名就没了
        "region": user.region,
        "signature": user.signature,
        "avatar": user.avatar,
        "categories": [{"kind": c.kind, "name": c.name, "created_at": _dt(c.created_at)}
                       for c in user.categories.order_by(Category.id).all()],
        "items": items,
        "reminders": [{
            "title": r.title, "subtitle": r.subtitle,
            "event_time": _minutes(r.event_time), "priority": r.priority,
            "link": r.link,
        } for r in user.reminders.order_by(Reminder.id).all()],
        # 换谷功能现在关着，但数据还在，一并导出，免得以后开回来发现丢过
        "exchanges": [{
            "emoji": e.emoji, "title": e.title, "want": e.want,
            "distance_km": e.distance_km, "region": e.region, "place": e.place,
            "contact": e.contact, "note": e.note,
            "color_start": e.color_start, "color_end": e.color_end,
            "created_at": _dt(e.created_at),
        } for e in user.exchanges.order_by(Exchange.id).all()],
    }

    uploads = current_app.config["UPLOAD_FOLDER"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("data.json", json.dumps(data, ensure_ascii=False, indent=2))
        for it in items:
            name = it["image"]
            # 文件名是服务端生成的 uuid，这里再挡一层路径穿越
            if not name or os.path.basename(name) != name:
                continue
            path = os.path.join(uploads, name)
            if os.path.exists(path):
                zf.write(path, f"photos/{name}")
        # 头像也一起打包，放在 avatar/ 下和谷子照片区分开
        if user.avatar and os.path.basename(user.avatar) == user.avatar:
            path = os.path.join(uploads, user.avatar)
            if os.path.exists(path):
                zf.write(path, f"avatar/{user.avatar}")
    buf.seek(0)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return buf, f"谷仓-{user.username}-{stamp}.zip"


# ==================== 导入 ====================

def _parse_dt(text, with_time=True):
    """宽松解析导出文件里的日期；认不出来就当没填。"""
    if not text:
        return None
    text = str(text).strip()
    formats = (["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M", "%Y-%m-%d"] if with_time
               else ["%Y-%m-%d"])
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _num(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value, default=1):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _text(value, limit):
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def read_export(file):
    """把上传的 zip 读成 dict；不合法就抛 ValueError（带用户看得懂的话）。

    成功时把打开的 ZipFile 一起返回，由调用方负责关——
    不能在这里用 with 包住再 return：那样一返回压缩包就被关掉了，
    后面读照片会报 "ZIP archive that was already closed"。
    """
    if not file or not file.filename:
        raise ValueError("请选择要导入的备份文件")
    raw = file.read()
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise ValueError("这个文件不是备份包（应该是以 .zip 结尾的导出文件）")

    try:
        names = zf.namelist()
        if "data.json" not in names:
            raise ValueError("备份包里没有 data.json，可能不是谷仓导出的文件")
        # 防解压炸弹：先按元数据算总大小，不老实解压
        total = sum(i.file_size for i in zf.infolist())
        if total > MAX_TOTAL_BYTES:
            raise ValueError("备份包解压后太大了，已拒绝")
        try:
            data = json.loads(zf.read("data.json").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("备份包里的 data.json 读不出来")
        if not isinstance(data, dict) or data.get("app") != "谷仓":
            raise ValueError("这不是谷仓导出的备份文件")
        if _int(data.get("format"), 0) > FORMAT_VERSION:
            raise ValueError("这个备份是更新版本导出的，当前程序读不了")
        photos = {n[len("photos/"):]: n for n in names
                  if n.startswith("photos/") and not n.endswith("/")}
    except Exception:
        zf.close()
        raise
    return data, zf, photos


def _import_photo(zf, photo_name, save_image):
    """把压缩包里的一张照片交给 _save_image 走正常校验/压缩流程。

    这样导入的图片和手动上传的走完全一样的白名单和像素上限，
    伪造的、损坏的、超大的图都会被挡掉并回落到 emoji。
    """
    info = zf.getinfo(photo_name)
    if info.file_size > MAX_ENTRY_BYTES:
        return None
    data = zf.read(photo_name)
    return save_image(FileStorage(stream=io.BytesIO(data),
                                 filename=os.path.basename(photo_name)))


def import_export(user, file, save_image):
    """把备份包追加导入到 user 名下，返回统计 dict。只增不删。"""
    data, zf, photos = read_export(file)
    try:
        return _import_data(user, data, zf, photos, save_image)
    finally:
        zf.close()


def _import_data(user, data, zf, photos, save_image):
    """真正干活的部分；zf 由调用方负责关。"""
    # 个人资料（地区/签名/头像）：只补空着的，绝不覆盖用户现在的设置——
    # 导入的语义是「追加」，连资料也一样
    restored = _restore_profile(user, data, zf, save_image)

    items = data.get("items") or []
    if not isinstance(items, list):
        raise ValueError("备份包里的谷子列表格式不对")
    if len(items) > MAX_IMPORT_ITEMS:
        raise ValueError(f"一次最多导入 {MAX_IMPORT_ITEMS} 件谷子")

    # 分类按「维度 + 名字」对齐：没有的就建，已有的复用（不重复建、不覆盖）
    existing = {(c.kind, c.name): c for c in user.categories}
    raw_cats = data.get("categories") or []
    for raw in raw_cats:
        if not isinstance(raw, dict):
            continue
        kind = raw.get("kind")
        name = _text(raw.get("name"), Category.NAME_MAX)
        if kind not in Category.KINDS or not name or (kind, name) in existing:
            continue
        cat = Category(user_id=user.id, kind=kind, name=name,
                       created_at=_parse_dt(raw.get("created_at")) or utcnow())
        db.session.add(cat)
        existing[(kind, name)] = cat          # 同一份备份里的重复名字也只建一个
    db.session.flush()                        # 拿到新分类的 id

    def cat_id(kind, name):
        cat = existing.get((kind, _text(name, Category.NAME_MAX)))
        return cat.id if cat else None

    added_items = added_photos = 0
    for raw in items:
        if not isinstance(raw, dict):
            continue
        name = _text(raw.get("name"), GuItem.NAME_MAX)
        if not name:
            continue
        image = None
        photo_key = _text(raw.get("image"), 255)
        if photo_key and photo_key in photos:
            image = _import_photo(zf, photos[photo_key], save_image)
            if image:
                added_photos += 1
        item = GuItem(
            user_id=user.id, name=name,
            emoji=_text(raw.get("emoji"), 8) or "🎁",
            image=image,
            count=max(_int(raw.get("count"), 1), 1),
            sold_count=max(_int(raw.get("sold_count"), 0), 0),
            status=raw.get("status") if raw.get("status") in GuItem.STATUS_LABELS
            else GuItem.DEFAULT_STATUS,
            category_type_id=cat_id("type", raw.get("category_type")),
            category_ip_id=cat_id("ip", raw.get("category_ip")),
            expect_date=_parse_dt(raw.get("expect_date"), with_time=False),
            channel=raw.get("channel") if raw.get("channel") in GuItem.CHANNEL_LABELS
            else None,
            order_no=_text(raw.get("order_no"), 64),
            price=_num(raw.get("price")),
            sold_price=_num(raw.get("sold_price")),
            sold_method=(raw.get("sold_method")
                         if raw.get("sold_method") in GuItem.SOLD_METHOD_LABELS else None),
            sold_channel=(raw.get("sold_channel")
                          if raw.get("sold_channel") in GuItem.SALE_CHANNEL_LABELS else None),
            sold_at=_parse_dt(raw.get("sold_at"), with_time=False),
            want_level=(raw.get("want_level")
                        if raw.get("want_level") in GuItem.WANT_LEVEL_LABELS else None),
            budget=_num(raw.get("budget")),
            want_link=_text(raw.get("want_link"), 255),
            want_note=_text(raw.get("want_note"), 120),
            color_start=_text(raw.get("color_start"), 16) or "#EAF3FF",
            color_end=_text(raw.get("color_end"), 16) or "#D9E9FF",
            created_at=_parse_dt(raw.get("created_at")) or utcnow(),
        )
        db.session.add(item)
        added_items += 1

    added_reminders = 0
    for raw in (data.get("reminders") or []):
        if not isinstance(raw, dict):
            continue
        title = _text(raw.get("title"), Reminder.TITLE_MAX)
        event_time = _parse_dt(raw.get("event_time"))
        if not title or not event_time:
            continue                      # 提醒必须有内容和时间，缺了没法用
        db.session.add(Reminder(
            user_id=user.id, title=title,
            subtitle=_text(raw.get("subtitle"), Reminder.SUBTITLE_MAX),
            event_time=event_time,
            priority=(raw.get("priority")
                      if raw.get("priority") in Reminder.PRIORITY_LABELS else "normal"),
            link=_text(raw.get("link"), 255),
        ))
        added_reminders += 1

    added_exchanges = 0
    for raw in (data.get("exchanges") or []):
        if not isinstance(raw, dict):
            continue
        title = _text(raw.get("title"), Exchange.TITLE_MAX)
        if not title:
            continue
        db.session.add(Exchange(
            user_id=user.id, title=title,
            emoji=_text(raw.get("emoji"), 8) or "🐰",
            want=_text(raw.get("want"), Exchange.WANT_MAX),
            distance_km=_num(raw.get("distance_km")) or 1.0,
            region=_text(raw.get("region"), Exchange.REGION_MAX),
            place=_text(raw.get("place"), Exchange.PLACE_MAX),
            contact=_text(raw.get("contact"), Exchange.CONTACT_MAX),
            note=_text(raw.get("note"), Exchange.NOTE_MAX),
            color_start=_text(raw.get("color_start"), 16) or "#EAF3FF",
            color_end=_text(raw.get("color_end"), 16) or "#D9E9FF",
            created_at=_parse_dt(raw.get("created_at")) or utcnow(),
        ))
        added_exchanges += 1

    db.session.commit()
    return {"items": added_items, "photos": added_photos,
            "categories": len(raw_cats) if isinstance(raw_cats, list) else 0,
            "reminders": added_reminders, "exchanges": added_exchanges,
            "profile": restored}


def _restore_profile(user, data, zf, save_image):
    """把备份里的个人资料补回**空着**的字段，返回恢复了哪些（中文名列表）。

    只补空、不覆盖：用户现在自己设了签名/头像/地区，导入一份老备份不该把它冲掉。
    换新机器时这些字段本来就是空的，所以能完整恢复。
    """
    restored = []
    if not user.region:
        region = _text(data.get("region"), User.REGION_MAX)
        if region:
            user.region = region
            restored.append("地区")
    if not user.signature:
        signature = _text(data.get("signature"), User.SIGNATURE_MAX)
        if signature:
            user.signature = signature
            restored.append("签名")
    if not user.avatar:
        name = _text(data.get("avatar"), 255)
        if name:
            # 新备份放在 avatar/ 下；老备份（如果有）里可能在 photos/ 下
            key = f"avatar/{name}"
            if key not in zf.namelist() and f"photos/{name}" in zf.namelist():
                key = f"photos/{name}"
            if key in zf.namelist():
                saved = _import_photo(zf, key, save_image)
                if saved:
                    user.avatar = saved
                    restored.append("头像")
    return restored
