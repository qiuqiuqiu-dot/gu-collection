<div align="center">

# 囤谷屋

**谷子（动漫周边）收藏管理应用**

在途的、柜子里的、出掉的、想要的，都记在一个地方，顺便把花了多少钱算清楚。

![Python](https://img.shields.io/badge/Python-3.11%2B-blue)
![Flask](https://img.shields.io/badge/Flask-3.x-black)
![SQLite](https://img.shields.io/badge/SQLite-%E5%8D%95%E6%96%87%E4%BB%B6-003B57)
![License](https://img.shields.io/badge/License-MIT-green)

手机壳版式 · 服务器端渲染 · 单文件数据库 · 纯本地运行，不联网

</div>

---

<!-- 有截图之后，把下面这段的注释去掉，并把图片放进 docs/ 目录（见 docs/screenshots.md）
<div align="center">
  <img src="docs/home.png" width="240" alt="首页">
  <img src="docs/guiku.png" width="240" alt="谷柜">
  <img src="docs/transit.png" width="240" alt="在途">
</div>
-->

## 能做什么

| 模块 | 说明 |
|---|---|
| **谷柜** | 记录每一件谷子，品类 / 作品IP 两维度分类，可传实物照片（服务端校验真实格式，压到最长边 1600px） |
| **谷柜检索** | 按名字 / 订单号 / 备注搜索，按品类和作品IP 筛选，6 种排序，分页加载 |
| **在途** | 预计到货、购买渠道、订单号、超期提示、一键确认到货 |
| **已出** | 成交价 / 出手方式 / 平台 / 日期，自动算盈亏；支持「部分已出」（以件数为准） |
| **心愿单** | 想要的东西单独一栏，按想要程度和预算排序，入手后一键转正 |
| **再贩提醒** | 到点倒计时、优先级标记 |
| **同城换谷** | 谷友互相看到对方想换什么、在哪、怎么联系（**默认关闭**，见下方 FAQ） |
| **数据安全** | 启动时 + 每天自动备份（轮转 14 份），一键导出 / 导入 zip（含照片和个人资料） |
| **账号与安全** | 改密码、登录失败限流、会话令牌（改密后其它设备立即失效）、注销账号（三道保险） |
| **个人信息** | 头像、用户名、签名、四级地区（国家 → 省 → 市 → 区/县，34 省 / 360 市 / 3156 区县） |

## 跑起来

需要 **Python 3.11+**（开发环境用 3.13）。

```bash
git clone https://github.com/<你的用户名>/<仓库名>.git
cd <仓库名>
python -m venv .venv
```

```bash
# Windows
.venv\Scripts\pip install -r requirements.txt
# macOS / Linux
source .venv/bin/activate && pip install -r requirements.txt
```

```bash
# ⚠️ SECRET_KEY 必须设置，否则会用不安全的开发默认值（启动时会打警告）
# Windows PowerShell
$env:SECRET_KEY = "自己生成一串"
# macOS / Linux
export SECRET_KEY="自己生成一串"

# 生成一串随机值：python -c "import secrets; print(secrets.token_hex(32))"

.venv/Scripts/python.exe app.py     # Windows
python app.py                       # macOS / Linux
```

打开 <http://127.0.0.1:5000>，注册一个账号就能用。

数据库会在 `instance/` 下**自动创建**，不需要手动初始化；以后版本新增字段也会在启动时自动补上。

想先看看长什么样，可以灌一份示例数据：

```bash
python init_db.py
```

> ⚠️ `init_db.py` 会**清空并重建**数据库，只能在空库上跑。已经有真实数据就别碰它。

## 环境变量

完整的说明和示例见 [`.env.example`](.env.example)。

> ⚠️ **本项目不读取 `.env` 文件**（没有装 python-dotenv）。把示例文件改名成 `.env` 是不会生效的，请用你启动进程的方式注入环境变量。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SECRET_KEY` | 不安全的开发默认值 | 必须设置，泄露等于任何人都能伪造登录态 |
| `DATABASE_URL` | `sqlite:///gu.db` | 相对路径落在 `instance/` 下；绝对路径要写四个斜杠 |
| `HOST` | `127.0.0.1` | 想让手机在同一个 WiFi 下访问就改成 `0.0.0.0` |
| `PORT` | `5000` | |
| `FLASK_DEBUG` | `1` | 对外提供服务时**必须**设成 `0` |
| `GU_UPLOAD_DIR` | `static/uploads` | 上传的图片存放位置 |
| `GU_BACKUP_DIR` | `instance/backups` | 自动备份存放位置 |

## 测试

```bash
.venv/Scripts/python.exe tests/run_all.py            # 站点内测试（约 17 秒）
.venv/Scripts/python.exe tests/run_all.py --live     # 再加上对着真服务器的验证
.venv/Scripts/python.exe tests/run_all.py --list     # 看会跑哪些
```

分四层：

| 层 | 内容 |
|---|---|
| **smoke** | 端到端跑 Flask，覆盖全部功能与越权 / CSRF / 坏输入（800+ 项断言） |
| **render** | 用临时库把页面渲染成 HTML，给下一层用 |
| **dom** | jsdom 里真跑页面上的 JS：弹窗、四级联动、图片预览、危险操作确认框（需 `npm install`） |
| **live** | 对着正在运行的服务器验证（只读为主，改动都会还原） |

详见 [`tests/README.md`](tests/README.md)。测试产物统一写在 `tests/.build/`，**不碰真实数据**。

## 技术栈

- **后端**：Flask 3 + Flask-SQLAlchemy + Flask-Login + Flask-WTF（CSRF）
- **数据库**：SQLite（单文件，零配置）
- **图片**：Pillow（校验真实格式 + 压缩尺寸，白名单格式，UUID 命名）
- **前端**：Jinja 模板 + 原生 JS + 一份手写 CSS。**没有前端框架、没有构建步骤、没有 npm 依赖**
- **测试**：自带四层测试，jsdom 用于 DOM 层

## 项目结构

```
app.py            应用工厂、配置、自动建表与补列（老库升级用）
models.py         数据模型：User / GuItem / Category / Reminder / Exchange
routes.py         全部路由与业务逻辑
backup.py         自动备份、导出 / 导入 zip
regions.py        地区数据：四级联动、同城判据
init_db.py        可选的示例数据
static/           样式 + 地区数据（regions.json，48 KB）
templates/        Jinja 模板
tests/            测试与工具脚本（smoke 在仓库根目录）
tools/            地区数据的抓取与生成脚本
```


## 常见问题

**能不能多个人一起用？**
可以，注册多个账号即可，各自的数据互相隔离（越权访问一律 404）。但 SQLite 适合个人或小圈子，很多人同时写会互相等待；那种场景建议换 PostgreSQL（代码用 SQLAlchemy，主要改 `DATABASE_URL`）。

**同城换谷怎么打开？**
`routes.py` 里把 `DEFAULT_EXCHANGE_ENABLED` 改成 `True` 就好，代码和数据都还在。默认关闭是因为它是唯一会把你的地区展示给别人的功能。

**忘记密码怎么办？**
没有邮箱找回，找不回来。密码只存散列值，谁都反推不出明文。能救你的只有数据库文件和备份 —— 所以**请定期在「数据备份」页导出 zip**。

**手机上能看吗？**
能。把 `HOST` 设成 `0.0.0.0` 并放行防火墙，手机连同一个 WiFi 访问 `http://电脑的IP:5000` 就行。

**界面为什么只占了屏幕中间一条？**
它按 390×844 的手机壳版式设计，桌面浏览器里也是这个样子。

## 已知的取舍

- **「用当前位置」是市级近似**：没有接地图 / 地理编码服务，只能按内置的城市坐标挑最近的城市，区/县请自己补。
- **时间存的是 naive UTC**，显示时按服务器本地时区换算 —— 换机器部署记得设时区，否则「今天到货」会差几个小时。
- **图片单张上限 5 MB**，超了会被拒绝（会压缩到最长边 1600px）。
- **登录失败限流存在进程内存里**，重启会清零，也不跨进程共享。个人自用足够。

## 路线图

- [ ] 批量操作：多选后一次改分类 / 标已出 / 删除
- [ ] 一件谷子支持多张照片（本体 / 瑕疵 / 包装）
- [ ] 提醒导出 `.ics`，一键加进手机日历
- [ ] 统计报表：月度盈亏、进出趋势图
- [ ] 部署文档（局域网 / 内网穿透 / 云服务器三种走法）

## 参与 / 反馈

欢迎提 Issue 说 bug 或者想要的功能。想改代码的话：

1. 先跑一遍 `tests/run_all.py` 确认基线是绿的
2. 改完再跑一次，**测试全绿**再提交
3. 新增功能请顺带补测试：这个项目的测试是分层写的（后端逻辑进 `smoke_test.py`，页面 JS 进 `tests/test_dom_*.js`）

代码风格：中文注释，解释「为什么这么做」，而不是复述代码在做什么。

## License

[MIT](LICENSE) —— 随便用，改了也不用告诉我。
