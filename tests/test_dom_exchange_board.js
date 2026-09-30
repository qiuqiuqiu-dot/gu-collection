/* jsdom：同城换谷的板子（我发的可编辑、谷友的只读）+ 一个弹窗两用 */
const fs = require("fs");
const path = require("path");
const { JSDOM, VirtualConsole } = require(require("./jsdom_path"));

const all = [];
function check(label, ok, detail) {
  all.push({ label, ok: !!ok, detail: ok ? "" : String(detail) });
}

function load(file) {
  const pageErrors = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("jsdomError", (e) => pageErrors.push("jsdomError: " + e.message));
  virtualConsole.on("error", (...a) => pageErrors.push("console.error: " + a.join(" ")));
  const dom = new JSDOM(fs.readFileSync(path.join(__dirname, ".build", file), "utf8"), {
    runScripts: "dangerously",
    url: "http://127.0.0.1:5000/exchanges",
    pretendToBeVisual: true,
    virtualConsole,
  });
  const window = dom.window;
  window.addEventListener("error", (e) => pageErrors.push("window.onerror: " + e.message));
  let confirms = 0;
  window.confirm = function () { confirms++; return false; };
  return { window, pageErrors, confirms: () => confirms };
}

const names = (doc) => [...doc.querySelectorAll(".side-item .s-name")].map((p) => p.textContent.trim());

/* ---------- page_a：/exchanges ---------- */
{
  const env = load("page_a.html");
  const { window } = env;
  const $ = (s) => window.document.querySelector(s);
  const $$ = (s) => [...window.document.querySelectorAll(s)];

  check("A: 三条换谷信息都在", names(window.document).length === 3, names(window.document));
  check("A: 我发的排最前",
    names(window.document)[0] === "求换 星野吧唧", names(window.document).join(","));
  const rows = $$("[data-exchange-id]");
  const mine = rows.filter((r) => r.dataset.editUrl);
  check("A: 只有我发的带编辑地址", mine.length === 1, mine.length);
  check("A: 我发的标「我发的」",
    mine[0].querySelector(".s-pill").textContent.trim() === "我发的",
    mine[0].querySelector(".s-pill").textContent.trim());
  check("A: 我发的有编辑和撤下两个按钮",
    !!mine[0].querySelector("[data-exchange-edit]")
    && !!mine[0].querySelector('button[type="submit"]'));
  const mateRow = rows.find((r) => r.dataset.title === "求换 抹茶吧唧");
  check("A: 谷友同地区那条标「同城」",
    mateRow.querySelector(".s-pill").textContent.trim() === "同城",
    mateRow.querySelector(".s-pill").textContent.trim());
  check("A: 谷友那条没有编辑/撤下按钮",
    !mateRow.querySelector("[data-exchange-edit]")
    && !mateRow.querySelector('button[type="submit"]'));
  const farRow = rows.find((r) => r.dataset.title === "求换 星野立牌");
  check("A: 不同地区那条显示地区名",
    farRow.querySelector(".s-pill").textContent.trim() === "北京 朝阳",
    farRow.querySelector(".s-pill").textContent.trim());
  check("A: 详情行拼了可换/距离/地区",
    mine[0].querySelector(".s-src").textContent.trim()
      === "可换 白兔立牌 · 约 1.5km · 上海 徐汇",
    mine[0].querySelector(".s-src").textContent.trim());
  check("A: 联系行拼了地点/联系方式/说明",
    mine[0].querySelector(".s-meta").textContent.trim()
      === "地铁 2 号线站内 · 微信 me_1 · 只换原画",
    mine[0].querySelector(".s-meta").textContent.trim());
  check("A: 地区栏带了当前地区",
    $(".region-bar input[name='region']").value === "上海 徐汇",
    $(".region-bar input[name='region']").value);

  // 编辑自己的那条
  const modal = $("#exchangeModal");
  const form = $("#exchangeForm");
  const addAction = form.getAttribute("action");
  mine[0].querySelector("[data-exchange-edit]").click();
  check("A: 点编辑打开弹窗", modal.classList.contains("show"));
  check("A: 标题变「编辑换谷信息」",
    $("#exchangeFormTitle").textContent === "编辑换谷信息",
    $("#exchangeFormTitle").textContent);
  check("A: action 指向那条的编辑地址",
    form.getAttribute("action") === mine[0].dataset.editUrl, form.getAttribute("action"));
  check("A: 七个字段全部预填",
    $("#exTitle").value === "求换 星野吧唧" && $("#exWant").value === "白兔立牌"
    && $("#exEmoji").value === "🐰" && $("#exDistance").value === "1.5"
    && $("#exPlace").value === "地铁 2 号线站内" && $("#exContact").value === "微信 me_1"
    && $("#exNote").value === "只换原画",
    [$("#exTitle").value, $("#exWant").value, $("#exEmoji").value,
     $("#exDistance").value, $("#exPlace").value, $("#exContact").value,
     $("#exNote").value].join("|"));

  // 撤下要弹确认
  modal.querySelector(".modal-mask").click();
  const delBtn = mine[0].querySelector('button[type="submit"]');
  delBtn.form.addEventListener("submit", (e) => e.preventDefault(), { once: true });
  delBtn.click();
  check("A: 撤下会先弹确认框", env.confirms() > 0);

  // FAB → 选择面板 → 换谷信息，走发布模式
  $(".fab").click();
  check("A: + 号打到选择面板", $("#addSheet").classList.contains("show"));
  $('#addSheet [data-exchange-mode="add"]').click();
  check("A: 从面板选「换谷信息」是发布模式",
    $("#exchangeFormTitle").textContent === "发布换谷信息"
    && form.getAttribute("action") === addAction,
    `${$("#exchangeFormTitle").textContent} / ${form.getAttribute("action")}`);
  check("A: 发布模式下字段是空的",
    $("#exTitle").value === "" && $("#exWant").value === ""
    && $("#exPlace").value === "" && $("#exContact").value === "",
    $("#exTitle").value);
  check("A: 面板已经收起来了", !$("#addSheet").classList.contains("show"));
  check("A: 页面无 JS 报错", env.pageErrors.length === 0, env.pageErrors.join(" | "));
}

/* ---------- page_b：仪表盘 ---------- */
{
  const env = load("page_b.html");
  const { window } = env;
  const $ = (s) => window.document.querySelector(s);
  const $$ = (s) => [...window.document.querySelectorAll(s)];

  check("B: 仪表盘换谷卡片最多 4 张（这次正好 3 张）", $$(".ex-card").length === 3,
    $$(".ex-card").length);
  check("B: 我发的那张标「我发的」",
    $$(".ex-card").some((c) => c.textContent.indexOf("我发的") >= 0));
  check("B: 仪表盘的换谷板块现在是只读预览 + 能从 + 号发布",
    !!$("#exchangeModal") && !!$("#exchangeLink"),
    !!$("#exchangeModal"));
  check("B: 仪表盘 + 号打开选择面板（面板里有换谷入口）",
    (function () {
      $(".fab").click();
      return $("#addSheet").classList.contains("show")
        && !!$('#addSheet [data-modal-open="exchangeModal"]');
    })());
  check("B: 页面无 JS 报错", env.pageErrors.length === 0, env.pageErrors.join(" | "));
}

let failed = 0;
for (const r of all) {
  if (!r.ok) failed++;
  console.log((r.ok ? "  PASS  " : "  FAIL  ") + r.label + (r.ok ? "" : "   -> " + r.detail));
}
console.log("-".repeat(52));
if (failed) { console.log(`FAILED ${failed}/${all.length}`); process.exit(1); }
console.log(`jsdom 交互检查全部通过（${all.length} 项）`);
