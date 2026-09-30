# -*- coding: utf-8 -*-
"""把下载的 pca-code.json 转成 App 用的 static/regions.json。

- 去掉行政区划代码（只留名字，体积减一半）
- 外面套一层「国家」：目前只有中国，另外手补港澳台
- 结构：{国家: {省: {市: [区/县, ...]}}}
"""
import io
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(HERE, "pca-code.json")
DEST = os.path.join(ROOT, "static", "regions.json")

# 数据源里只有大陆 31 个省；港澳台自己补一下（到区/县一级）
EXTRA = {
    "香港特别行政区": {
        "香港岛": ["中西区", "湾仔区", "东区", "南区"],
        "九龙": ["油尖旺区", "深水埗区", "九龙城区", "黄大仙区", "观塘区"],
        "新界": ["葵青区", "荃湾区", "屯门区", "元朗区", "北区", "大埔区",
                "沙田区", "西贡区", "离岛区"],
    },
    "澳门特别行政区": {
        "澳门半岛": ["花地玛堂区", "圣安多尼堂区", "大堂区", "望德堂区", "风顺堂区"],
        "离岛": ["嘉模堂区", "圣方济各堂区", "路氹城"],
    },
    "台湾省": {
        "台北市": ["中正区", "大同区", "中山区", "松山区", "大安区", "万华区",
                 "信义区", "士林区", "北投区", "内湖区", "南港区", "文山区"],
        "新北市": ["板桥区", "三重区", "中和区", "永和区", "新庄区", "新店区",
                 "土城区", "淡水区", "汐止区", "树林区"],
        "桃园市": ["桃园区", "中坜区", "平镇区", "八德区", "杨梅区"],
        "台中市": ["中区", "东区", "南区", "西区", "北区", "西屯区", "南屯区", "北屯区"],
        "台南市": ["中西区", "东区", "南区", "北区", "安平区", "安南区", "永康区"],
        "高雄市": ["盐埕区", "鼓山区", "左营区", "楠梓区", "三民区", "前金区",
                 "新兴区", "苓雅区", "前镇区", "凤山区"],
        "基隆市": ["仁爱区", "信义区", "中正区", "中山区", "安乐区"],
        "新竹市": ["东区", "北区", "香山区"],
        "嘉义市": ["东区", "西区"],
        "宜兰县": ["宜兰市", "罗东镇", "苏澳镇"],
        "花莲县": ["花莲市", "凤林镇", "玉里镇"],
        "台东县": ["台东市", "成功镇", "关山镇"],
        "澎湖县": ["马公市", "湖西乡", "白沙乡"],
    },
}

with io.open(SRC, encoding="utf-8") as f:
    raw = json.load(f)

china = {}
for province in raw:
    cities = {}
    for city in province.get("children", []):
        areas = [a["name"] for a in city.get("children", [])]
        cities[city["name"]] = areas
    china[province["name"]] = cities
china.update(EXTRA)

tree = {"中国": china}
# 再给几个常见国家留个位置：这些国家没有下级数据，
# 用户选了它们就用页面上的「其他（手动填写）」自己填。
for other in ("日本", "韩国", "美国", "英国", "加拿大", "澳大利亚"):
    tree.setdefault(other, {})

with io.open(DEST, "w", encoding="utf-8") as f:
    json.dump(tree, f, ensure_ascii=False, separators=(",", ":"))

size = os.path.getsize(DEST)
provinces = len(china)
cities = sum(len(v) for v in china.values())
areas = sum(len(a) for v in china.values() for a in v.values())
print(f"{DEST}\n  大小 {size / 1024:.0f} KB")
print(f"  国家 {len(tree)} · 省 {provinces} · 市 {cities} · 区/县 {areas}")
print("  抽查 广东省→深圳市：", china["广东省"]["深圳市"][:6])
print("  抽查 北京市→市辖区：", china["北京市"]["市辖区"][:5])
