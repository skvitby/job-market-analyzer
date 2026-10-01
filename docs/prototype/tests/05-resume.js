window.onerror = (m, u, l) => { document.body.dataset.err = (document.body.dataset.err || "") + m + "@" + l + ";"; };
const w = ms => new Promise(r => setTimeout(r, ms));
const R = []; const log = (k, v) => R.push(k + "=" + String(v).replace(/\s+/g, " ").slice(0, 160));
const txt = sel => document.querySelector(sel).innerText;
(async () => {
  try {
    // 1. Резюме есть, сравнение устарело
    location.hash = "#resume"; await w(200);
    log("nav", document.querySelector('[data-nav="resume"]').getAttribute("aria-current"));
    log("resume", txt("#s-resume"));
    log("kpiCover", txt("#kpiCover"));
    location.hash = "#gap"; await w(200);
    log("gapNotice", txt("#cvNotice"));
    // 2. Обновить анализ — предупреждение уходит
    document.querySelector("#s-gap [data-open-analyze]").click(); await w(300);
    log("panelOpen", !document.getElementById("analyzePanel").hidden);
    document.querySelector("#analyzePanel [data-run]").click(); await w(7000);
    log("afterAnalyze.state", document.getElementById("console").dataset.state);
    location.hash = "#gap"; await w(200);
    log("gapNotice2", txt("#cvNotice"));
    log("kpiCover2", txt("#kpiCover"));
    document.getElementById("conAction").click();
    // 3. Резюме не найдено
    const nc = document.getElementById("noCvMode"); nc.checked = true; nc.dispatchEvent(new Event("change"));
    await w(50);
    log("gapEmptyShown", !document.getElementById("gapEmpty").hidden + " body hidden " + document.getElementById("gapBody").hidden);
    location.hash = "#overview"; await w(200);
    log("kpiNoCv", txt("#kpiCover") + " / " + txt("#kpiGaps"));
    log("fabricHidden", document.getElementById("fabricWrap").hidden + " notice " + !document.getElementById("fabricNoCv").hidden);
    log("barsStatus", document.querySelector("#bars .row:not(.hrow) .st").innerText + " | note " + txt("#barsNote"));
    // анализ без резюме — предупреждение
    document.getElementById("analyzeToggle").click(); document.querySelector("#analyzePanel [data-run]").click(); await w(7000);
    log("analyzeNoCv", document.getElementById("console").dataset.state + " | " + txt("#conState") + " | " + txt("#conMsg"));
    log("logWarn", [...document.querySelectorAll("#conLog .warn")].map(x => x.innerText).join(";"));
    document.getElementById("conAction").click();
    // письмо без резюме — ошибка
    location.hash = "#vacancy/137828745"; await w(200);
    log("cardMatch", txt(".match"));
    document.querySelector("[data-ctab=letter]").click(); await w(50);
    document.querySelector("[data-run=cover-letter]").click(); await w(3000);
    log("letterNoCv", document.getElementById("console").dataset.state + " | " + txt("#conMsg"));
    log("letterVersionsUnchanged", [...document.querySelectorAll(".ver")].map(b => b.innerText).join(","));
    location.hash = "#resume"; await w(200);
    log("resumeEmpty", txt("#s-resume"));
    // 4. Вернуть резюме
    nc.checked = false; nc.dispatchEvent(new Event("change")); await w(50);
    log("restored", !document.getElementById("gapBody").hidden + " " + txt("#kpiCover"));
    // 5. Выбор файла (имитация)
    const md = "# Резюме\n## Опыт работы\nтекст\n# Навыки\nBPMN\n# О себе\nабзац\n";
    loadCvFile(new File([md], "cv_new.md", { type: "text/markdown" })); await w(300);
    log("loaded", txt("#s-resume"));
    loadCvFile(new File(["x"], "cv.docx")); await w(100);
    log("badFile", txt("#cvErr"));
  } catch (x) { log("EXC", x.message + " " + x.stack); }
  log("errors", document.body.dataset.err || "none");
  document.body.setAttribute("data-r", R.join(" || "));
})();
