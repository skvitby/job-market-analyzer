window.onerror=(m,u,l)=>{document.body.dataset.err=(document.body.dataset.err||"")+m+"@"+l+";"};
const w=(ms)=>new Promise(r=>setTimeout(r,ms));const R=[];const log=(k,v)=>R.push(k+"="+v);
const key=(el,k)=>el.dispatchEvent(new KeyboardEvent("keydown",{key:k,bubbles:true}));
(async()=>{
// 2. полоса покрытия
const segs=[...document.querySelectorAll("#fabric .seg")];
log("2.tabStops",document.querySelectorAll("#s-overview [tabindex='0']").length);
segs[0].focus();key(segs[0],"ArrowRight");log("2.arrowRight",document.activeElement===segs[1]&&segs[1].tabIndex===0&&segs[0].tabIndex===-1);
key(document.activeElement,"End");log("2.end",document.activeElement===segs[segs.length-1]);
key(document.activeElement,"ArrowRight");log("2.endClamp",document.activeElement===segs[segs.length-1]);
key(document.activeElement,"Home");log("2.home",document.activeElement===segs[0]);
log("2.readout",document.getElementById("fabricReadout").innerText);
// 1. кнопки во время выполнения
location.hash="#vacancy/137828745";await w(200);document.querySelector("[data-ctab=letter]").click();await w(50);
document.querySelector("[data-run=cover-letter]").click();await w(300);
location.hash="#vacancy/137480923";await w(200);document.querySelector("[data-ctab=letter]").click();await w(50);
log("1.disabledDuringRun",document.querySelector("#cardBody [data-run]").disabled);
document.querySelector("[data-ctab=tips]").click();await w(50);log("1.tipsBtnDisabled",document.querySelector("#cardBody [data-run]").disabled);
startCommand("cv-tips");await w(50);log("1.toast",document.getElementById("toast").innerText);
await w(6000);log("1.enabledAfterRun",!document.querySelector("#cardBody [data-run]").disabled);
// 4. служебные поля при сохранении
location.hash="#settings";await w(200);document.querySelector("[data-stab=dict]").click();
document.getElementById("newSkill").value="черновик";document.getElementById("dictSearch").value="SQL";
const ca=document.querySelector("[data-list=excludedSkills] .chip-add");ca.value="набираю";
const days=document.querySelector('[data-pane=search] .input.num');days.value="20";days.dispatchEvent(new Event("input",{bubbles:true}));
document.getElementById("saveSettings").click();await w(50);
days.value="25";days.dispatchEvent(new Event("input",{bubbles:true}));
document.getElementById("resetSettings").click();await w(50);
log("4.newSkill","'"+document.getElementById("newSkill").value+"'");
log("4.dictSearch","'"+document.getElementById("dictSearch").value+"'");
log("4.days",days.value);
log("errors",document.body.dataset.err||"none");
document.body.setAttribute("data-r",R.join(" || "));})();
