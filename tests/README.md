# 测试说明

一条命令跑完全部测试（在仓库根目录）：

```powershell
.\.venv\Scripts\python.exe tests\run_all.py           # 站点内测试
.\.venv\Scripts\python.exe tests\run_all.py --live    # 再加上线上验证
.\.venv\Scripts\python.exe tests\run_all.py --only smoke
.\.venv\Scripts\python.exe tests\run_all.py --list
```

## 四层测试

| 层 | 文件 | 干什么 |
|---|---|---|
| smoke | `../smoke_test.py` | 端到端跑 Flask：认证、谷子、分类、在途、已出、心愿、提醒、换谷、备份、账号安全 |
| render | `render_exchange_pages.py`、`render_app_pages.py` | 用临时库把页面渲染成 HTML，给 DOM 层用 |
| dom | `test_dom_*.js` | jsdom 里真跑页面上的 JS：弹窗、四级联动、图片预览、危险操作确认框 |
| live | `live_*.py` | 对着**真在跑**的服务器验一遍（`app.py` 得先跑起来） |

## 一些约定

- **不碰真实数据**：测试库、测试上传、渲染出的页面都写在 `tests/.build/`；
  线上脚本只读，个别会临时改数据的用例自己会还原并核对。
- `build_regions.py` 只做数据维护（重新生成 `static/regions.json`），不是测试；
  它需要先用 `fetch_pca_code.py` 取一份 `pca-code.json` 放在 `tools/` 下。
- DOM 层需要 jsdom：在 `tests/` 下跑一次 `npm install` 即可（已装过就不用管）。

## 换谷功能

`live_exchange.py` 只在**同城换谷开关打开**时才有意义
（`routes.DEFAULT_EXCHANGE_ENABLED = True`），默认关闭时它会整段跳过。
功能本身的用例在 `smoke_test.py` 第 19 节，那边是开着开关跑的。
