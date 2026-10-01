window.onerror = (m, u, l) => { document.body.dataset.err = (document.body.dataset.err || "") + m + "@" + l + ";"; };
const w = ms => new Promise(r => setTimeout(r, ms));
const R = []; const log = (k, v) => R.push(k + "=" + String(v).replace(/\s+/g, " ").slice(0, 300));
(async () => {
  try {
    location.hash = "#vacancy/137587921"; await w(200);
    document.querySelector("[data-ctab=tips]").click(); await w(50);
    log("laif.versions", [...document.querySelectorAll("[data-tver]")].map(b => b.innerText + ":" + b.getAttribute("aria-pressed")).join(","));
    document.querySelector('[data-tver="2"]').click(); await w(30);
    log("laif.v2", document.querySelector(".versions").innerText);
    location.hash = "#vacancy/137828745"; await w(200);
    document.querySelector("[data-ctab=tips]").click(); await w(50);
    log("kred.flags", [...document.querySelectorAll(".tip .flag, .tip .flag-inline")].map(x => x.innerText).join(" | "));
    log("kred.gaps", [...document.querySelectorAll(".aside.callout li")].map(x => x.innerText).join(" | "));
    log("kred.checklist", [...document.querySelectorAll(".aside .check")].map(x => x.innerText).join(" | "));
    document.querySelector("[data-run=cv-tips]").click(); await w(8000);
    log("run", document.getElementById("console").dataset.state + " | " + document.getElementById("conState").innerText + " | " + document.getElementById("conMsg").innerText);
    log("runLog", [...document.querySelectorAll("#conLog .warn")].map(x => x.innerText).join(" ¦ "));
    log("afterRun", document.querySelector(".versions").innerText);
  } catch (x) { log("EXC", x.message); }
  log("errors", document.body.dataset.err || "none");
  document.body.setAttribute("data-r", R.join(" || "));
})();
