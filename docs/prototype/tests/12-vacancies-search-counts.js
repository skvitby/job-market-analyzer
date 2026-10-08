window.onerror = (m, u, l) => { document.body.dataset.err = (document.body.dataset.err || "") + m + "@" + l + ";"; };
const w = ms => new Promise(r => setTimeout(r, ms));
const R = []; const log = (k, v) => R.push(k + "=" + v);
// Счётчики фильтров учитывают строку поиска: число на кнопке = строк после её нажатия
const pills = () => [...document.querySelectorAll("#vacFilter .pill")].map(b => b.innerText.replace(/\s+/g, " ") + (b.disabled ? "(off)" : "")).join(" / ");
const rows = () => document.querySelectorAll("#vacTable tbody tr:not(:has(.empty))").length;
const found = () => "found='" + document.getElementById("vacFound").textContent + "'";
const search = async q => { const s = document.getElementById("vacSearch"); s.value = q; s.dispatchEvent(new Event("input")); await w(30); };
const pick = async f => { document.querySelector("#vacFilter [data-f=" + f + "]").click(); await w(30); };
(async () => {
  try {
    location.hash = "#vacancies"; await w(200);
    log("start", pills() + " | " + found() + " rows=" + rows());
    await pick("new");
    log("newOnly", pills() + " | " + found() + " rows=" + rows());
    await pick("all");
    for (const q of ["банк", "аналитик", "zzz"]) {
      await search(q);
      const click = []; let match = true;
      for (const f of ["all", "new", "letter", "salary"]) {
        const b = document.querySelector("#vacFilter [data-f=" + f + "]");
        if (b.disabled) { click.push(f + ":off"); continue; }
        await pick(f);
        const n = +b.querySelector("b").innerText, r = rows();
        match = match && n === r; click.push(f + ":" + n + "→" + r);
      }
      await pick("all");
      log(q, pills() + " | " + found() + " | " + click.join(" ") + " | match=" + match);
    }
    await search(""); await pick("new"); await search("zzz");
    log("selectedZero", pills() + " | " + found() + " | " + document.querySelector("#vacTable tbody").innerText.trim());
    await search(""); await pick("all");
    log("reset", pills() + " | " + found() + " rows=" + rows());
  } catch (x) { log("EXC", x.message); }
  log("errors", document.body.dataset.err || "none");
  document.body.setAttribute("data-r", R.join(" || "));
})();
