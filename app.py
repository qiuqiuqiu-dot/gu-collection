"""应用工厂：创建并配置 Flask 应用。"""
import os

from flask import (Flask, flash, jsonify, redirect, request, session, url_for)
from flask_login import LoginManager
from flask_wtf.csrf import CSRFError, CSRFProtect
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text
from werkzeug.exceptions import RequestEntityTooLarge

from models import User, db

login_manager = LoginManager()
csrf = CSRFProtect()

DEV_SECRET = "dev-change-me"


# 老库需要补的列：(表, 列, 类型)。db.create_all() 只会建缺失的表，不会加列。
_COLUMN_ADDITIONS = (
    ("gu_items", "image", "VARCHAR(120)"),
    ("gu_items", "category_type_id", "INTEGER"),
    ("gu_items", "category_ip_id", "INTEGER"),
    # 在途相关
    ("gu_items", "expect_date", "DATE"),
    ("gu_items", "channel", "VARCHAR(16)"),
    ("gu_items", "order_no", "VARCHAR(64)"),
    ("gu_items", "price", "FLOAT"),
    ("gu_items", "arrived_at", "DATETIME"),
    # 已出相关
    ("gu_items", "sold_price", "FLOAT"),
    ("gu_items", "sold_method", "VARCHAR(8)"),
    ("gu_items", "sold_channel", "VARCHAR(16)"),
    ("gu_items", "sold_at", "DATE"),
    # 部分已出：已出件数。带默认值，老数据的行会补成 0 而不是 NULL
    ("gu_items", "sold_count", "INTEGER NOT NULL DEFAULT 0"),
    # 心愿单相关
    ("gu_items", "want_level", "VARCHAR(8)"),
    ("gu_items", "budget", "FLOAT"),
    ("gu_items", "want_link", "VARCHAR(255)"),
    ("gu_items", "want_note", "VARCHAR(120)"),
    # 再贩提醒相关
    ("reminders", "link", "VARCHAR(255)"),
    # 同城换谷相关
    ("users", "region", "VARCHAR(32)"),
    # 账号与安全相关
    ("users", "last_login_at", "DATETIME"),
    ("users", "session_token", "VARCHAR(64)"),
    # 个人信息：头像
    ("users", "avatar", "VARCHAR(120)"),
    # 个人信息：个性签名
    ("users", "signature", "VARCHAR(60)"),
    ("exchanges", "region", "VARCHAR(32)"),
    ("exchanges", "place", "VARCHAR(60)"),
    ("exchanges", "contact", "VARCHAR(60)"),
    ("exchanges", "note", "VARCHAR(120)"),
    ("exchanges", "created_at", "DATETIME"),
)


# 补列之后顺带跑一次的数据修正：只在「刚补上这一列」的那次执行
_COLUMN_FIXUPS = {
    # sold_count 默认 0，但老库里状态已经是「已出」的行，件数应该等于总数，
    # 否则「状态已出」和「已出件数 0」自相矛盾。
    "sold_count": (
        "UPDATE gu_items SET sold_count = count "
        "WHERE status = 'sold' AND COALESCE(sold_count, 0) = 0",
    ),
}


def _ensure_schema(app):
    """给已存在的库补上后加的表/列。

    db.create_all() 只建「缺失的表」，不会给已有表加「列」，所以老库加了字段
    会报 no such column。正式项目请用 Flask-Migrate/Alembic，这里用最小实现兜住。
    """
    inspector = sa_inspect(db.engine)
    tables = set(inspector.get_table_names())
    for table, column, ddl in _COLUMN_ADDITIONS:
        if table not in tables:
            continue                      # 表本身还没有，create_all() 会带列一起建
        if column in {c["name"] for c in inspector.get_columns(table)}:
            continue
        with db.engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            for fixup in _COLUMN_FIXUPS.get(column, ()):
                conn.execute(text(fixup))
        app.logger.info("已为 %s 补上 %s 列", table, column)


def create_app(config=None):
    app = Flask(__name__)

    secret = os.environ.get("SECRET_KEY")
    if not secret:
        # 兜底只为本地开发方便；上线必须用环境变量注入，否则 session 可被伪造。
        app.logger.warning("SECRET_KEY 未设置，正在使用不安全的开发默认值")
        secret = DEV_SECRET
    app.config["SECRET_KEY"] = secret
    # sqlite:///gu.db 是相对路径，Flask-SQLAlchemy 会落在 instance/ 目录下。
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "DATABASE_URL", "sqlite:///gu.db"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["REMEMBER_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    # 跨站请求不带 cookie（登录页 -> 首页这类站内跳转不受影响），
    # 少一类 CSRF 面。没设 Secure：本地是 http，设了就直接登不上了。
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    # 上传图片：存 static/uploads/，数据库里只记文件名。
    # 可用 GU_UPLOAD_DIR 指到别处（测试就靠它，免得把测试图写进真实上传目录）。
    app.config["UPLOAD_FOLDER"] = (
        os.environ.get("GU_UPLOAD_DIR") or os.path.join(app.static_folder, "uploads"))
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 单次请求上限 5MB
    app.config["IMAGE_MAX_SIDE"] = 1600                # 最长边压缩到这个像素

    if config:
        app.config.update(config)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    login_manager.login_view = "main.login"
    login_manager.login_message = "请先登录"
    login_manager.login_message_category = "error"

    @login_manager.user_loader
    def load_user(uid):
        # session 里的 id 可能是旧 cookie / 被篡改的字符串，不能直接 int()，
        # 否则每个请求都会 500。
        try:
            user = db.session.get(User, int(uid))
        except (TypeError, ValueError):
            return None
        # 会话令牌对不上 = 这个登录态已经作废（改过密码或点过「退出所有设备」）。
        # 库里令牌为空时不校验，兼容这次升级之前就已经存在的登录态。
        if user and user.session_token and session.get("st") != user.session_token:
            return None
        return user

    # 放在函数内部导入，避开 app <-> routes 的循环导入。
    from routes import (DEFAULT_EXCHANGE_ENABLED, LOGIN_LOCK_SECONDS,
                        LOGIN_MAX_FAILS, bp)
    # 同城换谷开关：默认关（见 routes.DEFAULT_EXCHANGE_ENABLED）。
    # 传 EXCHANGE_ENABLED=True 就能单独打开，测试就是这么完整跑换谷那套用例的。
    app.config.setdefault("EXCHANGE_ENABLED", DEFAULT_EXCHANGE_ENABLED)
    # 自动备份数据库（启动时一次 + 每天一次）；测试环境不写备份文件
    app.config.setdefault("AUTO_BACKUP", True)
    # 登录失败限流：连续失败到上限就锁一段时间（测试里会调成 1 秒来验）
    app.config.setdefault("LOGIN_MAX_FAILS", LOGIN_MAX_FAILS)
    app.config.setdefault("LOGIN_LOCK_SECONDS", LOGIN_LOCK_SECONDS)
    # 备份目录：默认 instance/backups，可用 GU_BACKUP_DIR 指到别处
    app.config.setdefault(
        "BACKUP_FOLDER",
        os.environ.get("GU_BACKUP_DIR") or os.path.join(app.instance_path, "backups"))
    # 模板里判「同城」要用：地区能选到区/县，同城得按「省 + 市」算
    import regions
    app.jinja_env.globals["same_city"] = regions.is_same_city
    app.register_blueprint(bp)

    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        """页面放太久导致 token 失效时，别给用户丢一个 400 裸页面。"""
        # AJAX 请求（分类弹窗）要 JSON，前端能就地提示而不是整页跳走
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": False, "message": "页面已过期，请重试"}), 400
        flash("页面已过期，请重新提交", "error")
        return redirect(url_for("main.index")), 302

    @app.errorhandler(RequestEntityTooLarge)
    def handle_too_large(e):
        """超过 MAX_CONTENT_LENGTH 时 Flask 抛 413，同样给个友好提示。"""
        if request.path.startswith("/backup"):
            flash("备份包太大了，请确认是不是选错了文件", "error")
            return redirect(url_for("main.backup_page")), 302
        mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        flash(f"图片太大了，请压缩到 {mb}MB 以内", "error")
        return redirect(url_for("main.index")), 302

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    with app.app_context():
        db.create_all()
        _ensure_schema(app)

    # 启动就备份一份：迁移之后、写业务数据之前。测试环境跳过，免得跑出一堆备份文件。
    if app.config.get("AUTO_BACKUP", True) and not app.config.get("TESTING"):
        from backup import make_db_backup
        make_db_backup(app, reason="start")

    return app


def main():
    app = create_app()
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    # 默认只绑定本机：绑定 0.0.0.0 + debug=True 会把 Werkzeug 调试器暴露给局域网。
    app.run(
        debug=debug,
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
    )


if __name__ == "__main__":
    main()
