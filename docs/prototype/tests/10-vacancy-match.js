window.onerror = (m, u, l) => { document.body.dataset.err = (document.body.dataset.err || "") + m + "@" + l + ";"; };
const w = ms => new Promise(r => setTimeout(r, ms));
const R = []; const log = (k, v) => R.push(k + "=" + String(v).replace(/\s+/g, " ").slice(0, 300));
(async () => {
  try {
    for (const id of ["137828745", "137493556", "137587921", "133960724"]) {
      location.hash = "#vacancy/" + id; await w(200);
      log(id, document.querySelector(".match").innerText + " | cells:" + document.querySelectorAll(".cells i").length);
    }
  } catch (x) { log("EXC", x.message); }
  log("errors", document.body.dataset.err || "none");
  document.body.setAttribute("data-r", R.join(" || "));
})();
