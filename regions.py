"""地区数据：国家 → 省 → 市 → 区/县 四级。

数据本体在 static/regions.json，来源：npm 包 china-division 的 dist/pca-code.json
（民政部行政区划数据整理，34 省 / 360 市 / 3156 区县），去掉行政区划代码后压缩成 48 KB，
港澳台三地是另外手工补的。要更新数据，重新取一份 pca-code.json 跑一遍转换即可，
程序本身不联网，只读这个本地文件。
前端点开「地区」页时按需拉取，服务端保存时也用它校验一遍，保证存进去的都是真实地名。

存进库里的写法是「短名」拼接，例如：
    中国     → 「广东 深圳 南山」
    其他国家 → 「日本 东京 涩谷」
短名 = 去掉「省/市/区/县/自治区/自治州…」这些后缀，
为了兼容以前手工填的「广东 深圳」，也让匹配同城时不会因为后缀不同而错判。
"""

import json
import os
from functools import lru_cache

MANUAL = "其他（手动填写）"
MANUAL_VALUE = "__manual__"
OTHER_COUNTRY = "其他"

#: 城市坐标：只用于「用当前位置」挑最近的市（市级近似，不是精确地址）
CITY_COORDS = [
    ("北京", "北京", 39.90, 116.41), ("上海", "上海", 31.23, 121.47),
    ("天津", "天津", 39.13, 117.20), ("重庆", "重庆", 29.56, 106.55),
    ("广东", "广州", 23.13, 113.26), ("广东", "深圳", 22.54, 114.06),
    ("广东", "珠海", 22.27, 113.58), ("广东", "佛山", 23.02, 113.12),
    ("广东", "东莞", 23.02, 113.75), ("广东", "中山", 22.52, 113.39),
    ("广东", "惠州", 23.11, 114.42), ("广东", "汕头", 23.35, 116.68),
    ("广东", "湛江", 21.27, 110.36), ("广东", "江门", 22.58, 113.08),
    ("广东", "肇庆", 23.05, 112.47), ("广东", "茂名", 21.66, 110.93),
    ("浙江", "杭州", 30.27, 120.15), ("浙江", "宁波", 29.87, 121.55),
    ("浙江", "温州", 28.00, 120.67), ("浙江", "嘉兴", 30.75, 120.76),
    ("浙江", "绍兴", 30.00, 120.58), ("浙江", "金华", 29.08, 119.65),
    ("浙江", "台州", 28.66, 121.42), ("浙江", "湖州", 30.89, 120.09),
    ("浙江", "义乌", 29.31, 120.07),
    ("江苏", "南京", 32.06, 118.80), ("江苏", "苏州", 31.30, 120.58),
    ("江苏", "无锡", 31.49, 120.31), ("江苏", "常州", 31.81, 119.97),
    ("江苏", "南通", 31.98, 120.89), ("江苏", "徐州", 34.26, 117.19),
    ("江苏", "扬州", 32.39, 119.41), ("江苏", "盐城", 33.35, 120.16),
    ("江苏", "昆山", 31.39, 120.98),
    ("山东", "济南", 36.65, 117.12), ("山东", "青岛", 36.07, 120.38),
    ("山东", "烟台", 37.46, 121.45), ("山东", "潍坊", 36.71, 119.16),
    ("山东", "临沂", 35.10, 118.36), ("山东", "淄博", 36.81, 118.06),
    ("山东", "济宁", 35.41, 116.59), ("山东", "威海", 37.51, 122.12),
    ("福建", "福州", 26.07, 119.30), ("福建", "厦门", 24.48, 118.09),
    ("福建", "泉州", 24.87, 118.68), ("福建", "漳州", 24.51, 117.65),
    ("福建", "莆田", 25.45, 119.01), ("福建", "龙岩", 25.08, 117.02),
    ("四川", "成都", 30.57, 104.07), ("四川", "绵阳", 31.47, 104.68),
    ("四川", "德阳", 31.13, 104.40), ("四川", "南充", 30.84, 106.11),
    ("四川", "宜宾", 28.77, 104.62), ("四川", "泸州", 28.87, 105.44),
    ("湖北", "武汉", 30.59, 114.31), ("湖北", "宜昌", 30.69, 111.29),
    ("湖北", "襄阳", 32.01, 112.12), ("湖北", "荆州", 30.33, 112.24),
    ("湖北", "黄石", 30.20, 115.04),
    ("湖南", "长沙", 28.23, 112.94), ("湖南", "株洲", 27.83, 113.13),
    ("湖南", "湘潭", 27.83, 112.94), ("湖南", "衡阳", 26.89, 112.57),
    ("湖南", "岳阳", 29.36, 113.13), ("湖南", "常德", 29.03, 111.69),
    ("河南", "郑州", 34.75, 113.63), ("河南", "洛阳", 34.62, 112.45),
    ("河南", "开封", 34.80, 114.31), ("河南", "新乡", 35.30, 113.93),
    ("河南", "南阳", 32.99, 112.53), ("河南", "许昌", 34.04, 113.85),
    ("河北", "石家庄", 38.04, 114.51), ("河北", "唐山", 39.63, 118.18),
    ("河北", "保定", 38.87, 115.46), ("河北", "廊坊", 39.52, 116.70),
    ("河北", "秦皇岛", 39.94, 119.60), ("河北", "邯郸", 36.63, 114.54),
    ("陕西", "西安", 34.34, 108.94), ("陕西", "咸阳", 34.33, 108.71),
    ("陕西", "宝鸡", 34.36, 107.24), ("陕西", "渭南", 34.50, 109.51),
    ("陕西", "榆林", 38.29, 109.73),
    ("安徽", "合肥", 31.82, 117.23), ("安徽", "芜湖", 31.35, 118.43),
    ("安徽", "蚌埠", 32.92, 117.39), ("安徽", "安庆", 30.51, 117.05),
    ("安徽", "黄山", 29.71, 118.34),
    ("江西", "南昌", 28.68, 115.86), ("江西", "赣州", 25.83, 114.93),
    ("江西", "九江", 29.71, 116.00), ("江西", "上饶", 28.45, 117.94),
    ("江西", "宜春", 27.81, 114.42),
    ("辽宁", "沈阳", 41.80, 123.43), ("辽宁", "大连", 38.91, 121.61),
    ("辽宁", "鞍山", 41.11, 122.99), ("辽宁", "抚顺", 41.88, 123.96),
    ("辽宁", "锦州", 41.10, 121.13),
    ("吉林", "长春", 43.82, 125.32), ("吉林", "吉林", 43.84, 126.55),
    ("吉林", "延吉", 42.90, 129.51), ("吉林", "四平", 43.17, 124.35),
    ("黑龙江", "哈尔滨", 45.80, 126.53), ("黑龙江", "大庆", 46.59, 125.10),
    ("黑龙江", "齐齐哈尔", 47.35, 123.92), ("黑龙江", "牡丹江", 44.55, 129.63),
    ("山西", "太原", 37.87, 112.55), ("山西", "大同", 40.08, 113.30),
    ("山西", "临汾", 36.09, 111.52), ("山西", "运城", 35.03, 111.00),
    ("云南", "昆明", 25.04, 102.72), ("云南", "大理", 25.61, 100.27),
    ("云南", "丽江", 26.86, 100.23), ("云南", "曲靖", 25.49, 103.80),
    ("云南", "西双版纳", 22.01, 100.80),
    ("贵州", "贵阳", 26.65, 106.63), ("贵州", "遵义", 27.73, 106.93),
    ("贵州", "六盘水", 26.59, 104.83),
    ("广西", "南宁", 22.82, 108.32), ("广西", "桂林", 25.27, 110.29),
    ("广西", "柳州", 24.31, 109.43), ("广西", "北海", 21.48, 109.12),
    ("海南", "海口", 20.04, 110.32), ("海南", "三亚", 18.25, 109.51),
    ("海南", "儋州", 19.52, 109.58),
    ("甘肃", "兰州", 36.06, 103.83), ("甘肃", "天水", 34.58, 105.72),
    ("甘肃", "酒泉", 39.73, 98.49),
    ("宁夏", "银川", 38.49, 106.23), ("宁夏", "石嘴山", 38.98, 106.38),
    ("青海", "西宁", 36.62, 101.78), ("青海", "海东", 36.50, 102.10),
    ("新疆", "乌鲁木齐", 43.83, 87.62), ("新疆", "喀什", 39.47, 75.99),
    ("新疆", "伊宁", 43.91, 81.32), ("新疆", "克拉玛依", 45.58, 84.89),
    ("西藏", "拉萨", 29.65, 91.14), ("西藏", "日喀则", 29.27, 88.88),
    ("内蒙古", "呼和浩特", 40.84, 111.75), ("内蒙古", "包头", 40.66, 109.84),
    ("内蒙古", "鄂尔多斯", 39.61, 109.78), ("内蒙古", "赤峰", 42.26, 118.89),
    ("香港特别行政区", "香港岛", 22.28, 114.15),
    ("澳门特别行政区", "澳门半岛", 22.20, 113.55),
    ("台湾省", "台北市", 25.03, 121.57),
]

#: 去掉这些后缀后就是「短名」。顺序有讲究：长的先匹配
_SUFFIXES = ("维吾尔自治区", "壮族自治区", "回族自治区", "特别行政区", "自治区",
             "自治州", "自治县", "地区", "林区", "省", "市", "区", "县", "盟", "旗")
#: 这些「市」其实不是市，是层级占位，存值时要跳过
_PLACEHOLDERS = ("市辖区", "县", "省直辖县级行政区划", "自治区直辖县级行政区划")


def short(name):
    """「广东省」→「广东」；占位层级（市辖区）返回空串。"""
    if not name:
        return ""
    if name in _PLACEHOLDERS:
        return ""
    for suffix in _SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name


@lru_cache(maxsize=1)
def tree():
    """{国家: {省: {市: [区, ...]}}}，进程内只读一次。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "static", "regions.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def countries():
    return list(tree().keys())


def provinces(country):
    return list(tree().get(country, {}).keys())


def cities(country, province):
    return list(tree().get(country, {}).get(province, {}).keys())


def areas(country, province, city):
    return list(tree().get(country, {}).get(province, {}).get(city, []))


def is_valid(country, province, city, area):
    """校验一条路径真的在数据里（省/市可空，区/县可空）。"""
    if not country or country not in tree():
        return False
    if not province:
        return True
    if province not in tree()[country]:
        return False
    if not city:
        return True
    if city not in tree()[country][province]:
        return False
    if not area:
        return True
    return area in tree()[country][province][city]


def build_value(country, province, city, area):
    """拼出存库的字符串：中国省略国名，其余层级去掉后缀、跳过占位层级。"""
    parts = []
    if country and country != "中国":
        parts.append(short(country) or country)
    for name in (province, city, area):
        piece = short(name)
        if piece and (not parts or parts[-1] != piece):
            parts.append(piece)
    return " ".join(parts)[:64]


def resolve(text):
    """把库里已有的写法解析回四级，用于打开页面时回填下拉。

    兼容老数据：以前手工填的「广东 深圳」也能认出省和市。
    """
    empty = {"country": "中国", "province": "", "city": "", "area": ""}
    if not text:
        return empty
    parts = [p for p in str(text).split() if p]
    if not parts:
        return empty
    # 先看第一个词是不是国家
    country = "中国"
    if parts[0] in tree():
        country = parts[0]
        parts = parts[1:]
    elif parts[0] in {short(c) for c in tree()}:
        # 「中国」这些短名也认
        country = next(c for c in tree() if short(c) == parts[0])
        parts = parts[1:]
    found = dict(empty, country=country)
    wanted = [p for p in parts]
    # 逐级用短名去对：省 → 市 → 区
    for province in provinces(country):
        if wanted and short(province) == wanted[0]:
            found["province"] = province
            for city in cities(country, province):
                if len(wanted) > 1 and short(city) == wanted[1]:
                    found["city"] = city
                    for area in areas(country, province, city):
                        if len(wanted) > 2 and short(area) == wanted[2]:
                            found["area"] = area
                    break
            break
    # 只填了「省 市」两级、但市名其实落在区一级（比如义乌、昆山）
    if found["province"] and not found["city"]:
        for city in cities(country, found["province"]):
            for area in areas(country, found["province"], city):
                if len(wanted) > 1 and short(area) == wanted[1]:
                    found["city"] = city
                    found["area"] = area
                    return found
    # 省级都没对上：可能只写了市，或整个是自定义写法
    if not found["province"]:
        for province in provinces(country):
            for city in cities(country, province):
                if len(wanted) >= 1 and short(city) == wanted[0]:
                    found["province"] = province
                    found["city"] = city
                    if len(wanted) > 1:
                        for area in areas(country, province, city):
                            if short(area) == wanted[1]:
                                found["area"] = area
                                break
                    return found
    return found


def display(text):
    """把库里的写法显示成标准短名形式（「广东省 深圳市」→「广东 深圳」）；
    认不出来（用户自己写的）就原样返回。"""
    if not text:
        return ""
    resolved = resolve(text)
    if resolved["province"] and is_valid(resolved["country"], resolved["province"],
                                        resolved["city"], resolved["area"]):
        return build_value(resolved["country"], resolved["province"],
                           resolved["city"], resolved["area"])
    return str(text).strip()


#: 直辖市没有单独的「市」层级（值是「上海 徐汇」这种两级写法），判同城时要区别对待
MUNICIPALITIES = {"北京", "上海", "天津", "重庆"}


def _parts(text):
    return [p for p in str(text or "").split() if p]


def city_key(text):
    """同城判据：只取「省 + 市」两级。

    加了区/县之后，同城绝不能被区拆开——「广东 深圳 南山」和「广东 深圳 福田」
    必须是同城。直辖市只有「市 + 区」两级，所以取第一级。
    """
    parts = _parts(text)
    if not parts:
        return ""
    if parts[0] in MUNICIPALITIES:
        return parts[0]
    return " ".join(parts[:2])


def district_key(text):
    """同区判据（用来在同城内部再排个序）：直辖市取第二级，其余取第三级。"""
    parts = _parts(text)
    if not parts:
        return ""
    if parts[0] in MUNICIPALITIES:
        return parts[1] if len(parts) > 1 else ""
    return parts[2] if len(parts) > 2 else ""


def is_same_city(a, b):
    """两个地区算不算同一个市。空值不算同城。"""
    key_a = city_key(a)
    return bool(key_a) and key_a == city_key(b)


def _distance(lat1, lon1, lat2, lon2):
    """球面距离（km），够用就行——只拿它挑最近的市。"""
    from math import asin, cos, radians, sin, sqrt
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = (sin(dlat / 2) ** 2
         + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2)
    return 2 * 6371 * asin(sqrt(a))


def _lookup_path(province_short, city_short):
    """按短名在数据里找一条真实路径，找不到返回 None。

    坐标表里像「义乌」「昆山」这种县级市，在数据里其实是区一级，
    所以市级对不上时再往区一级找一次。
    """
    for province in provinces("中国"):
        if short(province) != province_short:
            continue
        for city in cities("中国", province):
            if short(city) == city_short:
                return {"country": "中国", "province": province,
                        "city": city, "area": ""}
        for city in cities("中国", province):
            for area in areas("中国", province, city):
                if short(area) == city_short:
                    return {"country": "中国", "province": province,
                            "city": city, "area": area}
    return None


def nearest(lat, lon):
    """按坐标挑最近的市，返回 (路径 dict, 距离公里)。

    这是**市级近似**：没有地图服务可以反查精确地址，
    只能给出「离你最近的那个市」，区/县留空由用户自己补。
    """
    best, best_km = None, None
    for province_short, city_short, clat, clon in CITY_COORDS:
        km = _distance(lat, lon, clat, clon)
        if best_km is None or km < best_km:
            best, best_km = (province_short, city_short), km
    path = _lookup_path(*best) or {"country": "中国", "province": best[0],
                                   "city": best[1], "area": ""}
    return path, (round(best_km) if best_km is not None else None)
