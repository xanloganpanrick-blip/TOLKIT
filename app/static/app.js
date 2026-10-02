const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
const accept=".csv,.xlsx,.xlsm,.xls,.xlsb,.parquet,.txt,.html,.htm";
const allowed=new Set(accept.split(","));
const tools={
 phones:{name:"База номеров",icon:"↗",group:"Toolkit",desc:"Извлечение номеров из таблиц. Один номер в строке, без дублей.",output:"TXT"},
 geo:{name:"GEO — очистка",icon:"◎",group:"Toolkit",desc:"Определение номеров, разворот нескольких телефонов в отдельные строки, ФИО и даты."},
 regions:{name:"Регионы по номерам",icon:"⌖",group:"Toolkit",desc:"Регион выделения номера по диапазонам справочника.",ref:"Справочник диапазонов",refHint:"CSV: Код; От; До; Регион. Можно загрузить выписку реестра нумерации. Мобильный код сам по себе не определяет регион."},
 merge:{name:"Объединить таблицы",icon:"⊕",group:"Toolkit",desc:"Общие колонки, все листы и номер исходной таблицы. Поддерживаются также HTML-таблицы."},
 group_split:{name:"Разбить по таблицам",icon:"⊞",group:"Toolkit",desc:"Группировка по «№ таблицы», GEO-очистка и общий numbers.txt."},
 max_filter:{name:"Фильтр + MAX",icon:"◇",group:"Toolkit",desc:"Сверка номеров с твоим списком. 7, 8 и десятизначный номер сравниваются одинаково.",ref:"Номера для сверки",refHint:"TXT: номер; значение MAX. Также CSV или Excel с колонками «Номер» и «MAX». Сверка работает по загруженному списку."},
 row_names:{name:"Строки в имя",icon:"#",group:"Toolkit",desc:"Количество строк первого листа в имени файла. Содержимое сохраняется.",output:"Исходный"},
 fssp:{name:"ФССП — заголовок",icon:"≡",group:"Toolkit",desc:"Найти «Номер» в первых 20 строках и заменить на «Номер телефона». При выводе в XLSX сохраняются листы и оформление."},
 distribute:{name:"Делёжка",icon:"↔",group:"Toolkit",desc:"Строки по кругу в заданное количество частей. Дедупликация по номеру и исходный индекс строки."},
 take:{name:"Взять кусок",icon:"◩",group:"Toolkit",desc:"Строки из разных мест таблицы. Несколько файлов набираются по кругу; остаток сохраняется отдельно."},
 remove7:{name:"Удалить первую 7",icon:"−7",group:"Toolkit",desc:"Убрать только начальную 7 в выбранной колонке. Другие цифры сохраняются."},
 addresses:{name:"Формат адресов",icon:"⌂",group:"Подготовка",desc:"Город, улица, дом, корпус, строение, литера и квартира в едином порядке. Неполные и неоднозначные адреса отмечаются для проверки."},
 convert_csv:{name:"Конвертировать в CSV",icon:"⇄",group:"Подготовка",desc:"Excel, Parquet, HTML и CSV в единый CSV. Текстовые номера и ведущие нули сохраняются.",output:"CSV"},
 clean:{name:"Очистить данные",icon:"⌁",group:"Подготовка",desc:"Лишние пробелы и пустые текстовые значения."},
 normalize:{name:"Нормализовать",icon:"✦",group:"Подготовка",desc:"Единый вид телефонов, ФИО и дат."},
 dedupe:{name:"Удалить дубли",icon:"◈",group:"Подготовка",desc:"Уникальные строки целиком или по выбранным колонкам."},
 filter:{name:"Фильтр строк",icon:"⌘",group:"Подготовка",desc:"Точное значение или вхождение текста в колонке."},
 sort:{name:"Сортировка",icon:"↕",group:"Подготовка",desc:"По возрастанию или убыванию значения колонки."},
 split:{name:"Разбить по строкам",icon:"✂",group:"Подготовка",desc:"Последовательные части заданного размера, отдельные файлы и общий ZIP."},
 stats:{name:"Профиль данных",icon:"▦",group:"Подготовка",desc:"Количество строк, колонок и список полей.",output:"JSON"}
};
const defaults={output_format:"csv",csv_separator:",",matches_only:true,remove7:false,download_unmatched:false,
 region_zip:true,format_addresses:false,exclude_names:false,dedupe:true,parts:2,count:1000,max_rows:500000,
 phone_column:"",address_column:"",column:"",value:"",group_column:"№ таблицы",keys:"",contains:false,descending:false,replace_century:false};
const states=new Map();
let route="desktop",jobs=[],dragDepth=0,polling=false;
const sid=(()=>{try{let value=localStorage.getItem("dbflow_session");if(!value){value=crypto.randomUUID();localStorage.setItem("dbflow_session",value)}return value}catch{return crypto.randomUUID()}})();
const esc=v=>String(v??"").replace(/[&<>"']/g,x=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[x]));
function state(key=route){if(!states.has(key))states.set(key,{files:[],refs:[],options:{...defaults},busy:false,upload:0,error:"",jobId:null});return states.get(key)}
function size(n){const units=["B","KB","MB","GB"];let i=0;while(n>=1024&&i<3){n/=1024;i++}return n.toFixed(i?1:0)+" "+units[i]}
function toast(message){$("#toast").textContent=message;$("#toast").classList.add("show");clearTimeout(toast.timer);toast.timer=setTimeout(()=>$("#toast").classList.remove("show"),4000)}
async function api(path){const r=await fetch(path,{headers:{"X-Session-ID":sid}});const d=await r.json();if(!r.ok)throw Error(typeof d.detail==="string"?d.detail:"Ошибка запроса");return d}
function navLink(key,label,icon){return '<a class="nav" data-route="'+key+'" href="#/'+key+'"><span>'+icon+'</span>'+label+'</a>'}
$("#navigation").innerHTML=navLink("desktop","Рабочий стол","⌂")+navLink("jobs","История задач","↻")+
 ["Toolkit","Подготовка"].map(group=>'<div class="side-label nav-group">'+group+'</div>'+Object.entries(tools).filter(([,v])=>v.group===group).map(([key,v])=>navLink(key,v.name,v.icon)).join("")).join("");
$("#toolCards").innerHTML=Object.entries(tools).map(([key,v])=>'<a class="operation-card" href="#/'+key+'" data-tool="'+key+'"><span class="op-icon">'+v.icon+'</span><span class="op-kicker">'+v.group+'</span><h3>'+v.name+'</h3><p>'+v.desc+'</p><span class="op-arrow">↗</span></a>').join("");
function field(key,label,type="text",value="",hint=""){
 const s=state().options,v=s[key]??value;
 return '<label class="field"><span>'+label+'</span><input data-option="'+key+'" type="'+type+'" value="'+esc(v)+'" '+(type==="number"?'min="1" step="1"':"")+'>'+(hint?'<small>'+hint+'</small>':"")+'</label>';
}
function checkbox(key,label,hint=""){return '<label class="check"><input type="checkbox" data-option="'+key+'" '+(state().options[key]?"checked":"")+'><span>'+label+(hint?'<small>'+hint+'</small>':"")+'</span></label>'}
function select(key,label,choices){const value=state().options[key];return '<label class="field"><span>'+label+'</span><select data-option="'+key+'">'+choices.map(([v,t])=>'<option value="'+esc(v)+'" '+(String(value)===String(v)?"selected":"")+'>'+t+'</option>').join("")+'</select></label>'}
function settings(key){
 let html="";
 if(["phones","geo","regions","max_filter","remove7","distribute"].includes(key))html+=field("phone_column","Колонка номера","text","","Пусто — определить автоматически");
 if(key==="geo"){html+=checkbox("format_addresses","Форматировать адреса")+checkbox("exclude_names","Исключить ФИО с «оглы» / «кызы»");}
 if(key==="max_filter"){
  html+=checkbox("remove7","Убрать 7")+checkbox("matches_only","Отфильтровать совпадения","Оставить совпадения, убрать все несовпадения.")+checkbox("download_unmatched","Скачать несовпадения")+checkbox("dedupe","Удалить дубли по номеру")+checkbox("format_addresses","Форматировать адреса")+checkbox("replace_century","Заменить годы 20xx → 19xx");
 }
 if(key==="addresses")html+=field("address_column","Колонка адреса","text","","Пусто — найти «Адрес», «Адресс» или address");
 if(key==="regions")html+=checkbox("region_zip","Скачать отдельные таблицы по регионам","Определяется регион выделения номера, а не местонахождение человека.");
 if(key==="group_split")html+=field("group_column","Колонка группировки")+checkbox("format_addresses","Форматировать адреса")+checkbox("exclude_names","Исключить ФИО с «оглы» / «кызы»");
 if(key==="distribute")html+=field("parts","Количество частей","number")+checkbox("dedupe","Удалить дубли по номеру");
 if(key==="take")html+=field("count","Сколько строк взять","number");
 if(key==="split")html+=field("max_rows","Строк в каждой части","number");
 if(key==="dedupe")html+=field("keys","Колонки через запятую","text","","Пусто — сравнивать строки целиком");
 if(["filter","sort"].includes(key))html+=field("column","Название колонки");
 if(key==="filter")html+=field("value","Значение")+checkbox("contains","Содержит текст");
 if(key==="sort")html+=checkbox("descending","По убыванию");
 if(!tools[key].output)html+=select("output_format","Результат",[["csv","CSV"],["xlsx","XLSX"]]);
 if(!["phones","row_names","stats"].includes(key))html+=select("csv_separator","Разделитель CSV",[[",","Запятая"],[";","Точка с запятой"],["\t","Табуляция"],["|","Вертикальная черта"]]);
 return html||'<p class="file-meta">Настройки не требуются. Результат: '+tools[key].output+'.</p>';
}
function dropZone(ref=false){return '<div class="drop-zone" data-drop="'+(ref?"refs":"files")+'" tabindex="0" role="button" aria-label="'+(ref?"Выбрать справочник":"Выбрать файлы")+'"><div class="drop-icon">'+(ref?"◇":"↥")+'</div><strong>'+(ref?"Добавь справочник":"Перетащи файлы сюда")+'</strong><span>или выбери на компьютере</span><small>CSV · XLSX · XLSM · XLS · XLSB · Parquet · TXT · HTML</small><input class="picker" data-picker="'+(ref?"refs":"files")+'" type="file" multiple accept="'+accept+'" hidden></div><div data-file-list="'+(ref?"refs":"files")+'"></div>'}
function renderTool(){
 const meta=tools[route],s=state();
 $("#toolView").innerHTML='<div class="page-intro tool-intro"><div><span class="eyebrow">'+meta.group+' / '+meta.icon+'</span><h1>'+meta.name+'</h1><p>'+meta.desc+'</p></div><a class="text-btn" href="#/desktop">Все разделы ↗</a></div><div class="tool-layout"><div><section class="upload-panel"><div class="panel-heading"><h2>Исходные файлы</h2><button class="text-btn" data-clear>Очистить список</button></div>'+dropZone()+'</section>'+(meta.ref?'<section class="upload-panel reference-panel"><div class="panel-heading"><h2>'+meta.ref+'</h2></div><p class="file-meta">'+meta.refHint+'</p>'+dropZone(true)+'</section>':"")+'</div><aside class="settings-panel"><span class="eyebrow">ПАРАМЕТРЫ</span><h2>Настройки</h2><div class="settings-fields">'+settings(route)+'</div><button id="runTool" class="primary" data-run>Обработать →</button><div id="toolUpload" class="upload-progress hidden"><div class="progress-label"><span>Загрузка</span><b id="toolPercent">0%</b></div><div class="progress-track"><i id="toolBar"></i></div></div><p id="toolError" class="inline-error" role="alert"></p></aside></div><div class="section-head"><h2>Задачи раздела</h2></div><div id="toolJobs" class="jobs-list"></div>';
 renderFiles();renderJobs();
}
function renderFiles(){
 if(!tools[route])return;
 const s=state();
 for(const kind of ["files","refs"]){
  const container=$('[data-file-list="'+kind+'"]');if(!container)continue;
  container.innerHTML=s[kind].map((file,i)=>'<div class="pending-row"><span class="pending-file-icon">▤</span><span class="filename">'+esc(file.name)+'</span><small>'+size(file.size)+'</small><button aria-label="Убрать '+esc(file.name)+'" data-remove="'+i+'" data-kind="'+kind+'" '+(s.busy?"disabled":"")+'>×</button></div>').join("");
 }
 $("#runTool").disabled=s.busy||!s.files.length;
 $("#runTool").textContent=s.busy?"Загрузка…":"Обработать →";
 $("#toolUpload").classList.toggle("hidden",!s.busy);
 $("#toolPercent").textContent=s.upload+"%";$("#toolBar").style.width=s.upload+"%";
 $("#toolError").textContent=s.error;
}
function addFiles(list,kind="files",key=route){
 if(!tools[key]){key="geo";location.hash="#/geo"}
 const s=state(key);if(s.busy)return toast("Дождись окончания загрузки");
 let bad=0;
 for(const file of list){
  const ext=(file.name.match(/\.[^.]+$/)||[""])[0].toLowerCase();
  if(!allowed.has(ext)){bad++;continue}
  if(!s[kind].some(x=>x.name===file.name&&x.size===file.size&&x.lastModified===file.lastModified))s[kind].push(file);
 }
 s.error="";if(bad)toast("Пропущены неподдерживаемые файлы: "+bad);
 renderFiles();
}
function routePage(){
 const key=decodeURIComponent(location.hash.replace(/^#\/?/,"").split("?")[0]||"desktop");
 route=tools[key]||["desktop","jobs"].includes(key)?key:"desktop";
 $("#desktopView").classList.toggle("hidden",route!=="desktop");
 $("#jobsView").classList.toggle("hidden",route!=="jobs");
 $("#toolView").classList.toggle("hidden",!tools[route]);
 $("#crumb").textContent=tools[route]?.name||(route==="jobs"?"История задач":"Рабочий стол");
 document.title=($("#crumb").textContent)+" — DBFLOW";
 $$("[data-route]").forEach(a=>{a.classList.toggle("active",a.dataset.route===route);a.setAttribute("aria-current",a.dataset.route===route?"page":"false")});
 if(tools[route])renderTool();else renderJobs();
 window.scrollTo(0,0);
}
function upload(key,form){return new Promise((resolve,reject)=>{
 const xhr=new XMLHttpRequest();xhr.open("POST","/api/quick/jobs");xhr.setRequestHeader("X-Session-ID",sid);
 xhr.upload.onprogress=e=>{if(e.lengthComputable){state(key).upload=Math.round(e.loaded/e.total*100);if(route===key)renderFiles()}};
 xhr.onload=()=>{let d;try{d=JSON.parse(xhr.responseText)}catch{return reject(Error("Сервер вернул некорректный ответ"))}if(xhr.status<200||xhr.status>=300)reject(Error(typeof d.detail==="string"?d.detail:"Ошибка загрузки"));else resolve(d)};
 xhr.onerror=()=>reject(Error("Связь прервана. Файлы остались в списке — можно повторить."));xhr.send(form);
 })}
async function run(){
 const key=route,s=state(key);if(s.busy||!s.files.length)return;
 const opts={...s.options};if(key==="dedupe")opts.keys=opts.keys.split(",").map(x=>x.trim()).filter(Boolean);
 if((key==="regions"||(key==="max_filter"&&(opts.matches_only||opts.download_unmatched)))&&!s.refs.length){s.error="Добавь справочник для сверки";renderFiles();return}
 if(["filter","sort"].includes(key)&&!opts.column.trim()){s.error="Укажи название колонки";renderFiles();return}
 if(key==="filter"&&!opts.value){s.error="Укажи значение фильтра";renderFiles();return}
 s.busy=true;s.error="";s.upload=0;renderFiles();
 const form=new FormData();form.append("operation",key);form.append("options_json",JSON.stringify(opts));
 s.files.forEach(f=>form.append("files",f,f.name));s.refs.forEach(f=>form.append("references",f,f.name));
 try{const result=await upload(key,form);s.jobId=result.id;toast("Задача №"+result.id+" запущена");await refreshJobs()}
 catch(e){s.error=e.message}
 finally{s.busy=false;if(route===key)renderFiles()}
}
function statusName(status){return ({queued:"В очереди",running:"Обработка",done:"Готово",error:"Ошибка"})[status]||status}
function resultMarkup(job){
 return (job.outputs||[]).map(out=>'<div class="result-file"><span>▤</span><div><b>'+esc(out.name)+'</b><small>'+size(out.size)+(out.expired?" · срок истёк":"")+'</small></div><button class="secondary" data-download="'+out.id+'" data-job="'+job.id+'" '+(out.expired?"disabled":"")+'>Скачать ↓</button></div>').join("");
}
function jobMarkup(job){
 const s=tools[job.operation],expanded=state(job.operation).jobId===job.id;
 return '<article class="job-card" id="job-'+job.id+'"><div class="job-summary"><span class="job-symbol">'+esc(s?.icon||"▤")+'</span><div class="job-main"><b>'+esc(s?.name||job.operation)+' <small>№'+job.id+'</small></b><span>'+esc((job.source_names||[]).join(", "))+'</span></div><span class="status '+esc(job.status)+'">'+statusName(job.status)+'</span><span class="job-date">'+new Date(job.created_at).toLocaleString("ru")+'</span></div><div class="job-bar"><i style="width:'+Math.max(0,Math.min(100,job.progress))+'%"></i></div>'+(job.error?'<p class="inline-error">'+esc(job.error)+'</p>':"")+((job.outputs||[]).length?'<details '+(expanded?"open":"")+'><summary>Результаты ('+job.outputs.length+')</summary><div class="result-list">'+resultMarkup(job)+'</div></details>':job.status==="done"?'<p class="file-meta">Обработка завершена.</p>':'<p class="file-meta">'+job.progress+'%</p>')+'</article>';
}
function renderJobs(){
 const list=$("#jobsList");if(list)list.innerHTML=jobs.map(jobMarkup).join("")||'<div class="empty-state"><span>↻</span><h3>Задач пока нет</h3><p>Открой нужный раздел и добавь файлы.</p><a href="#/geo" class="text-btn">Начать с GEO →</a></div>';
 const toolJobs=$("#toolJobs");if(toolJobs&&tools[route]){const own=jobs.filter(j=>j.operation===route);toolJobs.innerHTML=own.map(jobMarkup).join("")||'<div class="empty-state"><span>'+tools[route].icon+'</span><p>Здесь появятся результаты этого раздела.</p></div>'}
}
async function refreshJobs(){if(polling)return;polling=true;try{jobs=await api("/api/jobs");renderJobs()}catch(e){toast(e.message)}finally{polling=false}}
async function download(id,jobId,button){
 const out=jobs.find(j=>j.id===jobId)?.outputs.find(f=>f.id===id);if(!out)return;
 button.disabled=true;
 try{const r=await fetch("/api/files/"+id+"/download",{headers:{"X-Session-ID":sid}});if(!r.ok)throw Error("Файл больше недоступен");const url=URL.createObjectURL(await r.blob());const a=document.createElement("a");a.href=url;a.download=out.name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),5000)}
 catch(e){toast(e.message)}finally{button.disabled=false}
}
document.addEventListener("click",event=>{
 const target=event.target.closest("button,[data-drop]");if(!target)return;
 if(target.hasAttribute("data-run"))run();
 if(target.hasAttribute("data-clear")){const s=state();if(!s.busy){s.files=[];s.error="";renderFiles()}}
 if(target.hasAttribute("data-remove")){const s=state();if(!s.busy){s[target.dataset.kind].splice(+target.dataset.remove,1);renderFiles()}}
 if(target.hasAttribute("data-download"))download(+target.dataset.download,+target.dataset.job,target);
 if(target.hasAttribute("data-drop")&&!event.target.matches("input"))target.querySelector("input").click();
});
document.addEventListener("change",event=>{
 if(event.target.matches("[data-option]")){const input=event.target;state().options[input.dataset.option]=input.type==="checkbox"?input.checked:input.type==="number"?Number(input.value):input.value}
 if(event.target.matches("[data-picker]")){addFiles(event.target.files,event.target.dataset.picker);event.target.value=""}
});
document.addEventListener("keydown",event=>{
 if(event.target.matches("[data-drop]")&&["Enter"," "].includes(event.key)){event.preventDefault();event.target.querySelector("input").click()}
});
$("#addFiles").onclick=()=>$("#globalPicker").click();
$("#globalPicker").onchange=e=>{addFiles(e.target.files);e.target.value=""};
$("#refresh").onclick=refreshJobs;
document.addEventListener("dragenter",event=>{if([...event.dataTransfer?.types||[]].includes("Files")){dragDepth++;$("#dragOverlay").classList.remove("hidden")}});
document.addEventListener("dragover",event=>{if([...event.dataTransfer?.types||[]].includes("Files")){event.preventDefault();event.target.closest("[data-drop]")?.classList.add("dragging")}});
document.addEventListener("dragleave",event=>{event.target.closest("[data-drop]")?.classList.remove("dragging");if([...event.dataTransfer?.types||[]].includes("Files")&&--dragDepth<=0){dragDepth=0;$("#dragOverlay").classList.add("hidden")}});
document.addEventListener("drop",event=>{if(![...event.dataTransfer?.types||[]].includes("Files"))return;event.preventDefault();dragDepth=0;$("#dragOverlay").classList.add("hidden");$$(".dragging").forEach(x=>x.classList.remove("dragging"));const zone=event.target.closest("[data-drop]"),card=event.target.closest("[data-tool],[data-route]"),key=card?.dataset.tool||card?.dataset.route||route;addFiles(event.dataTransfer.files,zone?.dataset.drop||"files",tools[key]?key:route);if(tools[key]&&key!==route)location.hash="#/"+key});
window.addEventListener("hashchange",routePage);
window.addEventListener("focus",refreshJobs);
routePage();refreshJobs();
setInterval(()=>{if(!document.hidden&&jobs.some(j=>j.status==="queued"||j.status==="running"))refreshJobs()},3000);
