window.onerror = (m, u, l) => { document.body.dataset.err = (document.body.dataset.err || "") + m + "@" + l + ";"; };
const w = ms => new Promise(r => setTimeout(r, ms));
const R = []; const log = (k, v) => R.push(k + "=" + String(v).replace(/\s+/g, " ").slice(0, 260));
const key = (el, k) => el.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true }));
const dlg = () => document.getElementById("dlg");
const press = v => document.querySelector(`#dlgActions button[value="${v}"]`).click();
(async () => {
  try {
    // п. 13
    document.querySelector("[data-rank=all]").click(); await w(30);
    log("13.all", !document.getElementById("notFound").hidden + " | " + document.getElementById("notFound").innerText);
    document.querySelector("[data-rank=top]").click(); await w(30);
    log("13.topHidden", document.getElementById("notFound").hidden);

    // п. 19: стрелки и tabindex
    const top = document.querySelector("[data-rank=top]");
    top.focus(); key(top, "ArrowRight"); await w(30);
    log("19.arrow", document.activeElement.dataset.rank + " selected:" + document.querySelector("[data-rank=all]").getAttribute("aria-selected"));
    key(document.activeElement, "End"); await w(30);
    log("19.end", document.activeElement.dataset.rank);
    key(document.activeElement, "ArrowRight"); await w(30);
    log("19.wrap", document.activeElement.dataset.rank);
    log("19.tabindex", [...document.querySelectorAll("[data-rank]")].map(b => b.dataset.rank + ":" + b.tabIndex).join(","));
    log("19.controls", document.querySelector("[data-rank=top]").getAttribute("aria-controls") + " / " + document.querySelector("[data-stab=dict]").getAttribute("aria-controls"));
    location.hash = "#vacancy/137828745"; await w(200);
    const ct = document.querySelector("[data-ctab=desc]"); ct.focus(); key(ct, "ArrowRight"); await w(50);
    log("19.cardArrow", document.activeElement.dataset.ctab + " body:" + document.getElementById("cardBody").innerText.slice(0, 20));
    document.querySelector("[data-ctab=tips]").click(); await w(30);
    log("19.mouseNoFocus", document.activeElement.dataset ? (document.activeElement.dataset.ctab || document.activeElement.tagName) : "-");

    // п. 11: объём письма
    location.hash = "#settings"; await w(200);
    document.querySelector("[data-stab=letter]").click();
    const mw = document.querySelector('[data-s="maxWords"]'); mw.value = "60"; mw.dispatchEvent(new Event("input", { bubbles: true }));
    document.getElementById("saveSettings").click(); await w(30);
    location.hash = "#vacancy/137828745"; await w(200);
    document.querySelector("[data-ctab=letter]").click(); await w(30);
    log("11.foot", document.querySelector(".letter-foot span").innerText);
    document.querySelector("[data-run=cover-letter]").click(); await w(6000);
    log("11.run", document.getElementById("console").dataset.state + " | " + document.getElementById("conMsg").innerText + " | " + [...document.querySelectorAll("#conLog .warn")].map(x => x.innerText).join(";"));
    document.getElementById("conClose").click();

    // п. 15: уход из настроек
    location.hash = "#settings"; await w(200);
    mw.value = "200"; mw.dispatchEvent(new Event("input", { bubbles: true }));
    location.hash = "#gap"; await w(200);
    log("15.dialogOpen", dlg().open + " hash:" + location.hash + " | " + document.getElementById("dlgTitle").innerText + " focus:" + document.activeElement.value);
    press("stay"); await w(100);
    log("15.stay", dlg().open + " hash:" + location.hash + " screen:" + !document.getElementById("s-settings").hidden);
    location.hash = "#gap"; await w(200); press("discard"); await w(200);
    log("15.discard", "hash:" + location.hash + " maxWords:" + mw.value + " dirty:" + document.getElementById("saveBar").classList.contains("dirty"));
    location.hash = "#settings"; await w(200);
    mw.value = "180"; mw.dispatchEvent(new Event("input", { bubbles: true }));
    location.hash = "#vacancies"; await w(200); press("save"); await w(200);
    log("15.save", "hash:" + location.hash + " saved:" + document.querySelector('[data-s="maxWords"]').defaultValue);
    location.hash = "#settings"; await w(200);
    mw.value = "170"; mw.dispatchEvent(new Event("input", { bubbles: true }));
    location.hash = "#gap"; await w(200);
    dlg().dispatchEvent(new Event("cancel", { cancelable: true })); await w(100);
    log("15.esc", dlg().open + " hash:" + location.hash);
    document.getElementById("resetSettings").click();

    // п. 16: удаление навыка
    document.querySelector("[data-stab=dict]").click(); await w(30);
    document.querySelector('[data-del="BPMN"]').click(); await w(50);
    log("16.dialog", dlg().open + " | " + document.getElementById("dlgText").innerText + " focus:" + document.activeElement.value);
    press("cancel"); await w(50);
    log("16.cancel", "BPMN in dict:" + !!document.querySelector('[data-del="BPMN"]') + " dirty:" + document.getElementById("saveBar").classList.contains("dirty"));
    document.querySelector('[data-del="DFD"]').click(); await w(50);
    log("16.zeroText", document.getElementById("dlgText").innerText);
    press("del"); await w(50);
    log("16.deleted", "DFD:" + !!document.querySelector('[data-del="DFD"]') + " count:" + document.getElementById("dictCount").innerText);
    document.getElementById("resetSettings").click(); await w(30);

    // п. 18: кнопка на мобильном есть в DOM, переключает класс
    const ft = document.getElementById("footToggle"); ft.click();
    log("18.toggle", document.querySelector(".side").classList.contains("show-foot") + " expanded:" + ft.getAttribute("aria-expanded"));
    ft.click();
    // подпись «Навык:» в gap-таблице
    log("gapLabel", document.querySelector("#gapTable tbody td").getAttribute("data-l"));
  } catch (x) { log("EXC", x.message + " " + x.stack); }
  log("errors", document.body.dataset.err || "none");
  document.body.setAttribute("data-r", R.join(" || "));
})();
