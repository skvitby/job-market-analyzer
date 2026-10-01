window.onerror=(m,u,l)=>{document.body.dataset.err=(document.body.dataset.err||"")+m+"@"+l+";"};
const w=(ms)=>new Promise(r=>setTimeout(r,ms));const R=[];const log=(k,v)=>R.push(k+"="+v);
const body=()=>document.getElementById("cardBody").innerText.slice(0,50).replace(/\n/g," | ");
const ent=(el,v)=>{el.value=v;el.dispatchEvent(new KeyboardEvent("keydown",{key:"Enter"}))};
(async()=>{
// общая проверка: дубли id, число Tab-остановок
const ids=[...document.querySelectorAll("[id]")].map(e=>e.id);log("dupIds",ids.filter((x,i)=>ids.indexOf(x)!==i).join(",")||"none");
log("tabStopsOverview",[...document.querySelectorAll("#s-overview [tabindex='0']")].length);
// B1 rich: Кредитон советы v2, переключение версий письма
location.hash="#vacancy/137828745";await w(200);
document.querySelector("[data-ctab=tips]").click();await w(50);document.querySelector("[data-run=cv-tips]").click();await w(7000);
log("B1.richTips",body());
document.querySelector("[data-ctab=letter]").click();await w(50);document.querySelector("[data-ver='1']").click();await w(50);
log("B1.ver1",[...document.querySelectorAll(".ver")].map(b=>b.innerText+":"+b.getAttribute("aria-pressed")).join(","));
// B4: во время генерации уйти на другую карточку — кнопка там активна?
document.querySelector("[data-run=cover-letter]").click();await w(300);
location.hash="#vacancy/137480923";await w(200);document.querySelector("[data-ctab=letter]").click();await w(50);
const gb=document.querySelector("#cardBody [data-run]");log("B4.btnDuringRun.disabled",gb.disabled);
gb.click();await w(50);log("B4.clickDuringRun.cmdLine",document.querySelector("#conLog span").innerText);
await w(6000);log("B4.otherCardAfter",body());
location.hash="#vacancy/137828745";await w(200);document.querySelector("[data-ctab=letter]").click();await w(50);
log("B4.richVersions",[...document.querySelectorAll(".ver")].map(b=>b.innerText).join(","));
// B2: фильтр «С письмом»
location.hash="#vacancies";await w(200);document.querySelector('#vacFilter [data-f=letter]').click();await w(50);
log("B2.filterRows",document.querySelectorAll("#vacTable tbody tr").length+" label="+document.querySelector('#vacFilter [data-f=letter]').innerText.replace(/\s+/g," "));
// B3: словарь + провайдер + повторное сохранение
location.hash="#settings";await w(200);document.querySelector("[data-stab=dict]").click();
document.getElementById("newSkill").value="Camunda";document.getElementById("addSkill").click();
document.querySelector("[data-del='BPMN']").click();
const prov=document.querySelectorAll("[data-provider]")[0];prov.value="ollama";prov.dispatchEvent(new Event("change",{bubbles:true}));
document.getElementById("saveSettings").click();await w(50);
log("B3.resetDisabledAfterSave",document.getElementById("resetSettings").disabled);
document.querySelector("[data-del='Camunda']").click();
document.getElementById("dictSearch").value="xyz";document.getElementById("newSkill").value="черновик";
prov.value="qwen";prov.dispatchEvent(new Event("change",{bubbles:true}));
document.getElementById("resetSettings").click();await w(50);
const names=[...document.querySelectorAll("#dictTable tbody tr td:first-child")].map(td=>td.innerText);
log("B3.dict","Camunda="+names.includes("Camunda")+" BPMN="+names.includes("BPMN")+" count="+document.getElementById("dictCount").innerText);
log("B3.provider",document.querySelectorAll("[data-provider]")[0].value+" model="+document.querySelectorAll("[data-model]")[0].value);
log("B3.newSkillField","'"+document.getElementById("newSkill").value+"'");
log("B3.dirtyAfterReset",document.getElementById("saveBar").classList.contains("dirty"));
// второе сохранение без изменений
document.getElementById("saveSettings").click();log("B3.saveDisabled",document.getElementById("saveSettings").disabled);
log("errors",document.body.dataset.err||"none");
document.body.setAttribute("data-r",R.join(" || "));})();
