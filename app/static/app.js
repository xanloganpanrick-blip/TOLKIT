const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
let token=localStorage.getItem("dbflow_token")||"", user=null, files=[], jobs=[], authMode="login";

function toast(t){const e=$("#toast");e.textContent=t;e.classList.add("show");setTimeout(()=>e.classList.remove("show"),2400)}
function sizeFmt(n){const u=["B","KB","MB","GB","TB"];let i=0,x=Number(n||0);while(x>=1024&&i<u.length-1){x/=1024;i++}return `${x.toFixed(i?1:0)} ${u[i]}`}
function api(path,opts={}){
  const h={...(opts.headers||{})}; if(token)h.Authorization=`Bearer ${token}`;
  if(opts.body && !(opts.body instanceof FormData)){h["Content-Type"]="application/json";opts.body=JSON.stringify(opts.body)}
  return fetch(path,{...opts,headers:h}).then(async r=>{if(r.status===401){logout();throw new Error("Сессия истекла")} const d=await r.json().catch(()=>({})); if(!r.ok)throw new Error(d.detail||"Ошибка");return d})
}
function showAuth(){ $("#auth").classList.remove("hidden"); $("#app").classList.add("hidden") }
function showApp(){ $("#auth").classList.add("hidden"); $("#app").classList.remove("hidden") }
function logout(){token="";user=null;localStorage.removeItem("dbflow_token");showAuth()}
$("#logout").onclick=logout;

$$("[data-auth]").forEach(b=>b.onclick=()=>{authMode=b.dataset.auth;$$("[data-auth]").forEach(x=>x.classList.toggle("active",x===b));$("#authBtn").textContent=authMode==="login"?"Войти":"Создать аккаунт"});
$("#authBtn").onclick=async()=>{try{const d=await api(`/api/auth/${authMode}`,{method:"POST",body:{username:$("#username").value,password:$("#password").value}});token=d.token;localStorage.setItem("dbflow_token",token);await boot()}catch(e){$("#authMsg").textContent=e.message}};

async function boot(){try{user=await api("/api/me");showApp();$("#userBadge").textContent=`@${user.username}`;await refreshAll();setInterval(poll,2500)}catch(e){logout()}}
async function refreshAll(){[files,jobs]=await Promise.all([api("/api/files"),api("/api/jobs")]);render()}
async function poll(){if(!token)return;try{jobs=await api("/api/jobs");renderJobs();renderMetrics()}catch{}}
$("#refresh").onclick=refreshAll;

function switchView(v){$$(".view").forEach(x=>x.classList.add("hidden"));$(`#${v}View`).classList.remove("hidden");$$(".nav").forEach(n=>n.classList.toggle("active",n.dataset.view===v));$("#crumb").textContent=v==="desktop"?"Рабочий стол":v==="files"?"Хранилище":"История задач"}
$$("[data-view]").forEach(b=>b.onclick=()=>switchView(b.dataset.view));

$("#filePicker").onchange=async e=>{if(!e.target.files.length)return;const fd=new FormData();[...e.target.files].forEach(f=>fd.append("files",f));toast("Загрузка…");try{const r=await api("/api/files/upload",{method:"POST",body:fd});toast(`Загружено: ${r.length}`);e.target.value="";await refreshAll()}catch(err){toast(err.message)}};

function render(){renderFiles();renderJobs();renderMetrics()}
function renderMetrics(){ $("#mFiles").textContent=files.length;$("#mJobs").textContent=jobs.length;$("#mDone").textContent=jobs.filter(j=>j.status==="done").length;$("#mSize").textContent=sizeFmt(files.reduce((a,b)=>a+b.size,0)) }
function fileCard(f){return `<div class="file-card" data-name="${(f.name||"").toLowerCase()}"><div class="file-icon">${f.kind==="result"?"◆":"▤"}</div><div class="file-name">${esc(f.name)}</div><div class="file-meta">${sizeFmt(f.size)} · ${f.kind}</div><div class="file-actions"><button onclick="downloadFile(${f.id})">Скачать</button><button onclick="takeFile(${f.id})">Забрать</button><button class="danger" onclick="deleteFile(${f.id})">Удалить</button></div></div>`}
function renderFiles(){const html=files.map(fileCard).join("")||`<div class="file-meta">Пока пусто. Загрузите таблицы.</div>`;$("#filesGrid").innerHTML=html;$("#recentFiles").innerHTML=files.slice(0,6).map(fileCard).join("")||html}
$("#fileSearch").oninput=e=>{$$("#filesGrid .file-card").forEach(c=>c.style.display=c.dataset.name.includes(e.target.value.toLowerCase())?"":"none")};
window.downloadFile=id=>window.open(`/api/files/${id}/download?token=${token}`,"_blank");
window.takeFile=id=>{if(confirm("Скачать и затем удалить файл из облака?"))window.open(`/api/files/${id}/take`,"_blank")};
window.deleteFile=async id=>{if(!confirm("Удалить файл безвозвратно?"))return;try{await api(`/api/files/${id}`,{method:"DELETE"});await refreshAll()}catch(e){toast(e.message)}};

function renderJobs(){const el=$("#jobsList");if(!el)return;el.innerHTML=jobs.map(j=>`<div class="job"><div class="job-op">${opName(j.operation)}</div><div><div class="job-bar"><i style="width:${j.progress}%"></i></div></div><div class="status ${j.status}">${j.status} · ${j.progress}%</div><div>${new Date(j.created_at).toLocaleString()}</div><button onclick="jobDetails(${j.id})">Детали</button></div>`).join("")||`<div class="file-meta">История пока пуста.</div>`}
function opName(x){return ({convert_csv:"В CSV",normalize:"Нормализация",remove7:"Убрать 7",dedupe:"Дедуп",merge:"Объединение",split:"Разделение",stats:"Анализ"})[x]||x}

$$("[data-op]").forEach(b=>b.onclick=()=>openOperation(b.dataset.op));
function openOperation(op){
  if(!files.length){toast("Сначала загрузите файл");switchView("files");return}
  const multi=op==="merge"||op==="convert_csv"||op==="normalize"||op==="dedupe"||op==="remove7"||op==="stats";
  $("#modalBody").innerHTML=`<h2>${opName(op)}</h2><p class="file-meta">Выберите ${multi?"один или несколько файлов":"один файл"}.</p><div class="select-list">${files.map(f=>`<label class="select-row"><input type="${multi?"checkbox":"radio"}" name="src" value="${f.id}"><span>${esc(f.name)}</span><small>${sizeFmt(f.size)}</small></label>`).join("")}</div>${op==="split"?`<div class="field"><label>Строк в части</label><input id="maxRows" value="500000"></div>`:""}<button class="primary" id="runOp">Запустить</button>`;
  $("#modal").classList.remove("hidden");
  $("#runOp").onclick=async()=>{const ids=$$(`input[name="src"]:checked`).map(x=>+x.value);if(!ids.length)return toast("Выберите файлы");let options={};if(op==="split")options.max_rows=+$("#maxRows").value||500000;try{await api("/api/jobs",{method:"POST",body:{operation:op,input_file_ids:ids,options}});$("#modal").classList.add("hidden");toast("Задача запущена");switchView("jobs");await refreshAll()}catch(e){toast(e.message)}}
}
$("#modalClose").onclick=()=>$("#modal").classList.add("hidden");$("#modal").onclick=e=>{if(e.target===$("#modal"))$("#modal").classList.add("hidden")};

window.jobDetails=async id=>{try{const j=await api(`/api/jobs/${id}`);$("#modalBody").innerHTML=`<h2>${opName(j.operation)} #${j.id}</h2><p class="status ${j.status}">${j.status} · ${j.progress}%</p><div class="pre">${esc(j.log||j.error||"Лог пуст")}</div>${j.output_file_ids?.length?`<p>Результаты: ${j.output_file_ids.map(x=>`<button onclick="downloadFile(${x})">Файл #${x}</button>`).join(" ")}</p>`:""}`;$("#modal").classList.remove("hidden")}catch(e){toast(e.message)}}
function esc(s){return String(s??"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[m]))}

if(token)boot(); else showAuth();
