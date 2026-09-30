/* jsdom：分类管理入口要好找（是显眼按钮，不是灰色小字）+ 点得开
   顺便验一下 CSS 层叠真的生效——.sec-head a 那条灰色链接规则曾经会把它盖掉。 */
const fs = require("fs");
const path = require("path");
const { JSDOM, VirtualConsole } = require(require("./jsdom_path"));

const css = fs.readFileSync(path.join(__dirname, "..", "static", "style.css"), "utf8");
const all = [];
function check(label, ok, detail) {
  all.push({ label, ok: !!ok, detail: ok ? "" : String(detail) });
}

function load(file, url, opts) {
  const pageErrors = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("jsdomError", (e) => pageErrors.push("jsdomError: " + e.message));
  virtualConsole.on("error", (...a) => pageErrors.push("console.error: " + a.join(" ")));
  let html = fs.readFileSync(path.join(__dirname, ".build", file), "utf8").replace(
    /<link[^>]+style\.css[^>]*>/, `<style>${css}</style>`);
  // 地区页会 fetch 静态的 regions.json：这里塞一份小小的假数据进去，
  // 好在 jsdom 里把四级联动那段逻辑真跑一遍
  const injected = [];
  if (opts && opts.tree) {
    injected.push(`window.fetch = function () {
      return Promise.resolve({ json: function () {
        return Promise.resolve(${JSON.stringify(opts.tree)}); } });
    };`);
  }
  // jsdom 没实现 form.submit()，会抛 "Not implemented" 并污染「无 JS 报错」这条；
  // 换成计数桩，这样还能顺带验证「改下拉就提交」那段逻辑真的挂上了
  if (opts && opts.stubSubmit) {
    injected.push("window.__submits = 0;\n"
      + "HTMLFormElement.prototype.submit = function () { window.__submits += 1; };");
  }
  if (injected.length) {
    html = html.replace("<head>", "<head><script>" + injected.join("\n") + "<\/script>");
  }
  const dom = new JSDOM(html, {
    runScripts: "dangerously", url, pretendToBeVisual: true, virtualConsole,
  });
  dom.window.addEventListener("error", (e) => pageErrors.push("window.onerror: " + e.message));
  return { window: dom.window, pageErrors };
}

const pending = [];
const flush = () => new Promise((r) => setTimeout(r, 0));

/* ---------- 谷柜页：搜索 / 筛选 / 排序 ---------- */
{
  const env = load("s_items.html", "http://127.0.0.1:5000/items",
    { stubSubmit: true });
  const doc = env.window.document;
  const win = env.window;
  const form = doc.querySelector("#filterForm");
  check("谷柜页有搜索框和搜索按钮",
    !!doc.querySelector("#searchInput") && !!doc.querySelector("#searchBtn"));
  check("搜索框提示说明了能搜什么",
    (doc.querySelector("#searchInput").getAttribute("placeholder") || "")
      .indexOf("订单号") >= 0,
    doc.querySelector("#searchInput").getAttribute("placeholder"));
  check("搜索框是 GET 表单（条件在地址栏里，可收藏可分享）",
    form && form.getAttribute("method").toLowerCase() === "get"
    && form.getAttribute("action") === "/items", form && form.getAttribute("method"));
  check("三个下拉：品类 / 作品IP / 排序",
    !!doc.querySelector("#filterType") && !!doc.querySelector("#filterIp")
    && !!doc.querySelector("#sortSelect"));
  check("排序下拉列出了所有排序方式",
    doc.querySelectorAll("#sortSelect option").length === 6,
    doc.querySelectorAll("#sortSelect option").length);
  check("品类下拉第一项是「全部品类」（默认不筛）",
    doc.querySelector("#filterType option").value === ""
    && doc.querySelector("#filterType option").textContent.trim() === "全部品类");
  check("切换分组时带着当前条件（隐藏字段保留 group）",
    !!form.querySelector('input[name="group"]'));
  check("三个下拉都挂上了「改动即提交」",
    ["#filterType", "#filterIp", "#sortSelect"]
      .every((s) => doc.querySelector(s).hasAttribute("data-autosubmit")));

  // 真的改一下下拉：应该触发一次提交
  const before = win.__submits;
  const sortSel = doc.querySelector("#sortSelect");
  sortSel.value = sortSel.options[1].value;
  sortSel.dispatchEvent(new win.Event("change"));
  check("改排序下拉会立刻提交表单", win.__submits === before + 1,
    `${before} -> ${win.__submits}`);
  const typeSel = doc.querySelector("#filterType");
  typeSel.dispatchEvent(new win.Event("change"));
  check("改品类下拉也会提交", win.__submits === before + 2, win.__submits);
  check("谷柜页（带筛选栏）无 JS 报错",
    env.pageErrors.length === 0, env.pageErrors.join(" | "));
}

/* ---------- 谷柜页（切换条里） ---------- */
{
  const env = load("s_items.html", "http://127.0.0.1:5000/items");
  const doc = env.window.document;
  const el = doc.querySelector(".cat-manage");
  check("谷柜页有分类管理入口", !!el);
  check("入口在分组切换条里", el && !!el.closest(".seg-row"));
  check("入口是能点的链接（打开分类管理弹窗）",
    el.tagName === "A" && el.dataset.modalOpen === "catModal");
  check("入口带图标", !!el.querySelector("svg"));
  check("入口文字就是「分类管理」",
    el.querySelector("span").textContent.trim() === "分类管理",
    el.querySelector("span").textContent.trim());

  // 分类数量角标：渲染用账号建了 2 个品类 + 3 个 IP
  check("入口带分类数量角标，且数对（2 品类 + 3 IP = 5）",
    el.querySelector("b") && el.querySelector("b").textContent.trim() === "5",
    el.querySelector("b") && el.querySelector("b").textContent.trim());

  // 外观：以前是灰色小字，现在得是绿色胶囊按钮
  const cs = env.window.getComputedStyle(el);
  check("外观是胶囊按钮（inline-flex + 圆角）",
    cs.display === "inline-flex" && cs.borderRadius === "999px",
    `${cs.display} / ${cs.borderRadius}`);
  check("文字是加粗的", cs.fontWeight === "650", cs.fontWeight);
  check("是绿色系（不是以前那种灰色小字）",
    cs.color === "var(--green-deep)", cs.color);
  check("有底色（浅绿）", cs.backgroundColor === "var(--green-soft)", cs.backgroundColor);
  check("有边框", parseFloat(cs.borderTopWidth) > 0, cs.borderTopWidth);

  // 点得开
  el.click();
  check("点它能打开分类管理弹窗", doc.getElementById("catModal").classList.contains("show"));
  check("谷柜页无 JS 报错", env.pageErrors.length === 0, env.pageErrors.join(" | "));
}

/* ---------- 首页：按需求不再放入口，走 + 面板 ---------- */
{
  const env = load("s_dash.html", "http://127.0.0.1:5000/");
  const doc = env.window.document;
  check("首页谷柜标题旁边已经没有分类管理按钮了",
    !doc.querySelector(".sec-head .cat-manage"));
  check("首页依然能从 + 号面板进分类管理",
    (function () {
      doc.querySelector(".fab").click();
      const item = doc.querySelector('#addSheet [data-modal-open="catModal"]');
      if (!item) { return false; }
      item.click();
      return doc.getElementById("catModal").classList.contains("show")
        && !doc.getElementById("addSheet").classList.contains("show");
    })());
  check("首页无 JS 报错", env.pageErrors.length === 0, env.pageErrors.join(" | "));
}

/* ---------- 添加表单里那行提示 ---------- */
{
  const env = load("s_items.html", "http://127.0.0.1:5000/items");
  const doc = env.window.document;
  const link = doc.querySelector("#addModal .cat-link");
  check("添加表单里也有分类管理入口", !!link);
  check("它也是绿色胶囊（不是裸链接）",
    env.window.getComputedStyle(link).backgroundColor === "var(--green-soft)"
    && env.window.getComputedStyle(link).borderRadius === "999px",
    env.window.getComputedStyle(link).backgroundColor);
  link.click();
  check("从添加表单点它也能开分类管理",
    doc.getElementById("catModal").classList.contains("show"));
}

/* ---------- 旧的灰色小字入口应该清理干净 ---------- */
{
  const itemsHtml = fs.readFileSync(path.join(__dirname, ".build", "s_items.html"), "utf8");
  const dashHtml = fs.readFileSync(path.join(__dirname, ".build", "s_dash.html"), "utf8");
  check("旧的 seg-more 小灰字入口没了", !itemsHtml.includes("seg-more"));
  check("旧的 sec-manage 小灰字入口没了", !dashHtml.includes("sec-manage"));
  check("对应的旧样式规则也删了",
    !/\.seg-more\s*\{/.test(css) && !/\.sec-manage\s*\{/.test(css));
}

/* ---------- 同城换谷关掉后的出厂状态 ---------- */
{
  const env = load("s_dash_off.html", "http://127.0.0.1:5000/");
  const doc = env.window.document;
  check("关掉换谷后首页没有换谷板块",
    !doc.getElementById("exchangeLink")
    && doc.body.textContent.indexOf("同城换谷") < 0);
  check("关掉换谷后页面里根本没有换谷弹窗", !doc.getElementById("exchangeModal"));

  doc.querySelector(".fab").click();
  const items = [...doc.querySelectorAll("#addSheet .sheet-item")];
  check("面板里没有换谷选项",
    !doc.querySelector('#addSheet [data-modal-open="exchangeModal"]'));
  check("面板剩下 6 项（谷子/在途/已出/心愿单/再贩提醒 + 分类管理）",
    items.length === 6, items.length);
  check("其余入口一个都没少",
    ['[data-add-kind="displaying"]', '[data-add-kind="in_transit"]',
     '[data-add-kind="sold"]', '[data-add-kind="wishlist"]',
     '[data-modal-open="reminderModal"]', '[data-modal-open="catModal"]']
      .every((sel) => !!doc.querySelector("#addSheet " + sel)));
  check("关掉换谷后无 JS 报错", env.pageErrors.length === 0, env.pageErrors.join(" | "));
}

/* ---------- 返回首页按钮：要够大够明显 ---------- */
{
  const env = load("s_items.html", "http://127.0.0.1:5000/items");
  const doc = env.window.document;
  const w = env.window;

  const top = doc.querySelector(".app-header .back-btn");
  check("顶栏的返回首页按钮带文字（不是光一个箭头）",
    !!top && top.textContent.trim() === "返回首页",
    top && top.textContent.trim());
  const tcs = w.getComputedStyle(top);
  check("顶栏按钮够大：高 40px、有内边距",
    tcs.height === "40px" && parseFloat(tcs.paddingLeft) >= 10
    && parseFloat(tcs.paddingRight) >= 10,
    `${tcs.height} / ${tcs.paddingLeft} / ${tcs.paddingRight}`);
  check("顶栏按钮是绿色胶囊（有底色、圆角）",
    tcs.backgroundColor === "var(--green-soft)"
    && tcs.color === "var(--green-deep)" && tcs.borderRadius === "14px",
    `${tcs.backgroundColor} / ${tcs.color} / ${tcs.borderRadius}`);
  check("顶栏按钮字重加粗、13px",
    tcs.fontWeight === "650" && tcs.fontSize === "13px",
    `${tcs.fontWeight} / ${tcs.fontSize}`);
  check("顶栏按钮是手型光标", tcs.cursor === "pointer", tcs.cursor);
  check("顶栏按钮真的回首页", top.getAttribute("href") === "/", top.getAttribute("href"));

  const foot = doc.querySelector(".sec-head a.back-link");
  check("页脚那个返回首页已经去掉了（不再有两处）", !foot);
  const backLinks = [...doc.querySelectorAll("a")].filter(
    (a) => a.textContent.indexOf("返回首页") >= 0);
  check("整页只有一个返回首页入口", backLinks.length === 1,
    backLinks.map((a) => a.getAttribute("href") + ":" + a.textContent.trim()));
  check("留下的那个就是顶栏按钮", backLinks[0] === top);
  check("页脚的 .back-link 样式规则也清掉了", !/\.back-link\s*\{/.test(css));
  check("返回首页页无 JS 报错", env.pageErrors.length === 0, env.pageErrors.join(" | "));
}

/* ---------- 统计块：点了去看这一类全部谷子 ---------- */
{
  const env = load("s_dash.html", "http://127.0.0.1:5000/");
  const doc = env.window.document;
  const w = env.window;
  const pairs = [["statDisplaying", "/items"], ["statInTransit", "/transit"],
                 ["statSold", "/sold"], ["statWishlist", "/wishlist"]];
  check("四个统计块都是链接，指向对应专区页",
    pairs.every(([id, href]) => {
      const el = doc.getElementById(id);
      return el && el.tagName === "A" && el.getAttribute("href") === href;
    }),
    pairs.map(([id]) => {
      const el = doc.getElementById(id);
      return el ? el.getAttribute("href") : "缺 " + id;
    }));
  check("统计块是手型光标",
    pairs.every(([id]) => w.getComputedStyle(doc.getElementById(id)).cursor === "pointer"));
  check("统计块数字和标签都还在",
    ["展示中", "在途", "已出", "心愿单"].every((label) =>
      pairs.some(([id]) => doc.getElementById(id).textContent.indexOf(label) >= 0)));
  check("统计块无 JS 报错", env.pageErrors.length === 0, env.pageErrors.join(" | "));
}

/* ---------- 首页顶栏：头像是个人信息入口 ---------- */
{
  const env = load("s_dash.html", "http://127.0.0.1:5000/");
  const doc = env.window.document;
  const acts = doc.querySelector(".app-header .header-acts");
  check("首页顶栏头像是个人信息入口",
    !!acts && !!acts.querySelector('a.avatar-btn[href="/profile"]'));
  check("首页顶栏有账号与数据入口",
    !!acts && !!acts.querySelector('a[href="/account-data"]'));
  check("顶栏就是头像 + 账号与数据 + 退出三个按钮",
    acts && acts.querySelectorAll(".avatar-btn, .icon-btn").length === 3,
    acts && acts.querySelectorAll(".avatar-btn, .icon-btn").length);
  check("没有头像时显示用户名首字", !!doc.querySelector(".avatar-btn span"));
  check("退出仍然是表单提交（不是链接）",
    !!acts.querySelector('button[type="submit"]').closest("form"));
  check("首页无 JS 报错", env.pageErrors.length === 0, env.pageErrors.join(" | "));
}

/* ---------- 个人信息页：白底只读列表 ---------- */
{
  const env = load("s_profile.html", "http://127.0.0.1:5000/profile");
  const doc = env.window.document;

  check("是白底版式（没有渐变底和光斑）",
    !!doc.querySelector(".phone.plain") && doc.querySelectorAll(".blob").length === 0);
  const rows = [...doc.querySelectorAll(".wx-item")];
  check("列表正好五行（头像 / 名字 / 签名 / 地区 + 账号与数据）",
    rows.length === 5, rows.length);
  check("每一行都是「左标签 + 右内容 + 箭头」",
    rows.every((r) => !!r.querySelector(".wx-label") && !!r.querySelector(".wx-value")
      && !!r.querySelector(".wx-arrow")),
    rows.map((r) => r.textContent.replace(/\s+/g, " ").trim()));
  check("前三行的标签分别是头像 / 名字 / 签名 / 地区",
    rows.slice(0, 4).map((r) => r.querySelector(".wx-label").textContent.trim())
      .join(",") === "头像,名字,签名,地区",
    rows.slice(0, 4).map((r) => r.querySelector(".wx-label").textContent.trim()));
  check("这四行分别跳到四个编辑页 + 账号与数据",
    rows[0].getAttribute("href") === "/profile/avatar"
    && rows[1].getAttribute("href") === "/profile/username"
    && rows[2].getAttribute("href") === "/profile/signature"
    && rows[3].getAttribute("href") === "/profile/region"
    && rows[4].getAttribute("href") === "/account-data",
    rows.map((r) => r.getAttribute("href")));
  check("头像那行右侧是缩略图（没头像时是首字）",
    !!rows[0].querySelector(".wx-thumb"));
  check("名字那行右侧就是当前用户名",
    rows[1].querySelector(".wx-value").textContent.trim().length > 0);
  check("主页面不放表单和保存按钮",
    doc.querySelectorAll("form").length === 0
    && doc.querySelectorAll("button").length === 0);
  check("保留标题与返回首页按钮",
    !!doc.querySelector(".app-header h1")
    && !!doc.querySelector('.back-btn[href="/"]'));
  check("个人信息页无 JS 报错", env.pageErrors.length === 0, env.pageErrors.join(" | "));
}

/* ---------- 头像编辑页 ---------- */
{
  const env = load("s_profile_avatar.html", "http://127.0.0.1:5000/profile/avatar");
  const doc = env.window.document;
  const form = doc.querySelector('form[action="/profile/avatar"]');
  check("头像编辑页是白底 + 返回个人信息",
    !!doc.querySelector(".phone.plain")
    && doc.querySelector(".back-btn").getAttribute("href") === "/profile");
  check("保留选文件（multipart）和本地预览钩子",
    form && form.getAttribute("enctype") === "multipart/form-data"
    && !!form.querySelector('input[type="file"][data-preview="avatarPreview"]'));
  check("保留保存头像按钮", !!doc.querySelector("#avatarSaveBtn"));
  check("没有头像时不显示删除按钮", !doc.querySelector("#avatarRemoveBtn"));
  check("保留格式提示文字",
    doc.body.textContent.indexOf("png / jpg / gif / webp") >= 0);
  check("头像编辑页无 JS 报错", env.pageErrors.length === 0, env.pageErrors.join(" | "));
}

/* ---------- 名字编辑页 ---------- */
{
  const env = load("s_profile_username.html", "http://127.0.0.1:5000/profile/username");
  const doc = env.window.document;
  check("名字编辑页是白底 + 返回个人信息",
    !!doc.querySelector(".phone.plain")
    && doc.querySelector(".back-btn").getAttribute("href") === "/profile");
  check("保留输入框 + 按钮 + 提示",
    !!doc.querySelector("#newUsername") && !!doc.querySelector("#renameBtn")
    && doc.body.textContent.indexOf("登录名") >= 0);
  check("输入框带 maxlength（和后台校验一致）",
    doc.querySelector("#newUsername").getAttribute("maxlength") === "32",
    doc.querySelector("#newUsername").getAttribute("maxlength"));
  check("名字编辑页无 JS 报错", env.pageErrors.length === 0, env.pageErrors.join(" | "));
}

/* ---------- 地区选择页：国家 → 省 → 市 → 区/县 四级联动 ---------- */
pending.push((async () => {
  const TREE = { "中国": { "广东省": { "深圳市": ["南山区", "福田区"] },
                           "上海市": { "市辖区": ["徐汇区", "静安区"] } } };
  const env = load("s_profile_region.html", "http://127.0.0.1:5000/profile/region",
    { tree: TREE });
  const doc = env.window.document;
  const $ = (s) => doc.querySelector(s);
  const fire = (el) => el.dispatchEvent(new env.window.Event("change"));

  check("地区页是白底 + 返回个人信息",
    !!doc.querySelector(".phone.plain")
    && doc.querySelector(".back-btn").getAttribute("href") === "/profile");
  check("四级下拉都在（国家 / 省 / 市 / 区县）",
    ["#selCountry", "#selProvince", "#selCity", "#selArea"]
      .every((s) => !!$(s)));
  check("国家默认选中中国", $("#selCountry").value === "中国",
    $("#selCountry").value);
  check("有「其他（手动填写）」兜底",
    !!$('#selCountry option[value="__manual__"]'));

  await flush();                       // 等页面里那个 fetch 的微任务跑完

  // 选省 → 市下拉要跟着填出来
  $("#selProvince").value = "广东省";
  fire($("#selProvince"));
  const cityOpts = [...$("#selCity").options].map((o) => o.value);
  check("选了广东省，市下拉自动出现深圳市",
    cityOpts.indexOf("深圳市") >= 0, cityOpts);

  // 选市 → 区/县下拉跟着填出来
  $("#selCity").value = "深圳市";
  fire($("#selCity"));
  const areaOpts = [...$("#selArea").options].map((o) => o.value);
  check("选了深圳市，区/县下拉出现南山区",
    areaOpts.indexOf("南山区") >= 0, areaOpts);

  // 换省 → 下面的市、区要清空重填
  $("#selProvince").value = "上海市";
  fire($("#selProvince"));
  const cityOpts2 = [...$("#selCity").options].map((o) => o.value);
  check("换省之后市下拉换成新省的（上海市→市辖区）",
    cityOpts2.indexOf("市辖区") >= 0 && cityOpts2.indexOf("深圳市") < 0, cityOpts2);
  check("换省之后区/县那一栏先清空",
    [...$("#selArea").options].length === 1,
    [...$("#selArea").options].map((o) => o.value));

  // 切到「其他（手动填写）」→ 手动输入框出现，三个下拉收起
  $("#selCountry").value = "__manual__";
  fire($("#selCountry"));
  check("选「其他」后手动输入框出现",
    !doc.querySelector("#wrapManual").hidden);
  check("选「其他」后三个下拉收起",
    doc.querySelector("#wrapProvince").hidden
    && doc.querySelector("#wrapCity").hidden
    && doc.querySelector("#wrapArea").hidden);

  // 切回中国 → 下拉恢复、手动框收起
  $("#selCountry").value = "中国";
  fire($("#selCountry"));
  check("切回中国后下拉又出现、手动框收起",
    !doc.querySelector("#wrapProvince").hidden
    && doc.querySelector("#wrapManual").hidden);

  check("保留定位按钮和放坐标的字段",
    !!$("#locateBtn") && !!$("#locateLat") && !!$("#locateLon"));
  check("保留定位说明（只到市级）",
    $("#locateHint").textContent.indexOf("最近") >= 0);
  check("保留保存地区按钮", !!$("#regionSaveBtn"));
  check("地区页无 JS 报错", env.pageErrors.length === 0, env.pageErrors.join(" | "));
})());

/* ---------- 签名编辑页 ---------- */
{
  const env = load("s_profile_signature.html", "http://127.0.0.1:5000/profile/signature");
  const doc = env.window.document;
  check("签名页是白底 + 返回个人信息",
    !!doc.querySelector(".phone.plain")
    && doc.querySelector(".back-btn").getAttribute("href") === "/profile");
  check("签名页有输入框 + 保存按钮 + 60 字上限",
    !!doc.querySelector("#signatureInput") && !!doc.querySelector("#signatureSaveBtn")
    && doc.querySelector("#signatureInput").getAttribute("maxlength") === "60");
  check("签名页说明会显示在哪里",
    doc.body.textContent.indexOf("问候语") >= 0);
  check("签名页无 JS 报错", env.pageErrors.length === 0, env.pageErrors.join(" | "));
}

/* ---------- 账号与数据：独立一页，只有两块入口 ---------- */
{
  const env = load("s_account_data.html", "http://127.0.0.1:5000/account-data");
  const doc = env.window.document;
  check("账号与数据页有两个入口",
    doc.querySelectorAll(".nav-row").length === 2,
    doc.querySelectorAll(".nav-row").length);
  check("两个入口分别指向账号与安全 / 数据备份",
    !!doc.querySelector('a[href="/account"]') && !!doc.querySelector('a[href="/backup"]'));
  check("这一页自己不做事（没有表单、没有密码框）",
    doc.querySelectorAll("form").length === 0
    && !doc.querySelector('input[type="password"]'));
  check("说明了个人信息在另一页",
    !!doc.querySelector('a[href="/profile"]'));
  check("返回按钮回首页",
    doc.querySelector(".back-btn").getAttribute("href") === "/");
  check("账号与数据页无 JS 报错", env.pageErrors.length === 0, env.pageErrors.join(" | "));
}

/* ---------- 账号与安全：菜单 + 下钻页 ---------- */
{
  const env = load("s_account.html", "http://127.0.0.1:5000/account");
  const doc = env.window.document;
  check("账号与安全只列两个选项",
    doc.querySelectorAll(".nav-row").length === 2,
    doc.querySelectorAll(".nav-row").length);
  check("两个选项分别指向改密码和登录与设备",
    !!doc.querySelector('a[href="/account/password"]')
    && !!doc.querySelector('a[href="/account/sessions"]'));
  check("菜单页上没有密码输入框（点进去才做事）",
    !doc.querySelector('input[type="password"]'));
  check("返回按钮回账号与数据页",
    doc.querySelector(".back-btn").getAttribute("href") === "/account-data");
  check("账号与安全菜单无 JS 报错", env.pageErrors.length === 0, env.pageErrors.join(" | "));
}
{
  const env = load("s_account_password.html", "http://127.0.0.1:5000/account/password");
  const doc = env.window.document;
  const inputs = [...doc.querySelectorAll('input[type="password"]')];
  check("改密码页有三个密码框", inputs.length === 3, inputs.length);
  check("密码框的 autocomplete 标对了",
    doc.querySelector("#oldPassword").getAttribute("autocomplete") === "current-password"
    && doc.querySelector("#newPassword").getAttribute("autocomplete") === "new-password"
    && doc.querySelector("#confirmPassword").getAttribute("autocomplete") === "new-password");
  check("返回按钮回账号与安全页",
    doc.querySelector(".back-btn").getAttribute("href") === "/account");
  check("改密码页无 JS 报错", env.pageErrors.length === 0, env.pageErrors.join(" | "));
}
{
  const env = load("s_account_sessions.html", "http://127.0.0.1:5000/account/sessions");
  const doc = env.window.document;
  check("登录与设备页有退出其它设备按钮",
    !!doc.querySelector('form[action="/account/sessions"] button[type="submit"]'));
  check("退出其它设备要先确认",
    !!doc.querySelector('form[action="/account/sessions"]').dataset.confirm);
  check("登录与设备页无 JS 报错",
    env.pageErrors.length === 0, env.pageErrors.join(" | "));
}

/* ---------- 数据备份页 ---------- */
{
  const env = load("s_backup.html", "http://127.0.0.1:5000/backup");
  const doc = env.window.document;
  check("备份页有导出入口", !!doc.querySelector('a[href="/backup/export"]'));
  check("备份页的导入表单是 multipart",
    doc.querySelector('form[action="/backup/import"]')
      .getAttribute("enctype") === "multipart/form-data");
  check("备份页有立即备份按钮",
    !!doc.querySelector('form[action="/backup/now"] button[type="submit"]'));
  check("备份页样式用的是通用 set-* 类",
    !!doc.querySelector(".set-card") && !!doc.querySelector(".set-btn"));
  check("备份页无 JS 报错", env.pageErrors.length === 0, env.pageErrors.join(" | "));
}

// 地区页那组是异步的（页面要等 fetch 回来），等它跑完再汇总
Promise.all(pending).then(() => {
  let failed = 0;
  for (const r of all) {
    if (!r.ok) failed++;
    console.log((r.ok ? "  PASS  " : "  FAIL  ") + r.label + (r.ok ? "" : "   -> " + r.detail));
  }
  console.log("-".repeat(52));
  if (failed) { console.log(`FAILED ${failed}/${all.length}`); process.exit(1); }
  console.log(`jsdom 检查全部通过（${all.length} 项）`);
});
