# 囤谷屋

个人向的**谷子（动漫周边）收藏管理**应用。手机壳版式、服务器端渲染、单文件 SQLite，一台机器就能跑起来。

给同样「吃谷」的人用：把在途的、柜子里的、出掉的、想要的都记在一个地方，顺便把花了多少钱算清楚。

## 能做什么

| 模块 | 说明 |
|---|---|
| **谷柜** | 记录每一件谷子，品类 / 作品IP 两维度分类，可传实物照片（服务端校验真实格式并压到 1600px） |
| **在途** | 预计到货、购买渠道、订单号、超期提示、一键确认到货 |
| **已出** | 成交价 / 出手方式 / 平台 / 日期，自动算盈亏；支持「部分已出」（以件数为准） |
| **心愿单** | 想要的东西单独一栏，按想要程度和预算排序，入手后一键转正 |
| **再贩提醒** | 到点倒计时、优先级标记 |
| **同城换谷** | 谷友互相看到对方想换什么、在哪、怎么联系（**默认关闭**，见下） |
| **数据安全** | 启动时 + 每天自动备份（轮转 14 份），一键导出/导入 zip（含照片和个人资料） |
| **账号与安全** | 改密码、登录失败限流、会话令牌（改密后其它设备立即失效）、注销账号（三道保险） |
| **个人信息** | 头像、用户名、签名、四级地区（国家 → 省 → 市 → 区/县，34 省 / 360 市 / 3156 区县） |
| **谷柜检索** | 按名字/订单号/备注搜索、按品类和作品IP筛选、6 种排序、分页加载 |

界面是 390×844 的手机壳版式，绿色系，服务器端渲染 + 少量原生 JS，没有前端框架、没有构建步骤。

## 跑起来

需要 **Python 3.11+**（本机用 3.13 开发）。

```bash
python -m venv .venv

# Windows
.venv\Scripts\pip install -r requirements.txt
# macOS / Linux
source .venv/bin/activate && pip install -r requirements.txt
```

```bash
# ⚠️ SECRET_KEY 必须设置，否则会用不安全的开发默认值（启动时会有警告）
# Windows PowerShell
$env:SECRET_KEY = "自己生成一串"
# macOS / Linux
export SECRET_KEY="自己生成一串"

.venv/Scripts/python.exe app.py     # Windows
python app.py                       # macOS / Linux
```

打开 <http://127.0.0.1:5000>，注册一个账号就能用。数据库会在 `instance/` 下自动创建，**不需要手动初始化**（新增字段也会在启动时自动补上）。

想先看看长什么样，可以灌一份示例数据：

```bash
python init_db.py
```

> ⚠️ `init_db.py` 会**清空并重建**数据库，只在空库上跑。有真实数据就别碰它。

## 环境变量

见 [`.env.example`](.env.example)。**注意：这个项目不读取 `.env` 文件**，环境变量要通过你的启动方式注入（shell、systemd、任务计划等）。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SECRET_KEY` | 不安全的开发默认值 | 必须设置，否则会话可被伪造 |
| `DATABASE_URL` | `sqlite:///gu.db` | 相对路径落在 `instance/` 下 |
| `HOST` | `127.0.0.1` | 只在局域网访问就改成 `0.0.0.0` |
| `PORT` | `5000` | |
| `FLASK_DEBUG` | `1` | 对外提供服务时设为 `0` |
| `GU_UPLOAD_DIR` | `static/uploads` | 上传的图片 |
| `GU_BACKUP_DIR` | `instance/backups` | 自动备份 |

## 测试

```bash
.venv/Scripts/python.exe tests/run_all.py            # 站点内测试（约 17 秒）
.venv/Scripts/python.exe tests/run_all.py --live     # 再加上对着真服务器的验证
```

分四层：**smoke**（端到端跑 Flask，800+ 项）、**render**（渲染页面）、**dom**（jsdom 真跑页面 JS，需要 `npm install`）、**live**（对着运行中的服务器验证，只读为主）。详见 [`tests/README.md`](tests/README.md)。

## 项目结构

```
app.py            应用工厂、配置、自动建表与补列
models.py         数据模型（User / GuItem / Category / Reminder / Exchange）
routes.py         全部路由与业务逻辑
backup.py         自动备份、导出/导入 zip
regions.py        地区数据（四级联动、同城判据）
init_db.py        可选的示例数据
static/           样式、地区数据（regions.json）
templates/        Jinja 模板
tests/            测试（smoke 在根目录）
tools/            地区数据的抓取与生成脚本
```

## 已知的取舍

- **同城换谷默认关闭**：代码和数据都在，把 `routes.DEFAULT_EXCHANGE_ENABLED` 改成 `True` 即可开启。
- **「用当前位置」是市级近似**：没有接地图/地理编码服务，只能按内置城市坐标挑最近的城市，区/县请自己补。
- **SQLite**：适合个人或小圈子；多人同时写会互相等待，那种场景建议换 PostgreSQL（代码用 SQLAlchemy，改动不大）。
- **只做了手机壳版式**：桌面浏览器里也是中间一条 390px 宽的手机界面。
- **时间存 naive UTC**，显示时按服务器本地时区换算 —— 换机器部署记得设时区。

## License

[MIT](LICENSE)
