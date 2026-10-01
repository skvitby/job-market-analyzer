window.onerror=(m,u,l)=>{document.title="ERR "+m+" @"+l};
const w=(ms)=>new Promise(r=>setTimeout(r,ms));const R=[];const log=(k,v)=>R.push(k+"="+v);
const body=()=>document.getElementById("cardBody").innerText.slice(0,60).replace(/\n/g," | ");
(async()=>{
// баги 1-2: письмо без советов
location.hash="#vacancy/137173591";await w(200);
document.querySelector("[data-ctab=letter]").click();await w(50);document.querySelector("[data-run=cover-letter]").click();await w(6000);
log("B1.letterTab",body());
document.querySelector("[data-ctab=tips]").click();await w(50);log("B1.tipsTab",body());
log("B1.tabs",[...document.querySelectorAll("[data-ctab]")].map(b=>b.innerText).join(" / "));
// советы без письма
location.hash="#vacancy/137354708";await w(200);
document.querySelector("[data-ctab=tips]").click();await w(50);document.querySelector("[data-run=cv-tips]").click();await w(7000);
document.querySelector("[data-ctab=letter]").click();await w(50);log("B1.letterAfterTips",body());
location.hash="#vacancies";await w(200);
const rows=[...document.querySelectorAll("#vacTable tbody tr")].slice(0,2).map(r=>r.lastElementChild.innerText);log("B2.col",rows.join(" / "));
document.querySelector('#vacFilter [data-f=letter]').click();await w(50);log("B2.letterFilter",document.querySelectorAll("#vacTable tbody tr").length);
// баг 3: сохранить -> изменить -> отменить
location.hash="#settings";await w(200);
let inp=document.querySelector("[data-list=roles] .chip-add");inp.value="Product Analyst";inp.dispatchEvent(new KeyboardEvent("keydown",{key:"Enter"}));
const days=document.querySelector('[data-pane=search] input[type=number][value="14"]');days.value="21";days.dispatchEvent(new Event("input",{bubbles:true}));
document.querySelectorAll('[data-pane=letter] .seg-ctl button')[2].click();
document.getElementById("saveSettings").click();await w(50);
inp=document.querySelector("[data-list=roles] .chip-add");inp.value="Data Analyst";inp.dispatchEvent(new KeyboardEvent("keydown",{key:"Enter"}));
days.value="30";days.dispatchEvent(new Event("input",{bubbles:true}));
document.querySelectorAll('[data-pane=letter] .seg-ctl button')[0].click();
document.getElementById("resetSettings").click();await w(50);
const roles=[...document.querySelectorAll("[data-list=roles] .tchip")].map(c=>c.firstChild.textContent);
log("B3.roles",roles.length+" hasProduct="+roles.includes("Product Analyst")+" hasData="+roles.includes("Data Analyst"));
log("B3.days",document.querySelector('[data-pane=search] .input.num').value);
log("B3.lang",[...document.querySelectorAll('[data-pane=letter] .seg-ctl button')].find(b=>b.getAttribute("aria-pressed")==="true").innerText);
// баг 4: ошибка письма -> уйти с экрана -> повторить
document.getElementById("failMode").checked=true;
location.hash="#vacancy/137140562";await w(200);
document.querySelector("[data-ctab=letter]").click();await w(50);document.querySelector("[data-run=cover-letter]").click();await w(5000);
log("B4.state",document.getElementById("console").dataset.state);
document.getElementById("failMode").checked=false;
location.hash="#overview";await w(200);
document.getElementById("conAction").click();await w(6000);
log("B4.cmdLine",document.querySelector("#conLog span").innerText);
log("B4.saved",document.getElementById("conMsg").innerText);
location.hash="#vacancy/137140562";await w(200);document.querySelector("[data-ctab=letter]").click();await w(50);log("B4.card",body());
document.body.setAttribute("data-r",R.join(" || "));})();
