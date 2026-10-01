window.onerror = (m, u, l) => { document.body.dataset.err = (document.body.dataset.err || "") + m + "@" + l + ";"; };
const w = ms => new Promise(r => setTimeout(r, ms));
const R = []; const log = (k, v) => R.push(k + "=" + String(v).replace(/\s+/g, " ").slice(0, 220));
const logLines = () => [...document.querySelectorAll("#conLog")][0].innerText.split("\n").filter(Boolean).map(x => x.replace(/^\d\d:\d\d:\d\d\s+/, "")).join(" ¦ ");
const closeCon = () => { const c = document.getElementById("console"); if (c.dataset.state === "error") { c.classList.remove("open"); c.dataset.state = "idle"; } else document.getElementById("conAction").click(); };
const ent = (el, v) => { el.value = v; el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter" })); };
(async () => {
  try {
    // п. 6: модели существующих версий
    location.hash = "#vacancy/137493556"; await w(200);
    document.querySelector("[data-ctab=letter]").click(); await w(50);
    const foot = () => document.querySelector(".letter-foot span").innerText;
    const vers = [];
    for (const v of [1, 2, 3, 4, 5]) { document.querySelector(`[data-ver="${v}"]`).click(); await w(20); vers.push("v" + v + ":" + foot().split(", ")[1]); }
    log("6.itransitionModels", vers.join(" "));
    document.querySelector("[data-ctab=tips]").click(); await w(50);
    log("6.tipsLine", document.querySelector(".versions").innerText);

    // п. 6: смена модели без сохранения не влияет, после сохранения — влияет
    location.hash = "#settings"; await w(200); document.querySelector("[data-stab=llm]").click();
    const cvSec = document.querySelector('[data-task="cv_processing"]');
    const m = cvSec.querySelector("[data-model]"); m.value = "claude-opus-5-5"; m.dispatchEvent(new Event("input", { bubbles: true }));
    location.hash = "#vacancy/137828745"; await w(200);
    log("6.guard", document.getElementById("dlg").open);
    document.querySelector('#dlgActions button[value="discard"]').click(); await w(200);
    document.querySelector("[data-ctab=letter]").click(); await w(50);
    document.querySelector("[data-run=cover-letter]").click(); await w(5000);
    log("6.unsavedModelLog", logLines());
    log("6.newVersionFoot", foot());
    closeCon();
    location.hash = "#settings"; await w(200); document.querySelector("[data-stab=llm]").click();
    m.value = "claude-opus-5-5"; m.dispatchEvent(new Event("input", { bubbles: true }));
    document.getElementById("saveSettings").click(); await w(50);
    location.hash = "#vacancy/137828745"; await w(200);
    document.querySelector("[data-ctab=letter]").click(); await w(50);
    document.querySelector("[data-run=cover-letter]").click(); await w(5000);
    log("6.savedModelLog", logLines());
    log("6.savedVersionFoot", document.querySelector(".versions").innerText + " | " + foot());
    closeCon();

    // п. 6: провайдер без ключа
    location.hash = "#settings"; await w(200); document.querySelector("[data-stab=llm]").click();
    const prov = cvSec.querySelector("[data-provider]"); prov.value = "deepseek"; prov.dispatchEvent(new Event("change", { bubbles: true }));
    document.getElementById("saveSettings").click(); await w(50);
    location.hash = "#vacancy/137828745"; await w(200);
    document.querySelector("[data-ctab=tips]").click(); await w(50);
    document.querySelector("[data-run=cv-tips]").click(); await w(3000);
    log("6.tipsNoKey", document.getElementById("console").dataset.state + " | " + document.getElementById("conMsg").innerText);
    closeCon();
    location.hash = "#overview"; await w(200);
    document.getElementById("analyzeToggle").click(); document.querySelector("#analyzePanel [data-run]").click(); await w(7000);
    log("6.analyzeNoKey", document.getElementById("console").dataset.state + " | " + document.getElementById("conMsg").innerText);
    log("6.analyzeNoKeyLog", logLines());
    closeCon();
    // --no-llm
    document.getElementById("analyzeToggle").click(); document.getElementById("noLlm").click();
    document.querySelector("#analyzePanel [data-run]").click(); await w(6000);
    log("6.noLlm", document.getElementById("console").dataset.state + " | " + logLines());
    closeCon(); document.getElementById("noLlm").click();
    // вернуть anthropic + sonnet, анализ с моделью → подписи
    location.hash = "#settings"; await w(200); document.querySelector("[data-stab=llm]").click();
    prov.value = "anthropic"; prov.dispatchEvent(new Event("change", { bubbles: true }));
    const va = document.querySelector('[data-task="vacancy_analysis"] [data-model]'); va.value = "claude-sonnet-5-5"; va.dispatchEvent(new Event("input", { bubbles: true }));
    document.getElementById("saveSettings").click(); await w(50);
    location.hash = "#overview"; await w(200);
    document.getElementById("analyzeToggle").click(); document.querySelector("#analyzePanel [data-run]").click(); await w(7000);
    log("6.analyzeOk", document.getElementById("console").dataset.state + " | " + logLines());
    closeCon();
    document.querySelector("[data-rank=llm]").click(); await w(30);
    log("6.barsNote", document.getElementById("barsNote").innerText);
    log("6.gapCallout", document.querySelector("[data-analysis-model]").innerText);

    // п. 7: панель сбора из профиля
    location.hash = "#vacancies"; await w(200);
    document.getElementById("fetchToggle").click(); await w(50);
    const checked = g => [...document.querySelectorAll(`#fetchPanel [data-group="${g}"] input:checked`)].map(i => i.dataset.arg.split(" ").slice(1).join(" ")).join(",");
    log("7.default", ["region", "experience", "employment", "schedule"].map(g => g + ":" + checked(g)).join(" ") + " roles:" + document.querySelectorAll('#fetchPanel [data-group="role"] input').length);
    log("7.defaultCmd", document.querySelector("#fetchPanel [data-preview]").innerText);
    // меняем профиль: Минск вместо России, без «1–3», удалённо только, новая роль, зарплата
    location.hash = "#settings"; await w(200); document.querySelector("[data-stab=search]").click();
    const set = (k, v, on) => { const i = document.querySelector(`[data-pane="search"] [data-s="${k}"][value="${v}"]`); i.checked = on; i.dispatchEvent(new Event("change", { bubbles: true })); };
    set("region", "113", false); set("region", "1002", true); set("experience", "between1And3", false);
    set("schedule", "office", false); set("schedule", "hybrid", false);
    ent(document.querySelector("[data-list=roles] .chip-add"), "Product Analyst");
    const ms = document.getElementById("minSalary"); ms.value = "3000"; ms.dispatchEvent(new Event("input", { bubbles: true }));
    const cur = document.getElementById("currency"); cur.value = "BYR"; cur.dispatchEvent(new Event("change", { bubbles: true }));
    location.hash = "#vacancies"; await w(200);
    log("7.beforeSave.regions", checked("region") + " roles:" + document.querySelectorAll('#fetchPanel [data-group="role"] input').length);
    location.hash = "#settings"; await w(200);
    document.getElementById("saveSettings").click(); await w(50);
    location.hash = "#vacancies"; await w(200);
    if (document.getElementById("fetchPanel").hidden) document.getElementById("fetchToggle").click();
    log("7.afterSave", ["region", "experience", "schedule"].map(g => g + ":" + checked(g)).join(" ") + " roles:" + document.querySelectorAll('#fetchPanel [data-group="role"] input').length
      + " salary:" + document.querySelector("#fetchPanel [data-argname]").placeholder + " " + document.querySelector("#fetchPanel [data-argname]").nextElementSibling.innerText);
    log("7.afterSaveCmd", document.querySelector("#fetchPanel [data-preview]").innerText);
    // разовое изменение в панели → флаги
    document.querySelector('#fetchPanel [data-arg="--region 40"]').click();
    log("7.overrideCmd", document.querySelector("#fetchPanel [data-preview]").innerText);
    document.querySelector("#fetchPanel [data-run]").click(); await w(7000);
    log("7.fetchLog", logLines().split(" ¦ ").slice(0, 2).join(" ¦ "));
  } catch (x) { log("EXC", x.message + " " + x.stack); }
  log("errors", document.body.dataset.err || "none");
  document.body.setAttribute("data-r", R.join(" || "));
})();
