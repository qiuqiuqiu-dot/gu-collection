/* jsdom：统一的「+」——先选要添加什么，再打开对应的表单 */
const fs = require("fs");
const path = require("path");
const { JSDOM, VirtualConsole } = require(require("./jsdom_path"));

const all = [];
function check(label, ok, detail) {
  all.push({ label, ok: !!ok, detail: ok ? "" : String(detail) });
}

function load(file, url) {
  const pageErrors = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("jsdomError", (e) => pageErrors.push("jsdomError: " + e.message));
  virtualConsole.on("error", (...a) => pageErrors.push("console.error: " + a.join(" ")));
  const dom = new JSDOM(fs.readFileSync(path.join(__dirname, ".build", file), "utf8"), {
    runScripts: "dangerously",
    url: url || "http://127.0.0.1:5000/",
    pretendToBeVisual: true,
    virtualConsole,
  });
  const window = dom.window;
  window.addEventListener("error", (e) => pageErrors.push("window.onerror: " + e.message));
  window.confirm = function () { return false; };
  return { window, pageErrors };
}

const shown = (doc, id) => doc.getElementById(id).classList.contains("show");
const statusSel = (doc) => doc.querySelector("#addModal select[name='status']");
const fields = (doc, kind) => doc.querySelector(`[data-status-fields="${kind}"]`);

/* ---------- 仪表盘：面板 → 谷子表单的四种状态 ---------- */
{
  const env = load("s_dash.html", "http://127.0.0.1:5000/");
  const doc = env.window.document;
  const q = (s) => doc.querySelector(s);

  check("页面只有一个 + 号", doc.querySelectorAll(".fab").length === 1,
    doc.querySelectorAll(".fab").length);
  check("六种记录类型 + 分类管理都在面板里",
    doc.querySelectorAll("#addSheet .sheet-item").length === 7,
    doc.querySelectorAll("#addSheet .sheet-item").length);

  // 1. 加号只开面板，不直接开表单
  q(".fab").click();
  check("加号打开「要添加什么」面板", shown(doc, "addSheet"));
  check("加号没有顺手把谷子表单也打开", !shown(doc, "addModal"));
  check("面板上有七个选项可点",
    doc.querySelectorAll("#addSheet .sheet-item").length === 7);

  // 2. 选「在途」→ 同一张谷子表单，状态预设成在途
  q('[data-add-kind="in_transit"]').click();
  check("选在途后面板收起", !shown(doc, "addSheet"));
  check("选在途后打开谷子表单", shown(doc, "addModal"));
  check("标题写成「添加在途的谷子」",
    doc.getElementById("addFormTitle").textContent === "添加在途的谷子",
    doc.getElementById("addFormTitle").textContent);
  check("状态预设成在途", statusSel(doc).value === "in_transit", statusSel(doc).value);
  check("在途专属字段展开", !fields(doc, "in_transit").hidden);
  check("已出字段是收起的", fields(doc, "sold").hidden);
  check("心愿字段是收起的", fields(doc, "wishlist").hidden);

  // 3. 选「心愿单」
  q("#addModal [data-modal-close='addModal']").click();
  q(".fab").click();
  q('[data-add-kind="wishlist"]').click();
  check("选心愿单后状态是 wishlist", statusSel(doc).value === "wishlist",
    statusSel(doc).value);
  check("标题写成「添加心愿单」",
    doc.getElementById("addFormTitle").textContent === "添加心愿单",
    doc.getElementById("addFormTitle").textContent);
  check("心愿字段展开、在途字段收起",
    !fields(doc, "wishlist").hidden && fields(doc, "in_transit").hidden);

  // 4. 选「已出」：出了几件是准绳，选已出时已出件数自动补成总数
  q("#addModal [data-modal-close='addModal']").click();
  q(".fab").click();
  q('[data-add-kind="sold"]').click();
  check("选已出后状态是 sold", statusSel(doc).value === "sold", statusSel(doc).value);
  check("标题写成「添加已出记录」",
    doc.getElementById("addFormTitle").textContent === "添加已出记录",
    doc.getElementById("addFormTitle").textContent);
  check("已出件数自动补成总数（整件出完）",
    q("#addModal input[name='sold_count']").value
      === q("#addModal input[name='count']").value,
    `${q("#addModal input[name='sold_count']").value} / ${q("#addModal input[name='count']").value}`);
  check("出货字段展开", !fields(doc, "sold").hidden);
  check("在途字段收起", fields(doc, "in_transit").hidden);

  // 5. 选「再贩提醒」→ 走提醒弹窗，而不是谷子表单
  q("#addModal [data-modal-close='addModal']").click();
  q(".fab").click();
  q('#addSheet [data-modal-open="reminderModal"]').click();
  check("选提醒后打开提醒弹窗", shown(doc, "reminderModal"));
  check("选提醒后面板收起", !shown(doc, "addSheet"));
  check("提醒没有误开谷子表单", !shown(doc, "addModal"));
  check("提醒标题是新增",
    doc.getElementById("reminderFormTitle").textContent === "添加再贩提醒",
    doc.getElementById("reminderFormTitle").textContent);
  check("提醒走的是新增地址（不是某条的编辑地址）",
    /\/reminders\/add$/.test(doc.getElementById("reminderForm").getAttribute("action")),
    doc.getElementById("reminderForm").getAttribute("action"));
  check("提醒字段是空的（新开一份）",
    doc.getElementById("remTitle").value === ""
    && doc.getElementById("remTime").value === ""
    && doc.getElementById("remLink").value === "",
    [doc.getElementById("remTitle").value, doc.getElementById("remTime").value].join("|"));

  // 6. 选「换谷信息」→ 走换谷弹窗
  doc.querySelector("#reminderModal [data-modal-close='reminderModal']").click();
  q(".fab").click();
  q('#addSheet [data-modal-open="exchangeModal"]').click();
  check("选换谷后打开换谷弹窗", shown(doc, "exchangeModal"));
  check("选换谷后面板收起", !shown(doc, "addSheet"));
  check("换谷标题是发布",
    doc.getElementById("exchangeFormTitle").textContent === "发布换谷信息",
    doc.getElementById("exchangeFormTitle").textContent);
  check("换谷走的是发布地址",
    /\/exchanges\/add$/.test(doc.getElementById("exchangeForm").getAttribute("action")),
    doc.getElementById("exchangeForm").getAttribute("action"));

  // 7. 谷子表单里能直接进分类管理，关掉还能回到表单
  doc.querySelector("#exchangeModal [data-modal-close='exchangeModal']").click();
  q(".fab").click();
  q('[data-add-kind="displaying"]').click();
  check("选谷子时状态回到展示中", statusSel(doc).value === "displaying",
    statusSel(doc).value);
  q("#addModal [data-modal-open='catModal']").click();
  check("谷子表单里能打开分类管理", shown(doc, "catModal"));
  check("分类管理是叠在谷子表单上的", shown(doc, "addModal"));
  doc.querySelector("#catModal [data-modal-close='catModal']").click();
  check("关掉分类管理后谷子表单还在", shown(doc, "addModal"));

  check("仪表盘无 JS 报错", env.pageErrors.length === 0, env.pageErrors.join(" | "));
}

/* ---------- 空状态里的一键添加 ---------- */
{
  const env = load("s_transit_empty.html", "http://127.0.0.1:5000/transit");
  const doc = env.window.document;
  const btn = doc.querySelector(".link-btn[data-add-kind='in_transit']");
  check("在途空状态有「记一件在途」按钮", !!btn);
  btn.click();
  check("点它直接开谷子表单", shown(doc, "addModal"));
  check("点它不经过选择面板", !shown(doc, "addSheet"));
  check("点它状态就是在途", statusSel(doc).value === "in_transit", statusSel(doc).value);
  check("在途空状态页无 JS 报错", env.pageErrors.length === 0, env.pageErrors.join(" | "));
}

{
  const env = load("s_sold_empty.html", "http://127.0.0.1:5000/sold");
  const doc = env.window.document;
  const btn = doc.querySelector(".link-btn[data-add-kind='sold']");
  check("已出空状态有「记一件已出」按钮", !!btn);
  btn.click();
  check("点它状态就是已出", statusSel(doc).value === "sold", statusSel(doc).value);
  check("点它已出件数补成总数",
    doc.querySelector("#addModal input[name='sold_count']").value
      === doc.querySelector("#addModal input[name='count']").value);
  check("已出空状态页无 JS 报错", env.pageErrors.length === 0, env.pageErrors.join(" | "));
}

{
  const env = load("s_wishlist_empty.html", "http://127.0.0.1:5000/wishlist");
  const doc = env.window.document;
  doc.querySelector(".link-btn[data-add-kind='wishlist']").click();
  check("心愿单空状态按钮把状态设成心愿单",
    statusSel(doc).value === "wishlist", statusSel(doc).value);
  check("心愿单空状态页无 JS 报错", env.pageErrors.length === 0, env.pageErrors.join(" | "));
}

/* ---------- 提醒 / 换谷的空状态也能直接开表单 ---------- */
{
  const env = load("s_reminders_empty.html", "http://127.0.0.1:5000/reminders");
  const doc = env.window.document;
  const btn = doc.querySelector(".link-btn[data-modal-open='reminderModal']");
  check("提醒空状态有「记一个再贩提醒」按钮", !!btn);
  btn.click();
  check("点它直接开提醒弹窗（不经过面板）",
    shown(doc, "reminderModal") && !shown(doc, "addSheet"));
  check("点它是新增模式",
    doc.getElementById("reminderFormTitle").textContent === "添加再贩提醒"
    && doc.getElementById("remTitle").value === "");
  check("提醒空状态页无 JS 报错", env.pageErrors.length === 0, env.pageErrors.join(" | "));
}

{
  const env = load("s_exchanges_empty.html", "http://127.0.0.1:5000/exchanges");
  const doc = env.window.document;
  const btn = doc.querySelector(".link-btn[data-modal-open='exchangeModal']");
  check("换谷空状态有「发布一条换谷信息」按钮", !!btn);
  btn.click();
  check("点它直接开换谷弹窗（不经过面板）",
    shown(doc, "exchangeModal") && !shown(doc, "addSheet"));
  check("点它是发布模式",
    doc.getElementById("exchangeFormTitle").textContent === "发布换谷信息");
  check("换谷空状态页无 JS 报错", env.pageErrors.length === 0, env.pageErrors.join(" | "));
}

/* ---------- 谷柜页 / 在途页也有同一个入口 ---------- */
for (const [file, url] of [["s_items.html", "/items"], ["s_transit.html", "/transit"]]) {
  const env = load(file, "http://127.0.0.1:5000" + url);
  const doc = env.window.document;
  doc.querySelector(".fab").click();
  check(`${url} 的 + 号也开面板`, shown(doc, "addSheet"));
  doc.querySelector('[data-add-kind="displaying"]').click();
  check(`${url} 也能直接加谷子`, shown(doc, "addModal"));
  check(`${url} 无 JS 报错`, env.pageErrors.length === 0, env.pageErrors.join(" | "));
}

/* ---------- 面板里的「分类管理」 ---------- */
{
  const env = load("s_dash.html", "http://127.0.0.1:5000/");
  const doc = env.window.document;
  const q = (s) => doc.querySelector(s);

  q(".fab").click();
  check("面板里有分类管理这一项", shown(doc, "addSheet")
    && !!q('#addSheet [data-modal-open="catModal"]'));
  const manage = q("#addSheet .sheet-item.is-manage");
  check("分类管理单独一项，排在记录类型后面", !!manage
    && doc.querySelectorAll("#addSheet .sheet-item")[6] === manage);
  check("分类管理的说明写明是加品类/IP",
    manage.textContent.indexOf("周边品类") >= 0
    && manage.textContent.indexOf("作品IP") >= 0,
    manage.textContent.trim());
  check("和上面几种之间有分隔线", !!q("#addSheet .sheet-sep"));

  q("#addSheet .sheet-item.is-manage").click();
  check("点分类管理后打开分类管理弹窗", shown(doc, "catModal"));
  check("点分类管理后面板收起", !shown(doc, "addSheet"));
  check("点分类管理没有误开谷子表单", !shown(doc, "addModal"));

  q("#catModal [data-modal-close='catModal']").click();
  check("关掉后可以再从面板进去",
    (function () {
      q(".fab").click();
      q("#addSheet .sheet-item.is-manage").click();
      return shown(doc, "catModal") && !shown(doc, "addSheet");
    })());
  check("面板各项的数量没变（还是 7 项）",
    doc.querySelectorAll("#addSheet .sheet-item").length === 7);
  check("面板无 JS 报错", env.pageErrors.length === 0, env.pageErrors.join(" | "));
}

let failed = 0;
for (const r of all) {
  if (!r.ok) failed++;
  console.log((r.ok ? "  PASS  " : "  FAIL  ") + r.label + (r.ok ? "" : "   -> " + r.detail));
}
console.log("-".repeat(52));
if (failed) { console.log(`FAILED ${failed}/${all.length}`); process.exit(1); }
console.log(`jsdom 交互检查全部通过（${all.length} 项）`);
