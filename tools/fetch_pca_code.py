# -*- coding: utf-8 -*-
"""试着下取一份公开的省/市/区数据，存到 .tmp/ 供检查。"""
import io
import json
import os
import ssl
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
URLS = [
    "https://cdn.jsdelivr.net/npm/china-division@2.7.0/dist/pca-code.json",
    "https://unpkg.com/china-division@2.7.0/dist/pca-code.json",
    "https://raw.githubusercontent.com/modood/Administrative-divisions-of-China/master/dist/pca-code.json",
]

ctx = ssl.create_default_context()
for url in URLS:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "gu-app/1.0"})
        with urllib.request.urlopen(req, timeout=40, context=ctx) as resp:
            blob = resp.read()
        print(f"OK   {url}   {len(blob)} 字节")
        data = json.loads(blob.decode("utf-8"))
        print("     顶层条数:", len(data), "| 第一条:", str(data[0])[:120])
        with io.open(os.path.join(HERE, "pca-code.json"), "wb") as f:
            f.write(blob)
        sys.exit(0)
    except Exception as exc:                       # noqa: BLE001
        print(f"FAIL {url} -> {type(exc).__name__}: {exc}")
sys.exit(1)
