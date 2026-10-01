window.onerror = (m, u, l) => { document.body.dataset.err = (document.body.dataset.err || "") + m + "@" + l + ";"; };
const w = ms => new Promise(r => setTimeout(r, ms));
const R = []; const log = (k, v) => R.push(k + "=" + String(v).replace(/\s+/g, " ").slice(0, 300));
const rows = () => [...document.querySelectorAll("#histTable tbody tr")];
(async () => {
  try {
    location.hash = "#vacancies"; await w(200);
    document.querySelector('[data-vtab="hist"]').click(); await w(30);
    log("panes", "hist:" + !document.getElementById("histPane").hidden + " list:" + !document.getElementById("listPane").hidden);
    log("rows", rows().length + " | first: " + rows()[0].innerText + " | last: " + rows()[7].innerText);
    log("buttons", document.querySelectorAll("[data-onlyfile]").length);
    document.querySelector('[data-onlyfile="7"]').click(); await w(300);
    log("analyzeOnly", location.hash + " | " + document.querySelector("#analyzePanel [data-preview]").innerText + " | label: " + document.getElementById("dataOneLabel").innerText);
    document.querySelector("#analyzePanel [data-run]").click(); await w(7000);
    log("analyzeLog1", document.getElementById("conLog").innerText.split("\n")[1]);
    document.getElementById("conClose").click();
    location.hash = "#vacancies"; await w(200);
    document.getElementById("fetchToggle").click(); document.querySelector("#fetchPanel [data-run]").click(); await w(9000);
    document.getElementById("conClose").click();
    document.querySelector('[data-vtab="hist"]').click(); await w(30);
    log("afterFetch", rows().length + " | first: " + rows()[0].innerText + " | count tab: " + document.getElementById("histCount").innerText + " details: " + document.getElementById("detailsCount").innerText);
    log("dataAll", document.getElementById("dataAllCount").innerText);
    document.querySelector('[data-vtab="list"]').click(); await w(30);
    log("backToList", !document.getElementById("listPane").hidden + " rows:" + document.querySelectorAll("#vacTable tbody tr").length);
  } catch (x) { log("EXC", x.message); }
  log("errors", document.body.dataset.err || "none");
  document.body.setAttribute("data-r", R.join(" || "));
})();
