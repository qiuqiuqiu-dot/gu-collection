# -*- coding: utf-8 -*-
"""一条命令跑完全部测试。

用法（在仓库根目录）：
    .\\.venv\\Scripts\\python.exe tests\\run_all.py            # 站点内测试：smoke + 渲染 + DOM
    .\\.venv\\Scripts\\python.exe tests\\run_all.py --live     # 再加上线上验证（需要 app.py 正在跑）
    .\\.venv\\Scripts\\python.exe tests\\run_all.py --only smoke
    .\\.venv\\Scripts\\python.exe tests\\run_all.py --list

分三层：
  1. smoke      —— 端到端跑 Flask（认证、谷子、分类、在途、已出、心愿、提醒、换谷、备份、账号安全）
  2. render     —— 用临时库把页面渲染成 HTML，给第 3 层用
  3. dom        —— jsdom 里真跑页面上的 JS（弹窗、四级联动、预览、确认框…）
  --live        —— 对着真在跑的服务器验一遍（只读为主，动过的数据都会还原）
"""
import argparse
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")
BUILD = os.path.join(TESTS, ".build")
PY = sys.executable

SMOKE = ["smoke_test.py"]
RENDER = ["tests/render_exchange_pages.py", "tests/render_app_pages.py"]
DOM = ["tests/test_dom_exchange_board.js", "tests/test_dom_add_sheet.js",
       "tests/test_dom_ui_and_settings.js"]
LIVE = ["tests/live_site_pages.py", "tests/live_backup.py", "tests/live_profile.py",
        "tests/live_restore.py",
        "tests/live_delete.py",
        # 换谷开关关着时它自己会跳过（打印一句说明，退出码 0）
        "tests/live_exchange.py"]


def node_exe():
    """找 node。找不到就跳过 DOM 那层，不当作失败。"""
    return shutil.which("node")


def run(title, argv, cwd=ROOT):
    print(f"\n{'=' * 60}\n▶ {title}\n  $ {' '.join(argv)}\n{'=' * 60}")
    started = time.time()
    result = subprocess.run(argv, cwd=cwd)
    took = time.time() - started
    flag = "✅ 通过" if result.returncode == 0 else "❌ 失败"
    print(f"{flag}  {title}（{took:.1f}s，退出码 {result.returncode}）")
    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(description="跑完谷仓的全部测试")
    parser.add_argument("--live", action="store_true",
                        help="再加上线上验证（需要先跑着 app.py）")
    parser.add_argument("--only", choices=["smoke", "render", "dom", "live"],
                        help="只跑某一层")
    parser.add_argument("--list", action="store_true", help="只列出会跑哪些")
    args = parser.parse_args()

    if args.list:
        for title, items in (("smoke", SMOKE), ("render", RENDER), ("dom", DOM),
                             ("live", LIVE)):
            print(f"{title}:")
            for item in items:
                print("   ", item)
        return 0

    layers = []
    if args.only:
        layers = [args.only]
    else:
        layers = ["smoke", "render", "dom"] + (["live"] if args.live else [])

    env_note = []
    results = []

    if "smoke" in layers:
        results.append(("smoke", run("站点端到端（smoke_test.py）", [PY] + SMOKE)))

    if "render" in layers:
        results.append(("render", run("渲染页面给 DOM 测试用", [PY] + RENDER)))

    if "dom" in layers:
        node = node_exe()
        if not node:
            print("\n⚠ 找不到 node，跳过 DOM 测试（其余照跑）")
            env_note.append("DOM 层被跳过：没装 node")
        else:
            results.append(("dom", run("页面 JS 交互（jsdom）", [node] + DOM)))

    if "live" in layers:
        print("\n线上验证需要有服务器在跑（默认 http://127.0.0.1:5000）")
        for item in LIVE:
            results.append((item, run(f"线上验证：{item}", [PY, item])))

    print(f"\n{'=' * 60}\n汇总\n{'=' * 60}")
    for name, ok in results:
        print(f"  {'✅' if ok else '❌'}  {name}")
    for note in env_note:
        print(f"  ⚠  {note}")
    failed = [name for name, ok in results if not ok]
    if failed:
        print(f"\n❌ 有 {len(failed)} 层没过：{', '.join(failed)}")
        return 1
    print("\n✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
