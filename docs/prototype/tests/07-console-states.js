const w = ms => new Promise(r => setTimeout(r, ms));
const R = []; const log = (k, v) => R.push(k + "=" + v);
const vis = id => getComputedStyle(document.getElementById(id)).display !== "none";
const st = () => { const c = document.getElementById("console"); return c.dataset.state + " open:" + c.classList.contains("open") + " action:" + (vis("conAction") ? document.getElementById("conAction").innerText : "-") + " close:" + vis("conClose") + " state:" + document.getElementById("conState").innerText; };
(async () => {
  try {
    document.getElementById("analyzeToggle").click(); document.querySelector("#analyzePanel [data-run]").click(); await w(400);
    log("running", st());
    document.getElementById("conAction").click(); await w(50);
    log("cancelled", st() + " focus:" + document.activeElement.id + " color:" + getComputedStyle(document.getElementById("conState")).color);
    document.getElementById("conClose").click(); log("closedAfterCancel", st());
    document.getElementById("failMode").checked = true;
    document.getElementById("analyzeToggle").click(); document.querySelector("#analyzePanel [data-run]").click(); await w(6000);
    log("error", st());
    document.getElementById("conClose").click(); log("closedAfterError", st());
    document.getElementById("failMode").checked = false;
    document.getElementById("analyzeToggle").click(); document.querySelector("#analyzePanel [data-run]").click(); await w(7000);
    log("done", st());
    document.getElementById("conClose").click(); log("closedAfterDone", st());
    document.getElementById("failMode").checked = true;
    document.getElementById("analyzeToggle").click(); document.querySelector("#analyzePanel [data-run]").click(); await w(6000);
    document.getElementById("failMode").checked = false;
    document.getElementById("conAction").click(); await w(7000);
    log("retryAfterError", st());
  } catch (x) { log("EXC", x.message); }
  document.body.setAttribute("data-r", R.join(" || "));
})();
