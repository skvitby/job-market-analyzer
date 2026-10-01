window.onerror = (m, u, l) => { document.body.dataset.err = (document.body.dataset.err || "") + m + "@" + l + ";"; };
const w = ms => new Promise(r => setTimeout(r, ms));
const R = []; const log = (k, v) => R.push(k + "=" + v);
(async () => {
  try {
    location.hash = "#vacancies"; await w(200);
    log("rows", document.querySelectorAll("#vacTable tbody tr").length);
    log("pills", [...document.querySelectorAll("#vacFilter .pill")].map(b => b.innerText.replace(/\s+/g, " ")).join(" / "));
    for (const f of ["new", "letter", "salary"]) {
      document.querySelector("#vacFilter [data-f=" + f + "]").click(); await w(30);
      log(f, [...document.querySelectorAll("#vacTable tbody tr")].map(r => r.children[1].innerText + ":" + r.children[3].innerText + ":" + r.lastElementChild.innerText).join(" | "));
    }
    document.querySelector("#vacFilter [data-f=all]").click();
    location.hash = "#vacancy/137493556"; await w(200);
    document.querySelector("[data-ctab=letter]").click(); await w(50);
    log("itransition", document.querySelector("#h-v").innerText + " | " + [...document.querySelectorAll(".ver")].map(b => b.innerText).join(","));
    location.hash = "#vacancy/137828745"; await w(200);
    document.querySelector("[data-ctab=letter]").click(); await w(50);
    log("kredVers", [...document.querySelectorAll(".ver")].map(b => b.innerText).join(",") + " | " + document.getElementById("letterText").innerText.slice(0, 50));
    location.hash = "#vacancy/137587921"; await w(200);
    document.querySelector("[data-ctab=tips]").click(); await w(50);
    log("laifTips", document.querySelector(".versions").innerText.replace(/\s+/g, " "));
  } catch (x) { log("EXC", x.message); }
  log("errors", document.body.dataset.err || "none");
  document.body.setAttribute("data-r", R.join(" || "));
})();
