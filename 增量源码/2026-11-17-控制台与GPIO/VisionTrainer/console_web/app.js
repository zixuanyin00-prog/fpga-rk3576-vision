'use strict';
const $=id=>document.getElementById(id), NS='http://www.w3.org/2000/svg', MAX_ROI_PIXELS=518400;
const page=crypto.randomUUID(), clone=v=>structuredClone(v), esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let token='', flowId=new URLSearchParams(location.search).get('flow'), state=null, step=1, frameId=null, draft=[], dirty=false, selectedBox=null, undo=[], frameRevision=-1;
let boardAutoConnect=new URLSearchParams(location.search).get('connect')!=='manual';
let helperChoice='', runId='', testId='', liveMeta=null, refreshPromise=null, previewBusy=false, boardBusy=false, closedJobs=new Set(), queuedEffects=new Map(), toastTimer, savePromise=null;
const viewerSource={1:null,4:null};
const liveFrames={1:{meta:null,url:null},4:{meta:null,url:null}};
const fingerprints={}, rects={train:[704,396,512,288],legal:[704,396,512,288]}, cameraDirty=new Set();
const submittedJobs=new Map(), watchedJobs=new Set();
let cameraSaveTimer=null,cameraSavePromise=null,cameraSaveError='',cameraEditSerial=0;
const cameraRevisions=new Map(),cameraInputValues=new Map();
const legalFields=['legal0','legal1','legal2','legal3','confidence','trackHits'],legalDirty=new Set(),legalRevisions=new Map();
let legalEditSerial=0,legalReadbackAfter=0,legalInputSize=null;
let actionDirty=false,actionModelSha='';
let runtimeOptionsDraft=null,runtimeOptionsRequest=null,runtimeModelChoice=null;
const headings = [
 ['画面调校', '在快照上拖选范围，或输入原图坐标。'],
 ['样本标注', '拖动画框，点选姿态。Delete 删框，Ctrl + Z 撤销。'],
 ['训练与测试', '按训练时间选择版本，现场测试每组 10 张。'],
 ['部署与运行', '合法区和电磁阀设置分别生效。']
];
const kinds={preview:'纯预览',freeze:'固定参数',capture:'采集',train:'训练',helper_train:'助手训练',assist:'候选框',test:'测试',deploy:'模型部署',legal:'合法区更新',camera:'相机调节',readback:'参数回读',convert:'RKNN 转换',action_output:'GPIO 设置',runtime_options:'全局运行选项'};
function notify(message,error=false){$('notice').hidden=false;$('notice').textContent=message;$('notice').classList.toggle('error',error);}
function toast(message,tone='info'){$('toast').textContent=message;$('toast').dataset.tone=tone;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,tone==='error'?8000:4500);}
async function bootstrap(){const r=await fetch('/api/bootstrap',{cache:'no-store'});if(!r.ok)throw Error('无法建立本机会话。');token=(await r.json()).token;}
async function api(path,body,again=true){
 const options={cache:'no-store',credentials:'same-origin'};
 if(body!==undefined){options.method='POST';options.headers={'Content-Type':'application/json','X-Console-Token':token};options.body=JSON.stringify({...body,page});}
 const r=await fetch(path,options);const data=await r.json();
 if(r.status===403&&again){await bootstrap();return api(path,body,false);}
 if(!r.ok)throw Error(data.error||'请求失败。');return data;
}
async function action(name,args={},effect=null){
 if(name==='freeze')validRect('train',args.rectangle);
 if(name==='capture'||name==='test')validRect('train',state.flow.rectangle);
 const submittedFields=new Set();
 if(name==='freeze'){for(const k of Object.keys(args.edited||{}))submittedFields.add('cam_'+k);for(const id of cameraDirty)if(id.startsWith('auto_'))submittedFields.add(id);}
 if(name==='camera'){
  for(const k of Object.keys(args.values||{}))if(k!=='auto')submittedFields.add('cam_'+k);
  for(const k of Object.keys(args.values?.auto||{}))submittedFields.add('auto_'+k);
 }
 const cameraEdits=new Map([...cameraDirty].filter(id=>submittedFields.has(id)).map(id=>[id,$(id)?.type==='checkbox'?$(id).checked:$(id)?.value]));
 const legalEdits=name==='legal'?new Map(legalFields.map(id=>[id,{value:$(id).value,revision:legalRevisions.get(id)||0}])):null;
 const result=await api('/api/action/'+name,{flow:flowId,...args});
 if(result?.id&&result?.kind&&result?.state){
  submittedJobs.set(result.id,{cameraEdits,legalEdits,actionEdits:name==='action_output'?JSON.stringify(args.values):null});watchedJobs.add(result.id);closedJobs.delete(result.id);
  if(effect)queuedEffects.set(result.id,effect);toast(`${kinds[result.kind]||'操作'}已提交。`);
 }else if(!['prepare_deploy','stop'].includes(name))toast('操作完成。','success');
 await refresh();return result;
}
function bind(id,fn){$(id).addEventListener('click',async e=>{const b=e.currentTarget;if(b.dataset.pending)return;b.dataset.pending='1';b.disabled=true;try{await fn(e);}catch(err){notify(err.message,true);}finally{delete b.dataset.pending;b.disabled=false;updateAvailability();}});}
function modal(title, html, label, handler) {
 const dialog = $('modal'), confirm = $('modalConfirm'), error = $('modalError');
 $('modalTitle').textContent = title;
 $('modalBody').innerHTML = html;
 $('modalCancel').textContent = handler ? '取消' : '关闭';
 error.hidden = true;
 confirm.textContent = label;
 confirm.disabled = false;
 confirm.hidden = !handler;
 const form = $('dialogForm');
 form.onsubmit = async function submit(event) {
  event.preventDefault();
  if (!handler || confirm.disabled) return;
  confirm.disabled = true;
  error.hidden = true;
  try { await handler(); if (form.onsubmit===submit) dialog.close(); }
  catch (e) { if (form.onsubmit===submit) { error.textContent = e.message; error.hidden = false; } }
  finally { if (form.onsubmit===submit) confirm.disabled = false; }
 };
 $('modalClose').onclick = $('modalCancel').onclick = () => dialog.close();
 dialog.showModal();
}
async function newFlow(){modal('开始一个新场景','<label>场景名称<input id="newName" maxlength="80" placeholder="例如：器件侧面 · 工位1"></label><p>新流程会关闭上一流程的推理与电磁阀，进入持久纯预览。历史图像、模型和标注保留。</p>','新建流程',async()=>{try{await saveDraftIfNeeded();}catch(e){try{sessionStorage.setItem('unsaved-'+flowId+'-'+frameId,JSON.stringify(draft));}catch{}toast('旧标注草稿未提交，已尽量保留在当前浏览器会话；新流程继续创建');}const p=await api('/api/action/new',{flow:flowId,name:$('newName').value||'新场景'});await switchFlow(p.id);});}
async function switchFlow(id){
 const next=await api('/api/state?flow='+encodeURIComponent(id));
 if(!next.flow)throw Error('场景读取失败，请重新打开。');
 if(id!==flowId){
  clearTimeout(cameraSaveTimer);if(cameraDirty.size){try{sessionStorage.setItem('camera-draft-'+flowId,JSON.stringify([...cameraDirty].map(k=>[k,$(k)?.type==='checkbox'?$(k).checked:$(k)?.value])));}catch{}}
  cameraDirty.clear();cameraRevisions.clear();cameraSaveError='';processDirty.clear();liveMeta=null;
  legalDirty.clear();legalRevisions.clear();legalInputSize=null;actionDirty=false;actionModelSha='';
  for(const n of [1,4]){
   viewerSource[n]=null;
   if(liveFrames[n].url)URL.revokeObjectURL(liveFrames[n].url);
   liveFrames[n]={meta:null,url:null};
   $('liveMount'+n).replaceChildren($('snapshotStandby').content.cloneNode(true));
   $('liveMeta'+n).textContent='HDMI · 手动快照';
  }
 }
 flowId=id;
 state=next;
 frameId=null;
 runId=testId=helperChoice='';
 $('helperSelect').value='';
 runtimeModelChoice=runtimeOptionsDraft=runtimeOptionsRequest=null;
 legalReadbackAfter=0;
 dirty=false;
 draft=[];
 selectedBox=null;
 frameRevision=-1;
 undo=[];
 $('notice').hidden=true;
 Object.keys(fingerprints).forEach(k=>delete fingerprints[k]);
 history.replaceState(null,'','/?flow='+id+(boardAutoConnect?'':'&connect=manual'));
 step=next.flow.stage||1;
 rects.train=clone(next.flow.rectangle);
 syncRect('train');
 render();
 setStep(step);
}
function setStep(n) {
 step = n;
 liveMeta = liveFrames[n]?.meta || null;
 document.querySelectorAll('.page').forEach((panel, i) => panel.hidden = i !== n - 1);
 document.querySelectorAll('.step').forEach(button => {
  const current = Number(button.dataset.step) === n;
  button.classList.toggle('active', current);
  if (current) button.setAttribute('aria-current', 'step');
  else button.removeAttribute('aria-current');
 });
 $('pageIndex').textContent = '工作流程 / 0' + n;
 [$('pageTitle').textContent, $('pageSubtitle').textContent] = headings[n - 1];
 if (n === 1 || n === 4) { renderViewerSource(n); drawLiveSelection(); }
 if (n === 2) renderAnnotation();
 if (n === 3) renderTests();
 updateAvailability();
}
document.querySelectorAll('.step').forEach(b=>b.onclick=async()=>{try{await saveCameraIfNeeded();await saveDraftIfNeeded();setStep(Number(b.dataset.step));window.scrollTo(0,0);}catch(e){notify(e.message,true);}});
function changed(key,value){const s=JSON.stringify(value);if(fingerprints[key]===s)return false;fingerprints[key]=s;return true;}
async function refresh(){
 if(!token)return;if(refreshPromise)return refreshPromise;
 const owner=flowId;
 refreshPromise=(async()=>{
  try {
   const next=await api('/api/state'+(owner?'?flow='+owner:''));
   if(flowId===owner){state=next;render();}
  } catch(e){if(flowId===owner)notify(e.message,true);}
 })();
 try{await refreshPromise;}finally{refreshPromise=null;}
}
function render(){
 const hasFlow = Boolean(state?.flow);
 $('settingsOpen').disabled = !state?.settings;
 $('flowPages').hidden = !hasFlow;
 $('welcome').hidden = hasFlow;
 $('flowTag').hidden = !hasFlow;
 document.querySelectorAll('.step').forEach(button => button.disabled = !hasFlow);
 if (!hasFlow) {
  $('flowName').textContent = '尚未打开场景';
  return;
 }
 if (step === 1 || step === 4) renderViewerSource(step);
 const p=state.flow;$('flowName').textContent=p.name;$('flowTag').textContent=p.id.slice(0,8);renderJobs();renderFrames();renderLabels();renderHelpers();renderRuns();renderTests();renderBoard(state.board);renderRuntimeIsolation();updateAvailability();
 const base=p.frames.filter(f=>f.batch===p.capture_batch&&f.role==='train'&&!f.excluded),reviewed=base.filter(f=>f.reviewed).length;
 $('captureSummary').textContent=`${base.length} / 15 张原图`;$('annotationSummary').textContent=`已确认 ${reviewed} 张`;
 const feedback=p.frames.filter(f=>f.feedback&&f.base_batch===p.capture_batch&&!f.excluded);
 $('trainCounts').innerHTML=stat(base.length,'本批原图')+stat(feedback.length,'纠错图像')+stat(p.labels.filter(v=>v.active).length,'姿态类别');
}
function renderRuntimeIsolation(){
 const isolation=state.flow.runtime_isolation;$('flowRuntime').dataset.state=isolation?.state||'';$('flowRuntime').hidden=!isolation;if(!isolation)return;
 $('flowRuntimeStatus').textContent=isolation.state==='pending'?'正在关闭旧推理与电磁阀，等待纯预览回读；完成前暂停板端操作。':isolation.state==='error'?'尚未确认进入纯预览：'+isolation.message:isolation.mode==='preview'?'纯预览已就绪；不加载历史模型，电磁阀已关闭。请明确选择本流程训练版本后部署。':'流程记录：本场景已执行模型部署。当前运行状态可通过「读取板端」核对。';
 $('retryPreview').hidden=isolation.state!=='error';
}
function stat(n,name){return `<div class="stat"><strong>${esc(n)}</strong><span>${esc(name)}</span></div>`;}
function renderJobs(){
 const jobs=state.jobs||[],active=jobs.filter(j=>['queued','running'].includes(j.state)),last=active.at(-1)||jobs.at(-1);
 $('jobbar').hidden=!last;if(last){$('jobbar').dataset.state=last.state;$('jobKind').textContent=kinds[last.kind]||last.kind;const status=({queued:'排队中',complete:'已完成',error:'失败',cancelled:'已取消'}[last.state]||'执行中'),detail=last.message;$('jobMessage').textContent=' · '+status+(detail&&detail!==status?' · '+detail:'');$('stopJob').hidden=!['queued','running'].includes(last.state);const p=last.progress;$('jobProgress').value=last.state==='complete'?100:p?.steps?100*p.step/p.steps:0;}
 const completion={preview:'纯预览已确认，旧推理与电磁阀已关闭。',freeze:'已保持现有成像参数、暂停自动调节并取得原生选区图像',capture:'图像已保存，可以开始标注。',camera:'板端操作已完成，实际参数已回读',readback:'板端参数实读完成',legal:'合法区已在板端生效',assist:'候选框已生成，请人工确认',helper_train:'画框助手训练完成，请明确选择新版本',train:'GPU训练和RKNN转换完成',convert:'RKNN转换完成',test:'新图推理完成，等待你检查',deploy:'板端已报告模型加载，性能与准确率等待验收',action_output:'姿态、延迟及脉宽已由GPIO服务回读确认',runtime_options:'全局运行选项已由板端回读确认。'};
 for(const j of jobs){
  if(['queued','running'].includes(j.state)){watchedJobs.add(j.id);continue;}
  if(closedJobs.has(j.id))continue;closedJobs.add(j.id);
                                                                          
  if(!watchedJobs.has(j.id)&&!submittedJobs.has(j.id))continue;
  if(j.state==='error'){notify(`${kinds[j.kind]||'操作'}失败：${j.message}`,true);toast(`${kinds[j.kind]||'操作'}失败：${j.message}`,'error');}
  else if(j.state==='cancelled')toast(`${kinds[j.kind]||'操作'}已取消：${j.message}`,'warning');
  else if(j.state==='complete'){
   const partial=j.capture_partial||j.result?.complete===false,wb=j.result?.white_balance;
   const message=partial?(j.capture_partial?j.message:`已保留 ${j.result?.frames??0}/10 张，请重新采集完整测试组。`):wb?wb.message+'；其余自动调节会持续更新对应输入值':(completion[j.kind]||'操作完成。');
   notify(`${kinds[j.kind]||'操作'}${partial?'部分完成':'完成'}：${message}`);toast(`${kinds[j.kind]||'操作'}${partial?'部分完成':'完成'} · ${message}`,partial||wb&&!wb.converged?'warning':'success');
   const effect=queuedEffects.get(j.id);
   if(j.kind==='legal'&&j.result?.settings){
    legalReadbackAfter=Math.max(legalReadbackAfter,j.result.capture_received_monotonic||0);
    syncLegalSettings(j.result.settings,submittedJobs.get(j.id)?.legalEdits);
    if(state.board?.connected)state.board.rect={...state.board.rect,...j.result,recorded_monotonic:Math.max(state.board.rect.recorded_monotonic||0,j.result.capture_received_monotonic||0)};
   }
   if(j.kind==='action_output'&&j.result?.applied){
    if(submittedJobs.get(j.id)?.actionEdits===JSON.stringify(actionForm()))actionDirty=false;
    if(state.board?.connected)state.board.action_output={...state.board.action_output,config:j.result.config,daemon:j.result.daemon,fresh:true,error:null};
    renderActionOutput(state.board);
   }
   if(j.kind==='runtime_options'&&j.result?.board)state.board=j.result.board;
   if(effect==='freeze'){rects.train=clone(state.flow.rectangle);syncRect('train');setStep(2);}
   if(effect==='capture'){frameId=null;frameRevision=-1;}
   if(effect==='test'){testId=state.flow.tests.at(-1)?.id||'';fingerprints.tests='';}
   if(effect==='deploy')setStep(4);
   if(['camera','readback','legal','freeze','deploy','action_output','runtime_options'].includes(j.kind)){
    if(j.page===page&&['camera','freeze'].includes(j.kind)&&!submittedJobs.get(j.id)?.autoSave)for(const[id,value]of submittedJobs.get(j.id)?.cameraEdits||[]){const el=$(id);if(el&&(el.type==='checkbox'?el.checked:el.value)===value)cameraDirty.delete(id);}
    refreshBoard();
   }
  }
  queuedEffects.delete(j.id);submittedJobs.delete(j.id);watchedJobs.delete(j.id);
 }
}
function currentFrames(){const p=state?.flow;return p?p.frames.filter(f=>(f.batch===p.capture_batch&&f.role==='train')||(f.feedback&&f.base_batch===p.capture_batch)):[];}
function selectedFrame(){return state?.flow.frames.find(f=>f.id===frameId);}
function renderFrames(){
 const rows=currentFrames();if(!rows.some(f=>f.id===frameId)){frameId=rows.find(f=>!f.excluded)?.id||rows[0]?.id||null;frameRevision=-1;dirty=false;}
 if(changed('frames',rows.map(f=>[f.id,f.reviewed,f.revision,f.excluded]))){$('filmstrip').innerHTML=rows.map((f,i)=>`<button class="thumb ${f.reviewed?'confirmed':''} ${f.id===frameId?'selected':''}" data-frame="${f.id}" title="${f.excluded?'已排除，可恢复':'选择原图'}"><img loading="lazy" src="/api/image?flow=${flowId}&frame=${f.id}" alt="原图${i+1}"><small>${f.feedback?'纠错 ':''}${i+1} · ${f.excluded?'已排除':f.reviewed?'已确认':'待确认'}</small></button>`).join('');$('filmstrip').querySelectorAll('button').forEach(b=>b.onclick=async()=>{try{await saveDraftIfNeeded();frameId=b.dataset.frame;frameRevision=-1;dirty=false;undo=[];selectedBox=null;renderAnnotation();$('filmstrip').querySelectorAll('button').forEach(x=>x.classList.toggle('selected',x.dataset.frame===frameId));}catch(e){notify(e.message,true);}});}
 const f=selectedFrame();if(f&&!dirty&&frameRevision!==f.revision){draft=clone(f.objects);frameRevision=f.revision;undo=[];renderAnnotation();}
}
function renderLabels(){
 const labels=state.flow.labels.filter(l=>l.active);
 if(!changed('labels',labels))return;const previous=$('drawingPose').value;
 $('poses').innerHTML=labels.map(l=>`<div class="pose-chip"><button data-pose="${l.id}">${esc(l.name)}</button><button class="remove-pose" data-remove="${l.id}" title="删除姿态 ${esc(l.name)}">×</button></div>`).join('');
 $('drawingPose').innerHTML='<option value="">待指定</option>'+labels.map(l=>`<option value="${l.id}">${esc(l.name)}</option>`).join('');$('drawingPose').value=labels.some(l=>l.id===previous)?previous:'';
 $('poses').querySelectorAll('[data-pose]').forEach(b=>b.onclick=()=>{if(!selectedBox){$('drawingPose').value=b.dataset.pose;toast('新增框的默认姿态为 '+b.textContent);return;}remember();draft.find(o=>o.id===selectedBox).class_id=b.dataset.pose;dirty=true;renderBoxes();});
 $('poses').querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>modal('删除姿态','<p>引用此姿态的标注会变为待指定。历史训练版本保留原类别含义。</p>','删除姿态',async()=>{await saveDraftIfNeeded();await action('label',{operation:'delete',id:b.dataset.remove});}));
}
function poseName(id){return state?.flow.labels.find(l=>l.id===id)?.name||(id==='ignore'?'忽略':'待指定');}
function renderAnnotation(){
 const f=selectedFrame();if(!f){
  delete $('annotationMount').dataset.frame;
  $('annotationMount').innerHTML='<div class="empty live-empty">采集原图后即可开始标注</div>';
  $('frameTitle').textContent='图像标注';
  $('frameMeta').textContent='原图像素坐标';
  $('excludeFrame').textContent='排除本图';
  $('objectList').replaceChildren();
  $('boxCoords').replaceChildren();
  return;
 }
 if(frameRevision!==f.revision&&!dirty){draft=clone(f.objects);frameRevision=f.revision;}
 if($('annotationMount').dataset.frame!==f.id){$('annotationMount').dataset.frame=f.id;$('annotationMount').innerHTML=`<img src="/api/image?flow=${flowId}&frame=${f.id}" alt="采集原图"><svg id="annotationSvg" viewBox="0 0 ${f.size[0]} ${f.size[1]}" aria-label="元件标注区域"></svg>`;attachAnnotationPointer($('annotationSvg'));}
 $('frameTitle').textContent=`${f.feedback?'纠错图像':'原生图像'} · ${currentFrames().findIndex(x=>x.id===f.id)+1}`;
 $('frameMeta').textContent=`${f.size[0]} × ${f.size[1]} · 源帧 ${f.source_frame_id}${dirty?' · 未保存':''}`;
 updateAvailability();
 $('excludeFrame').textContent=f.excluded?'恢复本图':'排除本图';renderBoxes();
}
function svgEl(name,attrs){const n=document.createElementNS(NS,name);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,v);return n;}
function drawBoxes(svg,objects,size,interactive=false){
 svg.replaceChildren();const font=Math.max(10,size[0]/55);
 for(let i=0;i<objects.length;i++){const o=objects[i],b=o.bbox,sel=interactive&&o.id===selectedBox;
 const r=svgEl('rect',{x:b[0],y:b[1],width:b[2]-b[0],height:b[3]-b[1],class:'svg-box'+(sel?' selected':'')+(o.class_id===null?' pending':''),'data-id':o.id||''});svg.append(r);
 const title=interactive?`${i+1} · ${poseName(o.class_id)}`:`${poseName(o.label)} ${Number(o.confidence*100).toFixed(1)}%`;
 const text=svgEl('text',{x:b[0]+2,y:Math.max(font,b[1]-4),'font-size':font,class:'svg-label','pointer-events':'none'});text.textContent=title;svg.append(text);
 if(sel){const handle=Math.max(5,size[0]/100);[[b[0],b[1]],[b[2],b[1]],[b[2],b[3]],[b[0],b[3]]].forEach(([x,y],k)=>svg.append(svgEl('rect',{x:x-handle/2,y:y-handle/2,width:handle,height:handle,class:'svg-handle','data-id':o.id,'data-handle':k})));}
 }
}
function renderBoxes(){
 const f=selectedFrame(),svg=$('annotationSvg');if(!f||!svg)return;updateAvailability();drawBoxes(svg,draft,f.size,true);
 $('objectList').innerHTML=draft.map((o,i)=>`<button class="object-row ${o.id===selectedBox?'selected':''} ${o.class_id===null?'pending':''}" data-object="${o.id}"><span>元件 ${i+1}</span><span>${esc(poseName(o.class_id))}</span></button>`).join('');
 $('objectList').querySelectorAll('button').forEach(b=>b.onclick=()=>{selectedBox=b.dataset.object;renderBoxes();});
 const o=draft.find(x=>x.id===selectedBox);$('boxCoords').innerHTML=o?o.bbox.map((v,i)=>`<label>${['左 X','上 Y','右 X','下 Y'][i]}<input data-coordinate="${i}" type="number" step="1" value="${Math.round(v)}"></label>`).join(''):'';
 $('boxCoords').querySelectorAll('input').forEach(input=>input.onchange=()=>{const b=o.bbox.slice();b[Number(input.dataset.coordinate)]=Number(input.value);if(!(0<=b[0]&&b[0]<b[2]&&b[2]<=f.size[0]&&0<=b[1]&&b[1]<b[3]&&b[3]<=f.size[1])){toast('坐标必须位于原图内且有面积');renderBoxes();return;}remember();o.bbox=b;dirty=true;renderBoxes();});
 $('frameMeta').textContent=`${f.size[0]} × ${f.size[1]} · 源帧 ${f.source_frame_id}${dirty?' · 未保存':''}`;
}
function remember(){undo.push(clone(draft));if(undo.length>50)undo.shift();}
function point(svg,e){const p=new DOMPoint(e.clientX,e.clientY).matrixTransform(svg.getScreenCTM().inverse());return[p.x,p.y];}
function attachAnnotationPointer(svg){
 let drag=null;
 svg.addEventListener('pointerdown',e=>{if(e.button!==0)return;const f=selectedFrame();if(!f||f.excluded)return;let [x,y]=point(svg,e);x=Math.max(0,Math.min(f.size[0],x));y=Math.max(0,Math.min(f.size[1],y));const id=e.target.dataset.id,o=draft.find(o=>o.id===id);remember();
 if(o){selectedBox=id;drag={mode:e.target.hasAttribute('data-handle')?'resize':'move',corner:Number(e.target.dataset.handle),start:[x,y],box:o.bbox.slice(),id};}
 else{selectedBox=crypto.randomUUID();draft.push({id:selectedBox,bbox:[x,y,x,y],class_id:$('drawingPose').value||null,proposed:false});drag={mode:'draw',start:[x,y],id:selectedBox};}
 svg.setPointerCapture(e.pointerId);e.preventDefault();renderBoxes();});
 svg.addEventListener('pointermove',e=>{if(!drag)return;const f=selectedFrame(),o=draft.find(o=>o.id===drag.id);let[x,y]=point(svg,e);x=Math.max(0,Math.min(f.size[0],x));y=Math.max(0,Math.min(f.size[1],y));const[sx,sy]=drag.start;
 if(drag.mode==='draw')o.bbox=[Math.min(x,sx),Math.min(y,sy),Math.max(x,sx),Math.max(y,sy)];
 else if(drag.mode==='move'){const b=drag.box,dx=Math.max(-b[0],Math.min(f.size[0]-b[2],x-sx)),dy=Math.max(-b[1],Math.min(f.size[1]-b[3],y-sy));o.bbox=[b[0]+dx,b[1]+dy,b[2]+dx,b[3]+dy];}
 else{const b=drag.box.slice(),left=drag.corner===0||drag.corner===3,top=drag.corner<2;b[left?0:2]=left?Math.min(x,b[2]-1):Math.max(x,b[0]+1);b[top?1:3]=top?Math.min(y,b[3]-1):Math.max(y,b[1]+1);o.bbox=b;}
 dirty=true;renderBoxes();});
 const finish=()=>{if(!drag)return;const o=draft.find(o=>o.id===drag.id);if(o&&(o.bbox[2]-o.bbox[0]<2||o.bbox[3]-o.bbox[1]<2)){draft=draft.filter(x=>x!==o);selectedBox=null;}else if(o)o.bbox=o.bbox.map(v=>Math.round(v));drag=null;dirty=true;renderBoxes();};
 svg.addEventListener('pointerup',finish);svg.addEventListener('pointercancel',finish);
}
async function saveDraftIfNeeded(){if(dirty&&frameId)await saveAnnotation(false);}
async function saveAnnotation(reviewed){
 if(savePromise)await savePromise;const f=selectedFrame();if(!f)throw Error('请先选择一张图像');const copy=clone(draft),owner=flowId;
 savePromise=(async()=>{const result=await api('/api/action/annotate',{flow:owner,frame:f.id,objects:copy,reviewed,page});if(flowId===owner&&frameId===f.id){dirty=JSON.stringify(draft)!==JSON.stringify(copy);frameRevision=result.revision;const index=state.flow.frames.findIndex(x=>x.id===f.id);state.flow.frames[index]=result;}await refresh();toast(reviewed?'本图已确认':'草稿已保存');})();
 try{await savePromise;}finally{savePromise=null;}
}
function renderHelpers(){
 const old=$('helperSelect').value;
 if(changed('helperOptions',state.helpers)){$('helperSelect').innerHTML='<option value="">选择训练时间和场景</option>'+state.helpers.map(h=>`<option value="${esc(h.key)}">${esc(h.trained_at||'训练时间未记录')} · ${esc(h.scene?.name||h.scene?.project||h.run)}</option>`).join('');$('helperSelect').value=state.helpers.some(h=>h.key===old)?old:'';}
 const selected=helperChoice && state.flow.helper_selection?.key===helperChoice ? state.flow.helper_selection : null;$('helperSummary').textContent=selected?`已选择：${selected.trained_at||'时间未记录'}`:'本次尚未选择助手';showHelper();
}
function showHelper(){const h=state?.helpers.find(h=>h.key===$('helperSelect').value);$('helperDetails').textContent=h?`训练开始（北京时间）：${h.training_started_beijing||'未记录'}\n训练完成（北京时间）：${h.trained_at||'未记录，文件时间不能作为训练时间'}\n场景：${JSON.stringify(h.scene||'未记录')}\nSHA256：${h.sha256}\n原生定位分支：仅产生候选框，全部由人工确认。`:'';}
function renderRuns(){
 const runs=state.flow.runs;
 if(changed('runs',runs.map(r=>[r.id,r.manifest?.training_completed_beijing,r.model_file,r.manifest?.output_hashes?.[r.model_file],r.int8_test_available,r.manifest?.output_hashes?.['detector_int8.rknn'],r.convert_available,r.state]))){$('runSelect').innerHTML='<option value="">选择本次使用的训练版本</option>'+runs.slice().reverse().map(r=>`<option value="${r.id}">${esc(r.manifest?.training_completed_beijing||'训练未完成')} · ${r.id.slice(0,8)}${r.convert_available?' · '+(r.model_file==='detector_int8.rknn'?'INT8':'FP16')+' RKNN已生成':''}</option>`).join('');$('runSelect').value=runs.some(r=>r.id===runId)?runId:'';}
 showRun();
}
function selectedRun(){return state?.flow.runs.find(r=>r.id===runId);}
function runArgs(){const r=selectedRun();if(!r?.manifest?.training_completed_beijing)throw Error('请选择有实际完成时间的训练版本');return{run:r.id,trained_at:r.manifest.training_completed_beijing,confirmed:true};}
function showRun(){
 const r=selectedRun(),m=r?.manifest;$('runDetails').textContent=m?`训练开始（北京时间）：${m.training_started_beijing||'未开始'}\n训练完成（北京时间）：${m.training_completed_beijing||'未完成'}\n场景：${m.scene?.name||state.flow.name}\nROI: ${m.roi?.join(' × ')}\n原图数量：${m.original_images} · 原生裁剪\nRKNN：${r.convert_available?r.model_file+'（哈希已核验）':'尚未转换或哈希校验失败'}\nPT SHA256：${m.output_hashes?.['detector.pt']||'未生成'}\nRKNN SHA256：${m.output_hashes?.[r.model_file]||'未生成'}\n现场新图测试：INT8 RKNN / RK NPU\n测试 INT8 SHA256：${m.output_hashes?.['detector_int8.rknn']||'未生成；请先转换INT8'}`:'请选择本次使用的训练版本。';
 $('deploySummary').textContent=m?.training_completed_beijing?`${state.flow.name} · 北京时间 ${m.training_completed_beijing} · ${r.id.slice(0,8)}`:'请在第三步选择模型，并完成人工新图复核。';
}
function renderTests(){
 const tests=state.flow.tests.filter(t=>!runId||t.run===runId);
 if(!tests.some(t=>t.id===testId))testId=tests.at(-1)?.id||'';
 if(changed('testOptions',tests.map(t=>[t.id,t.reviewed,t.used_for_training,t.inference]))){$('testSelect').innerHTML=tests.length?tests.slice().reverse().map(t=>`<option value="${t.id}">${esc(t.created_at)} · ${t.inference?.backend==='RKNN-INT8-NPU'?'INT8 NPU':'历史 PT'}${t.used_for_training?' · 已转纠错':t.reviewed?' · 已复核':''}</option>`).join(''):'<option value="">尚无测试组</option>';}
 $('testSelect').value=testId;const t=tests.find(t=>t.id===testId);if(!changed('tests',t))return;
 if(!t){$('testGrid').innerHTML='<div class="empty">现场测试完成后可在这里查看图像。</div>';$('testNote').textContent='未标注测试图不提供自动准确率。';return;}
 $('testGrid').innerHTML=t.predictions.map((r,i)=>{const f=state.flow.frames.find(f=>f.id===r.frame);return `<article class="test-tile"><label><input type="checkbox" data-testframe="${f.id}">第 ${i+1} 张 · ${r.detections.length} 个结果</label><div class="image-stage"><img loading="lazy" width="${f.size[0]}" height="${f.size[1]}" src="/api/image?flow=${flowId}&frame=${f.id}" alt="测试原图${i+1}"><svg data-prediction="${i}" viewBox="0 0 ${f.size[0]} ${f.size[1]}"></svg></div><small>源帧 ${f.source_frame_id} · ${f.size.join(' × ')}</small></article>`;}).join('');
 $('testGrid').querySelectorAll('svg').forEach(svg=>{const r=t.predictions[Number(svg.dataset.prediction)],f=state.flow.frames.find(f=>f.id===r.frame);drawBoxes(svg,r.detections,f.size);});
 const backend=t.inference?.backend==='RKNN-INT8-NPU'?`INT8 NPU · SHA256 ${t.inference.model_sha256}。`:'历史PT浮点结果，不作为INT8部署验收。';
 $('testNote').textContent=backend+(!t.complete?`本组仅${t.frames.length}/10 张，已保留供查看，请重新采集完整测试组。`:t.used_for_training?'本组已参与纠错，不再作为独立测试。':t.reviewed?'已记录人工检查；仍需由你判断现场效果。':'尚未人工确认。推理置信度不是准确率。');
}
function setupRect(name,mount){$(mount).innerHTML=['起点 X','起点 Y','宽度','高度'].map((label,i)=>`<label>${label}<input id="${name}${i}" type="number" data-rect="${name}" data-index="${i}" value="${rects[name][i]}" min="0" max="${i%2?1080:1920}"${name==='legal'&&i>=2?' disabled':''}></label>`).join('');$(mount).querySelectorAll('input').forEach(el=>el.oninput=()=>{rects[name][Number(el.dataset.index)]=Number(el.value);if(name==='legal')markLegalEdit(el.id);drawLiveSelection();updateTileCount();});}
function syncRect(name){document.querySelectorAll(`[data-rect="${name}"]`).forEach(e=>e.value=rects[name][Number(e.dataset.index)]);updateTileCount();drawLiveSelection();}
function validRect(name,r=rects[name]){const preview=name==='legal'&&state?.board?.runtime?.inference_enabled===false,min=preview?[8,8]:[512,288];if(!r.every(Number.isInteger)||r[0]<0||r[1]<0||r[2]<min[0]||r[3]<min[1]||r[0]+r[2]>1920||r[1]+r[3]>1080)throw Error(preview?'纯预览选区需位于1920×1080内，宽高至少8像素。':name==='train'?'采集范围至少512×288，且必须位于 1080p 原图内。':'合法区必须位于1080p原图内，至少512×288，以保持训练切窗规则');if(r[2]*r[3]>MAX_ROI_PIXELS)throw Error('每张原生RGB采集图最多518,400像素；当前选区'+r[2]+'×'+r[3]+' = '+(r[2]*r[3]).toLocaleString('en-US')+'像素，请回第一步缩小范围并重新固定参数');return r.slice();}
function updateTileCount() {
 const [,,width,height]=rects.train, pixels=width*height;
                                                                                      
 const windows=(length,tile)=>Number.isFinite(length)&&length>=tile&&length<=1920
  ? 1+Math.ceil((length-tile)/(tile/2)) : 0;
 $('tileCount').textContent=windows(width,512)*windows(height,288);
 $('pixelMeter').value=Math.max(0,Math.min(pixels,MAX_ROI_PIXELS));
 const overLimit=pixels>MAX_ROI_PIXELS;
 $('trainPixels').dataset.invalid=String(overLimit);
 $('trainPixels').textContent=(Number.isFinite(pixels)?pixels.toLocaleString('en-US'):'—')
  +' / 518,400 像素'+(overLimit?' · 超出上限，请缩小选区。':'');
}
function liveSvg(){return $('liveMount'+step).querySelector('svg');}
function drawLiveSelection(){
 if (step === 1 || step === 4) {
  const name = step === 1 ? 'train' : 'legal';
  const [x,y,width,height] = rects[name], map = $(name+'Map');
  map.setAttribute('style', '--roi-x:'+x/1920*100+'%;--roi-y:'+y/1080*100+'%;--roi-w:'+width/1920*100+'%;--roi-h:'+height/1080*100+'%');
  map.setAttribute('aria-label', '原图选区示意，起点 '+x+'、'+y+'，宽 '+width+'，高 '+height+' 像素');
 }
 if(![1,4].includes(step)||!liveMeta?.camera_area)return;const svg=liveSvg();if(!svg)return;svg.replaceChildren();const [x,y,w,h]=liveMeta.camera_area,r=rects[step===1?'train':'legal'];if(state?.board?.runtime_options?.show_roi!==false)svg.append(svgEl('rect',{x:x+r[0]*w/1920,y:y+r[1]*h/1080,width:r[2]*w/1920,height:r[3]*h/1080,class:'svg-selection'}));
 if(step===4&&state?.board?.action_output?.installed){const actual=state.board.rect.settings.rectangle,cx=x+(actual[0]+actual[2]/2)*w/1920;svg.append(svgEl('line',{x1:cx,x2:cx,y1:y+actual[1]*h/1080,y2:y+(actual[1]+actual[3])*h/1080,class:'svg-action-line','pointer-events':'none'}));}
}
function attachLivePointer(svg){
 let start=null;
 svg.onpointerdown=e=>{
  if(e.button!==0||!liveMeta?.camera_area||step===4&&!legalInputSize&&state?.board?.runtime?.inference_enabled!==false)return;
  const [px,py]=point(svg,e),[x,y,w,h]=liveMeta.camera_area;if(px<x||py<y||px>x+w||py>y+h)return;
  start={name:step===1?'train':'legal',point:[(px-x)*1920/w,(py-y)*1080/h],rectangle:clone(rects.legal)};svg.setPointerCapture(e.pointerId);e.preventDefault();
 };
 svg.onpointermove=e=>{
  if(!start)return;const [px,py]=point(svg,e),[x,y,w,h]=liveMeta.camera_area,nx=Math.max(0,Math.min(1920,(px-x)*1920/w)),ny=Math.max(0,Math.min(1080,(py-y)*1080/h));
  if(start.name==='legal'){
   const [width,height]=legalInputSize||start.rectangle.slice(2),next=[Math.round(Math.max(0,Math.min(1920-width,start.rectangle[0]+nx-start.point[0]))),Math.round(Math.max(0,Math.min(1080-height,start.rectangle[1]+ny-start.point[1]))),width,height];
   for(let i=0;i<2;i++)if(next[i]!==rects.legal[i])markLegalEdit(legalFields[i]);rects.legal=next;
  }else rects.train=[Math.round(Math.min(start.point[0],nx)),Math.round(Math.min(start.point[1],ny)),Math.round(Math.abs(nx-start.point[0])),Math.round(Math.abs(ny-start.point[1]))];
  syncRect(start.name);
 };
 svg.onpointerup=()=>{if(!start)return;const name=start.name;start=null;if(name==='train'){const r=rects.train;r[2]=Math.max(512,r[2]);r[3]=Math.max(288,r[3]);r[0]=Math.min(r[0],1920-r[2]);r[1]=Math.min(r[1],1080-r[3]);}syncRect(name);};
 svg.onpointercancel=()=>start=null;
}
function renderViewerSource(n) {
 const sample=state?.flow?.frames.find(f=>f.role==='train' && f.batch===state.flow.capture_batch && !f.excluded);
 const mode=viewerSource[n] || (sample && !liveFrames[n].meta ? 'reference' : 'live');
 const reference=Boolean(sample && mode==='reference');
 const mount=$('refMount'+n);
 $('refTab'+n).disabled=!sample;
 $('refTab'+n).setAttribute('aria-pressed', String(reference));
 $('liveTab'+n).setAttribute('aria-pressed', String(!reference));
 $('liveMount'+n).hidden=reference;
 $('liveMeta'+n).hidden=reference;
 $('refMeta'+n).hidden=!reference;
 mount.hidden=!reference;
 if (!reference) return;
 const src='/api/image?flow='+flowId+'&frame='+sample.id;
 if (mount.dataset.source===src) return;
 mount.dataset.source=src;
 const img=new Image(sample.size[0],sample.size[1]);
 img.alt='当前采集批次保存的原图';
 img.onerror=()=>{ if(mount.dataset.source===src) $('refMeta'+n).textContent='批次原图读取失败。'; };
 img.src=src;
 mount.replaceChildren(img);
 $('refMeta'+n).textContent='批次原图 · '+sample.size.join(' × ')+' px · 源帧 '+(sample.source_frame_id??'未记录');
}
for (const n of [1,4]) {
 for (const [tab,source] of [['liveTab','live'],['refTab','reference']]) {
  $(tab+n).addEventListener('click',()=>{viewerSource[n]=source;renderViewerSource(n);});
 }
}
async function refreshPreview(targetStep){
 if(previewBusy)return;
 if(!token||!flowId)throw Error('请先打开场景。');
 const owner=flowId,button=$('getPreview'+targetStep);let next=null;previewBusy=true;
 viewerSource[targetStep]='live';
 renderViewerSource(targetStep);
 for(const n of [1,4])$('getPreview'+n).disabled=true;
 button.textContent='正在获取…';$('liveMeta'+targetStep).textContent='正在获取…';
 try{
  const r=await fetch('/api/preview',{cache:'no-store'});
  if(!r.ok){if(r.status===403)await bootstrap();const e=await r.json();throw Error(e.error);}
  const bytes=Uint8Array.from(atob(r.headers.get('X-Image-Meta')),c=>c.charCodeAt(0));
  const meta=JSON.parse(new TextDecoder().decode(bytes)),blob=await r.blob();
  if(owner!==flowId)return;
  next=URL.createObjectURL(blob);
  const img=new Image();
  img.alt='HDMI 手动快照';
  img.src=next;
  await img.decode();
  if(owner!==flowId)return;
  const svg=svgEl('svg',{viewBox:'0 0 '+meta.size.join(' ')});
  attachLivePointer(svg);
  $('liveMount'+targetStep).replaceChildren(img,svg);
  const previous=liveFrames[targetStep].url;
  liveFrames[targetStep]={meta,url:next};
  if(previous)URL.revokeObjectURL(previous);
  if(targetStep===step){liveMeta=meta;drawLiveSelection();}
  $('liveMeta'+targetStep).textContent=`${meta.monitor} · ${meta.size.join(' × ')} · 获取于 ${new Date().toLocaleTimeString('zh-CN')}`;
  if(!meta.camera_area)$('liveMeta'+targetStep).textContent+=' · 缺少选区映射，请使用坐标输入';
 }catch(e){
  if(owner===flowId){$('liveMeta'+targetStep).textContent='获取失败，可点击重试：'+e.message;const image=$('liveMount'+targetStep).querySelector('img');if(image)image.style.opacity='.4';}
  throw e;
 }finally{if(next&&liveFrames[targetStep].url!==next)URL.revokeObjectURL(next);previewBusy=false;button.textContent='获取图像';updateAvailability();}
}
const cameraFields=[['exposure_us','曝光时间 μs',10,16000,1000,1],['gain_db','模拟增益 dB',0,30,0,.3],['digital_gain','数字增益 Q8',64,1024,256,1],['contrast','对比度 Q8',76,768,256,1],['brightness','亮度',-100,100,0,1],['histogram','直方图强度（请求保留值）',0,256,0,1],['rg','白平衡 R',128,512,256,1],['bg','白平衡 B',128,512,256,1],['gaussian','高斯强度（请求保留值）',0,256,0,1]];
const processFlags=[['gaussian','Gaussian平滑'],['histogram','直方图亮度映射'],['foreground','前景分割'],['binary_morphology','前景掩码形态学'],['sobel','Sobel边缘'],['edge_cleanup','边缘邻域清理'],['edge_overlay','彩色边缘叠加'],['background_shade','背景压暗'],['feature_statistics','特征统计'],['grayscale_morphology','灰度形态学融合'],['edge_closing','边缘闭运算'],['mask_boundary','前景内边界']];
const processParameters=[['gaussian_strength','Gaussian强度 Q8',256],['histogram_strength','直方图强度 Q8',256],['foreground_low','前景低阈值',255],['foreground_high','前景高阈值',255],['foreground_mode','分割方式：0≥低/1＜低/2区间',2],['foreground_invert','分割反相：0关/1开',1],['binary_operation','掩码操作：0直通/1腐蚀/2膨胀/3开/4闭',4],['binary_shape','掩码核：0方/1十字/2横/3竖',3],['sobel_threshold','Sobel阈值',2040],['edge_neighbors','清理最少相邻点数',8],['edge_foreground_only','边缘范围：0全图/1仅前景',1],['overlay_rgb','描边颜色 RGB十进制',16777215],['overlay_alpha','描边混合：0–4（四分比）',4],['background_level','背景保留：0–4（四分比）',4],['gray_operation','灰度操作：0直通/1开/2闭/3白帽/4黑帽',4],['gray_kernel5','灰度核：0为3×3/1为5×5',1],['gray_fusion','灰度融合：0–4（四分比）',4],['gray_delta_clip','灰度改变量上限',255],['feature_luma','统计前景亮度阈值',255],['feature_highlight','统计高亮阈值',255]];
const processDirty=new Set();let processPending=false;
function setupProcess(){
 $('processFlags').innerHTML=processFlags.map(([key,label])=>`<label class="auto-check"><input id="proc_${key}" type="checkbox">${label}<small id="proc_effect_${key}">尚未读取</small></label>`).join('');
 $('processParameters').innerHTML=processParameters.map(([key,label,max])=>`<label>${label}（请求保留值）<input id="proc_${key}" type="number" min="0" max="${max}" step="1"><small id="proc_actual_${key}">尚未读取</small></label>`).join('');
 for(const el of [...$('processFlags').querySelectorAll('input'),...$('processParameters').querySelectorAll('input'),$('processView')])el.onchange=()=>{processDirty.add(el.id);$('processActual').textContent='有未提交修改；请应用后查看实际画面。';};
 bind('applyProcess',async()=>{
  if(!state?.board?.camera?.processing_controls)throw Error('请先读取支持PRC2的板端');
  if(!state.board.camera.automatic?.config?.frozen)throw Error('请先暂停自动调节');
  await saveCameraIfNeeded();if(!processDirty.size)return;
  const patch={},enabled={},parameters={};
  if(processDirty.has('processView'))patch.view=Number($('processView').value);
  for(const[key]of processFlags)if(processDirty.has('proc_'+key))enabled[key]=$('proc_'+key).checked;
  for(const[key,,max]of processParameters)if(processDirty.has('proc_'+key)){const value=Number($('proc_'+key).value);if(!Number.isInteger(value)||value<0||value>max)throw Error('处理参数超出范围：'+key);parameters[key]=value;}
  if(Object.keys(enabled).length)patch.enabled=enabled;if(Object.keys(parameters).length)patch.parameters=parameters;
  const submitted=new Map([...processDirty].map(id=>[id,$(id).type==='checkbox'?$(id).checked:$(id).value]));
  const owner=flowId;processPending=true;$('processActual').textContent='正在提交，等待FPGA帧边界生效…';
  try{const job=await action('camera',{operation:'process',values:patch});await waitCameraJob(job.id,owner);for(const[id,value]of submitted)if(value===($(id).type==='checkbox'?$(id).checked:$(id).value))processDirty.delete(id);await refreshBoard();}
  finally{processPending=false;renderProcess(state?.board?.camera);}
 });
}
function renderProcess(camera){
 const c=camera?.processing_controls;
 if(!c){
  $('applyProcess').disabled=true;
  $('processView').disabled=true;
  for(const el of document.querySelectorAll('#processFlags input,#processParameters input'))el.disabled=true;
  $('processActual').textContent=processDirty.size?'当前编辑已保留，读取板端后可继续。':'读取板端后可查看处理参数。';
  return;
 }
 $('processView').disabled=false;
 const r=c.requested,e=c.effective,settled=c.requested_revision===c.effective_revision&&c.source_valid&&!c.pending&&!c.fault&&!processPending;
 if(!processDirty.has('processView'))$('processView').value=r.view;
 for(const[key]of processFlags){const el=$('proc_'+key);if(!processDirty.has(el.id))el.checked=r.enabled[key];el.disabled=!(c.supported_mask&(1<<processFlags.findIndex(x=>x[0]===key)));$('proc_effect_'+key).textContent=!c.source_valid?'尚无源帧':e.effective_enabled[key]?(settled?'已生效':'当前帧启用'):'当前帧关闭';}
 for(const[key]of processParameters){const el=$('proc_'+key);el.disabled=false;if(!processDirty.has(el.id)&&document.activeElement!==el)el.value=r.parameters[key];$('proc_actual_'+key).textContent=c.source_valid?`当前帧保留值 ${e.parameters[key]}${e.parameters[key]!==r.parameters[key]?' / 请求值 '+r.parameters[key]:''}`:'尚无源帧实读';}
 $('applyProcess').disabled=processPending||c.pending||!camera.automatic?.config?.frozen;
 if(!processPending)$('processActual').textContent=`请求版本 ${c.requested_revision} / 生效版本 ${c.effective_revision} · 源帧 ${c.effective_source_frame_id}${c.source_valid?'':'（尚无源帧）'} · ${c.fault?'配置被拒绝':c.pending?'等待帧边界':settled?'已生效':'请求尚未同步到有效源帧'} · 显示 ${c.source_valid?e.image_mode:'未确认'}${r.view!==e.view?' / 请求显示 '+r.image_mode:''}${processDirty.size?' · 有未提交编辑':''}. ${c.histogram_curve?.identity?'当前直方图曲线为恒等映射，开关本身不会重算曲线。':''}`;
}
function cameraSaveStatus(message,tone='pending'){const el=$('cameraSaveStatus');el.textContent=message;el.dataset.tone=tone;}
function markCameraEdit(id){cameraDirty.add(id);cameraRevisions.set(id,++cameraEditSerial);cameraSaveError='';}
function scheduleCameraSave(delay=450){clearTimeout(cameraSaveTimer);cameraSaveStatus('等待自动保存');cameraSaveTimer=setTimeout(()=>{saveCameraIfNeeded().catch(()=>{});},delay);}
async function waitCameraJob(id,owner,settleOnly=false){
 const deadline=Date.now()+180000;
 while(Date.now()<deadline){
  const current=await api('/api/state?flow='+owner),job=current.jobs.find(j=>j.id===id);
  if(flowId===owner){state=current;render();}
  if(job&&!['queued','running'].includes(job.state)){if(job.state!=='complete'&&!settleOnly)throw Error(job.message||'参数保存未完成');return job;}
  await new Promise(resolve=>setTimeout(resolve,650));
 }
 throw Error('参数保存等待超时，未重复提交；请先查看当前任务结果');
}
async function saveCameraIfNeeded(){
 clearTimeout(cameraSaveTimer);
 if(cameraSavePromise)return cameraSavePromise;
 if(!cameraDirty.size)return;
 if(cameraSaveError)throw Error(cameraSaveError);
 const saveOwner=flowId;
 cameraSavePromise=(async()=>{
  while(cameraDirty.size&&flowId===saveOwner){
   if(!flowId||!token)throw Error('流程尚未准备好，暂时不能保存参数');
   const owner=flowId;
   const running=(state?.jobs||[]).find(j=>j.page===page&&['camera','readback'].includes(j.kind)&&['queued','running'].includes(j.state));
   if(running)await waitCameraJob(running.id,owner,true);
   if(flowId!==owner)return;
   const edits=[...cameraDirty].map(id=>({id,revision:cameraRevisions.get(id)})),values={},automatic={};
   for(const {id}of edits){const el=$(id);if(!el)throw Error('成像控件不存在，请刷新页面');if(id.startsWith('auto_'))automatic[id.slice(5)]=el.checked;else{if(!el.checkValidity()||el.value==='')throw Error('请输入范围内的有效数值：'+id.slice(4));values[id.slice(4)]=Number(el.value);}}
   if(Object.keys(automatic).length){if(Object.values(automatic).some(Boolean))automatic.frozen=false;else if(Object.keys(automatic).length===5)automatic.frozen=true;values.auto=automatic;}
                                                                           
                                                                
   if('rg' in values||'bg' in values)values.wb_auto=false;
   boardAutoConnect=true;history.replaceState(null,'','/?flow='+owner);
   cameraSaveStatus('正在保存并回读…');
   const job=await api('/api/action/camera',{flow:owner,operation:'patch',values});
   submittedJobs.set(job.id,{autoSave:true});watchedJobs.add(job.id);closedJobs.delete(job.id);
   toast('参数自动保存已提交，等待完成');
   const complete=await waitCameraJob(job.id,owner);
   if(flowId!==owner)return;
   for(const {id,revision}of edits)if(cameraRevisions.get(id)===revision)cameraDirty.delete(id);
   if(complete.result?.camera&&state&&flowId===owner){
    if(state.board?.connected){state.board={...state.board,camera:complete.result.camera};renderBoard(state.board);}
    else await refreshBoard();
   }
   cameraSaveStatus(cameraDirty.size?'继续保存最新修改…':'已自动保存 · 板端已回读',cameraDirty.size?'pending':'success');
  }
 })().catch(error=>{if(flowId===saveOwner){cameraSaveError=error.message;cameraSaveStatus('保存失败 · '+error.message,'error');notify('参数自动保存失败：'+error.message,true);toast('保存失败 · '+error.message,'error');throw error;}}).finally(()=>{cameraSavePromise=null;if(flowId!==saveOwner&&cameraDirty.size)scheduleCameraSave(0);});
 return cameraSavePromise;
}
function editCameraControl(el){
 const value=el.type==='checkbox'?el.checked:el.value;if(cameraInputValues.get(el.id)===value)return;cameraInputValues.set(el.id,value);
 markCameraEdit(el.id);
 if(el.id.startsWith('cam_')){
  const mode={gain_db:'analog_gain',digital_gain:'digital_gain',contrast:'contrast',brightness:'brightness',histogram:'histogram'}[el.id.slice(4)];
  if(mode){$('auto_'+mode).checked=false;cameraInputValues.set('auto_'+mode,false);markCameraEdit('auto_'+mode);}
 }
 scheduleCameraSave(el.type==='checkbox'?0:450);
}
async function setCameraAutomatic(enabled){
 if(enabled){await saveCameraIfNeeded();const owner=flowId,job=await action('camera',{operation:'auto_adjust',values:{}});return waitCameraJob(job.id,owner);}
 const adjusting=(state?.jobs||[]).find(j=>j.page===page&&j.camera_operation==='auto_adjust'&&['queued','running'].includes(j.state));
 if(adjusting)await api('/api/action/stop',{flow:flowId,job:adjusting.id});
 for(const k of ['analog_gain','digital_gain','contrast','brightness','histogram']){$('auto_'+k).checked=false;cameraInputValues.set('auto_'+k,false);markCameraEdit('auto_'+k);}
 scheduleCameraSave(0);return saveCameraIfNeeded();
}
function setupCamera(){
 $('cameraFields').innerHTML=cameraFields.map(([key,label,min,max,value,s])=>`<div><label>${label}<input id="cam_${key}" type="number" min="${min}" max="${max}" step="${s}" value="${value}"></label>${['gain_db','digital_gain','contrast','brightness','histogram'].includes(key)?`<label class="auto-check"><input type="checkbox" id="auto_${key==='gain_db'?'analog_gain':key}">自动</label>`:''}</div>`).join('')+'<div><label>曝光调节</label><button id="autoExposure" class="small" type="button">自动曝光一次</button><small>自动修改上方曝光参数</small></div>';
 $('cameraFields').querySelectorAll('input,select').forEach(el=>{cameraInputValues.set(el.id,el.type==='checkbox'?el.checked:el.value);el.oninput=()=>editCameraControl(el);el.onchange=()=>editCameraControl(el);});bind('autoExposure',async()=>{await saveCameraIfNeeded();await action('camera',{operation:'auto_exposure',values:{}});});
}
async function refreshBoard() {
 if (!boardAutoConnect || boardBusy || !token || !state?.flow) return;
 boardBusy = true;
 try { const board = await api('/api/board'); state.board = board; renderBoard(board); }
 catch (e) {
  state.board = {connected:false, message:e.message};
  renderBoard(state.board);
  notify(e.message, true);
 } finally { boardBusy = false; updateAvailability(); }
}
function markLegalEdit(id){legalDirty.add(id);legalRevisions.set(id,++legalEditSerial);$('legalStatus').textContent='有未提交编辑；点击“热更新合法区”后等待实际回读。';}
function syncLegalSettings(settings,submitted=null){
 const values=[...settings.rectangle,settings.confidence_percent,settings.track_hits];
 if(legalInputSize){values[2]=legalInputSize[0];values[3]=legalInputSize[1];}
 legalFields.forEach((id,i)=>{const el=$(id),sent=submitted?.get(id);if(sent&&sent.revision===(legalRevisions.get(id)||0)&&sent.value===el.value)legalDirty.delete(id);if(legalDirty.has(id)||document.activeElement===el)return;el.value=String(values[i]);if(i<4)rects.legal[i]=values[i];});
 drawLiveSelection();
}
function renderBoard(b){
 renderRuntimeOptions(b);drawLiveSelection();
 renderActionOutput(b);
 if(!b?.connected){
  if(!cameraDirty.size&&!cameraSavePromise)cameraSaveStatus('修改后自动保存');
  renderProcess(null);
  $('boardMetrics').innerHTML='';$('boardResults').innerHTML='';$('actionOutputMetrics').innerHTML='';
  $('cameraActual').textContent='读取板端后显示实际值。';
  $('legalStatus').textContent=legalDirty.size?'连接中断，当前编辑已保留。':'读取板端后显示实际参数。';
  $('connection').textContent=b?.message?'未连接':'尚未读取';$('connectionDot').classList.remove('online');return;}$('connection').textContent='已连接';$('connectionDot').classList.add('online');const c=b.camera,s=c.sensor,a=c.automatic,e=a?.enhancement||{},v={...c.isp.config,exposure_us:Math.round(s.exposure_us||0),gain_db:s.analog_gain_db??s.gain_code*.3,digital_gain:e.gain_q8,histogram:e.strength_q8,wb_auto:String(c.isp.wb_auto)};
 if(c.processing_controls){v.gaussian=c.processing_controls.requested.parameters.gaussian_strength;v.histogram=c.processing_controls.requested.parameters.histogram_strength;}
 for(const[k,value]of Object.entries(v)){const el=$('cam_'+k);if(el&&value!=null&&!cameraDirty.has(el.id)&&document.activeElement!==el)el.value=value;}
 for(const[k,value]of Object.entries(a?.config||{})){const el=$('auto_'+k);if(el&&!cameraDirty.has(el.id))el.checked=Boolean(value);}
 $('cameraFields').querySelectorAll('input,select').forEach(el=>{if(!cameraDirty.has(el.id))cameraInputValues.set(el.id,el.type==='checkbox'?el.checked:el.value);});
 const controls=c.processing_controls,processSettled=!controls||controls.requested_revision===controls.effective_revision&&controls.source_valid&&!controls.pending&&!controls.fault&&!processPending;
 if(!cameraSavePromise&&!cameraDirty.size)cameraSaveStatus(processSettled?'已同步板端回读 · G/H为请求保留值':'已回读 · 公共处理请求尚未生效',processSettled?'success':'pending');
 const processingActual=controls?(controls.source_valid?` · 当前帧 G ${controls.effective.parameters.gaussian_strength}（${controls.effective.effective_enabled.gaussian?'开':'关'}） / H ${controls.effective.parameters.histogram_strength}（${controls.effective.effective_enabled.histogram?'开':'关'}）${processSettled?' · 公共处理已生效':' · 公共处理请求待同步'}`:' · G/H尚无有效源帧实读'):'';
 $('cameraActual').textContent=`实读 ${Number(s.exposure_us||0).toFixed(1)} μs / ${Number(v.gain_db).toFixed(1)} dB · R ${c.isp.config.rg} / B ${c.isp.config.bg} · ${c.isp.wb_auto?'旧白平衡状态待同步，请点击自动调整':'参数已回读'} · 自动调节 ${a?.config?.frozen?'已暂停':'可运行'}${processingActual}`;
 renderProcess(c);
 const r=b.runtime,inf=r.inference||{},metrics=inf.async_inference||{};
 const size=metrics.model_input_size;
 if(r.inference_enabled===false){legalInputSize=null;$('legal0').max=1912;$('legal1').max=1072;for(const id of legalFields.slice(2))$(id).disabled=false;}
 else if(Array.isArray(size)&&size.length===2&&size.every(Number.isInteger)&&size[0]>0&&size[0]<=1920&&size[1]>0&&size[1]<=1080){legalInputSize=size.slice();$('legal0').max=1920-size[0];$('legal1').max=1080-size[1];for(let i=0;i<2;i++){const id=legalFields[i+2];$(id).disabled=true;legalDirty.delete(id);}}
 $('boardMetrics').innerHTML=Object.entries(b.displays||{}).map(([monitor,v])=>stat(v.fresh_fps==null?'—':Number(v.fresh_fps).toFixed(2),monitor+' 新源帧/秒')+stat(v.redraw_fps==null?'—':Number(v.redraw_fps).toFixed(1),monitor+' 重绘/秒')).join('')+stat(Number(inf.effective_result_fps||0).toFixed(1),'推理结果/秒')+stat(Number(inf.fine_model_calls_per_second||0).toFixed(1),'模型调用/秒');
 const titles=new Map((inf.counting?.class_counts||[]).map(x=>[x.label,x.title]));$('boardResults').innerHTML=(r.results||[]).map(o=>{const c=o.classification||{};const label=c.raw_label||o.cached_fine?.label;const accepted=c.accepted??!['uncertain','unreadable'].includes(o.cached_fine?.label);const tracking=o.association_uncertain?'关联不确定':o.confirmed?'已确认':'确认中';return `<span class="result-chip">分类 ${esc(titles.get(label)||o.cached_fine?.title||'未知')}${accepted?'':'（低置信度）'} · ${Number((c.confidence??o.cached_fine?.score??0)*100).toFixed(1)}% · 跟踪 ${tracking} · ID ${esc(o.track_id)}</span>`;}).join('');
 if((b.rect.recorded_monotonic||0)>=legalReadbackAfter){
  syncLegalSettings(b.rect.settings);
  $('legalStatus').textContent=`版本 ${String(b.rect.version).slice(0,8)} · ${b.rect.settings.rectangle.join(', ')} · 置信度 ${b.rect.settings.confidence_percent}% · 确认次数 ${b.rect.settings.track_hits} · ${metrics.input_transform==='native_tiles_v1'?'原生窗口推理':'尚未加载本控制台原生模型'}${legalDirty.size?' · 有未提交编辑':''}`;
 }
}
function actionForm(){return{enabled:$('actionEnabled').checked,target_labels:[...$('actionPoses').querySelectorAll('input:checked')].map(el=>el.value),model_sha256:actionModelSha,delay_ms:Number($('actionDelay').value),pulse_ms:Number($('actionPulse').value)};}
function actionValues(){const values=actionForm();if(!state?.board?.connected||!actionModelSha)throw Error('请先读取板端当前模型');if(values.enabled&&!values.target_labels.length)throw Error('启用前至少选择一种触发姿态');if(!Number.isFinite(values.delay_ms)||values.delay_ms<0||values.delay_ms>5000||!Number.isFinite(values.pulse_ms)||values.pulse_ms<1||values.pulse_ms>1000)throw Error('延迟为0–5000毫秒，脉宽为1–1000毫秒');return values;}
function renderActionOutput(b){
 const output=b?.action_output,connected=Boolean(b?.connected),installed=Boolean(output?.installed),ready=Boolean(output?.ready),model=b?.runtime?.selected_model?.fine_sha256||'',config=output?.config,daemon=output?.daemon,labels=b?.runtime?.inference?.counting?.class_counts||[];
 if(!connected){for(const el of document.querySelectorAll('#actionPoses input,#actionEnabled,#actionDelay,#actionPulse,#applyActionOutput'))el.disabled=true;$('actionOutputStatus').textContent='尚未连接板端；未提交的输出编辑保留。';return;}
 if(model!==actionModelSha){actionModelSha=model;actionDirty=false;}
 if(changed('actionLabels',[model,labels.map(row=>[row.label,row.title])])){
  const selected=new Set(actionDirty?actionForm().target_labels:config?.model_sha256===model?config.target_labels:[]);
  $('actionPoses').innerHTML=labels.map(row=>`<label><input type="checkbox" value="${esc(row.label)}"${selected.has(row.label)?' checked':''}>${esc(row.title)}</label>`).join('');
  $('actionPoses').querySelectorAll('input').forEach(el=>el.onchange=()=>{actionDirty=true;});
 }
 if(!actionDirty){const matching=config?.model_sha256===model;$('actionEnabled').checked=Boolean(matching&&config.enabled);$('actionDelay').value=String(config?.delay_ms??200);$('actionPulse').value=String(config?.pulse_ms??50);$('actionPoses').querySelectorAll('input').forEach(el=>el.checked=Boolean(matching&&config.target_labels.includes(el.value)));}
 $('actionPoses').querySelectorAll('input').forEach(el=>el.disabled=!connected||!ready);
 for(const id of ['actionEnabled','actionDelay','actionPulse'])$(id).disabled=!connected||!ready;
 const actual=b?.rect?.settings?.rectangle;
 $('actionLine').textContent=actual?`统一触发线：原生画面 x = ${actual[0]+actual[2]/2} · 工件检测框中心经过此线时开始计时；橙色线为当前生效位置。`:'读取板端后显示触发线位置。';
 const reasons={disabled:'输出关闭',source_stale:'源帧已停止或过旧',model_mismatch:'当前模型与输出设置不一致，请重新选择姿态并保存',no_targets:'尚未选择触发姿态',active_level_unset:'未设置动作电平',waiting_for_delay:'等待已安排的延迟到达',waiting_for_confirmation:'已有真实过线，等待姿态确认',waiting_for_crossing:'等待所选姿态工件过线'};
 const levelMatches=daemon?.physical_high===(daemon?.active===daemon?.pin?.active_high);
 const level=daemon?.configured?(daemon.physical_high?'高电平3.3 V':'低电平0 V')+' · '+
  (levelMatches?(daemon.active?'当前动作脉冲':'当前空闲'):'状态不一致'):'尚未申请GPIO';
 $('actionOutputStatus').textContent=!connected?'未连接':!installed?'板端尚未安装GPIO输出接口':output.error?output.error:!output.fresh?'GPIO输出状态未更新，请读取板端后重试':`${config?.enabled?'已启用':'已禁用'} · ${level}${daemon.off_reason?' · '+(reasons[daemon.off_reason]||daemon.off_reason):''}${config?.model_sha256!==model?' · 模型已变化，需要重新选择触发姿态':''}${actionDirty?' · 有未提交编辑':''}`;
 if(daemon&&output.fresh){
  const drop=daemon.last_drop,clear=daemon.last_clear;
  const dropReasons={confirmation_expired:'确认超时未发',confirmation_identity_lost:'等待确认时身份不可靠',confirmation_capacity_dropped:'待确认缓存已满',crossing_gap_rejected:'真实过线位置间隔过大',start_deadline_at_receive:'来包迟到',start_deadline_at_tick:'执行迟到',queue_overflow:'待执行队列已满'};
  const clearReasons={external_inactive:'输出撤销',configuration_changed:'输出设置变化',disarm:'收到撤销请求',source_epoch_changed:'源帧会话变化',model_mismatch:'模型不匹配',queue_overflow:'待执行队列已满',disabled:'输出关闭',active_level_unset:'未设置动作电平',source_unavailable:'暂无有效源帧',source_stale:'源帧已停止或过旧'};
  if(drop){
   const deadline=drop.deadline_kind==='confirmation'?'确认截止':'最晚开始时刻',delta=drop.deadline_delta_ms;
   const timing=delta==null?'':` · ${delta>=0?'超过'+deadline:'距'+deadline} ${Math.abs(delta).toFixed(1)} ms`;
   const age=drop.recorded_monotonic==null||daemon.recorded_monotonic==null?'':` · ~${Math.max(0,daemon.recorded_monotonic-drop.recorded_monotonic).toFixed(1)} 秒前`;
   $('actionOutputStatus').textContent+=`. 最近未发：${dropReasons[drop.reason]||drop.reason}${drop.track_id==null?'':' · ID '+drop.track_id}${timing}${age}`;
  }else $('actionOutputStatus').textContent+='. 最近未发：暂无拒绝记录';
  if(clear){
   const age=clear.recorded_monotonic==null||daemon.recorded_monotonic==null?'':` · ~${Math.max(0,daemon.recorded_monotonic-clear.recorded_monotonic).toFixed(1)} 秒前`;
   $('actionOutputStatus').textContent+=`. 最近清队列：${clearReasons[clear.reason]||clear.reason} · 已清待执行 ${clear.pending_cleared??0}，待确认 ${clear.awaiting_cleared??0} 个${clear.was_active?' · 已结束当时低电平':''}${age}`;
  }else $('actionOutputStatus').textContent+='. 最近清队列：暂无记录';
 }
 const counts=daemon?.metrics||{};
 $('actionOutputMetrics').innerHTML=stat(counts.scheduled_events??0,'已安排')+stat(counts.started_events??0,'已开始输出（软件计数）')+stat(`${daemon?.pending??0}/${daemon?.pending_limit??128}`,'待执行队列')+
  stat(`${daemon?.awaiting_confirmation??0}/${daemon?.awaiting_confirmation_limit??512}`,'待确认队列')+stat(counts.recovered_confirmed_events??0,'恢复确认')+stat(counts.confirmation_expired??0,'确认超时未发')+
  stat((counts.expired_event??0)+(counts.late_event??0),'来包迟到')+stat((counts.expired_pulse??0)+(counts.late_pulse??0),'执行迟到')+
  stat((counts.confirmation_identity_lost??0)+(counts.crossing_gap_rejected??0),'身份或位置丢失')+stat(counts.disabled_event??0,'关闭期间过线')+
  stat((counts.invalid_datagram??0)+(counts.queue_overflow_inactive??0)+(counts.confirmation_capacity_dropped??0),'数据或队列错误')+stat(counts.ignored_same_config_saves??0,'重复设置');
 $('actionOutputMetrics').title='待确认与待执行为当前数量，其余为本次GPIO服务累计软件记录；已开始输出不代表电磁阀动作反馈。';
 const button=$('applyActionOutput');if(!button.dataset.pending)button.disabled=!connected||!ready||!model||Boolean(state?.jobs?.some(job=>job.kind==='action_output'&&['queued','running'].includes(job.state)));
}
function legalValues(){const confidence=Number($('confidence').value),hits=Number($('trackHits').value);if(!Number.isFinite(confidence)||confidence<=0||confidence>100||!Number.isInteger(hits)||hits<1||hits>30)throw Error('置信度必须大于0且不超过100，姿态确认次数为1–30次');const preview=state?.board?.runtime?.inference_enabled===false;if(!preview&&!legalInputSize)throw Error('请先读取模型输入尺寸（input dimensions）。');const rectangle=validRect('legal');if(!preview&&(rectangle[2]!==legalInputSize[0]||rectangle[3]!==legalInputSize[1]))throw Error('合法区宽高必须匹配模型输入尺寸（model input）。');return{rectangle,confidence_percent:confidence,track_hits:hits};}
function renderRuntimeOptions(board){
 if(!$('runtimeOptionsStatus'))return;
 const actual=board?.connected?board.runtime_options:null,known=typeof actual?.inference_enabled==='boolean'&&typeof actual?.show_roi==='boolean';
 const request=runtimeOptionsRequest,job=request?.id?state?.jobs?.find(j=>j.id===request.id):null;
 if(job?.state==='error'||job?.state==='cancelled')request.error=job.message||'运行选项未应用，请重试。';
 if(job?.state==='complete'&&job.result?.runtime_options){
  const applied=job.result.runtime_options;
  if(applied.inference_enabled===request.values.inference_enabled&&applied.show_roi===request.values.show_roi){request.complete=true;runtimeOptionsDraft=null;}
 }
 if(job?.state==='complete'&&known&&actual.inference_enabled===request.values.inference_enabled&&actual.show_roi===request.values.show_roi){request.complete=true;runtimeOptionsDraft=null;}
 const running=Boolean(request&&!request.error&&!request.complete),choosing=Boolean(runtimeModelChoice),values=runtimeOptionsDraft||actual;
 if(known){$('runtimeInferenceEnabled').checked=values.inference_enabled;$('runtimeShowRoi').checked=values.show_roi;}
 for(const id of ['runtimeInferenceEnabled','runtimeShowRoi','applyRuntimeOptions'])$(id).disabled=!known||running||choosing;
 $('runtimeModelChoice').hidden=!choosing;$('confirmRuntimeModel').disabled=running;$('cancelRuntimeModel').disabled=running;
 $('retryRuntimeOptions').hidden=!request?.error;$('retryRuntimeOptions').disabled=!known||running;
 const status=$('runtimeOptionsStatus');status.dataset.state=request?.error?'error':running?'running':request?.complete?'success':'idle';
 status.textContent=request?.error?'失败：'+request.error+' 可点击重试。':running?job?.state==='complete'?'应用已完成，正在等待实际状态回读…':'正在应用全局运行选项，等待板端回读…':choosing?'尚未开启推理；请核对下方真实训练记录并明确选择。':request?.complete?'成功：全局运行选项已实际生效并保存。':!known?'尚未读取支持全局选项的板端状态；请先点击“读取板端”。':runtimeOptionsDraft?'有未应用修改；板端仍保持原设置。':`板端实际状态：推理${actual.inference_enabled?'开启':'关闭'} · ROI 框${actual.show_roi?'显示':'隐藏'}。`;
}
async function submitRuntimeOptions(values,selection=null){
 runtimeModelChoice=null;runtimeOptionsRequest={values:clone(values),id:null,error:'',complete:false};renderRuntimeOptions(state?.board);
 try{
  const args={values};if(selection){args.confirm_model=true;args.selection=selection;}
  const result=await action('runtime_options',args);
  runtimeOptionsRequest.id=result.id;renderRuntimeOptions(state?.board);
 }catch(e){runtimeOptionsRequest.error=e.message;renderRuntimeOptions(state?.board);notify(e.message,true);}
}
async function prepareRuntimeOptions(){
 const actual=state?.board?.runtime_options;
 if(!state?.board?.connected||typeof actual?.inference_enabled!=='boolean'||typeof actual?.show_roi!=='boolean')throw Error('请先读取板端实际运行选项。');
 const values={inference_enabled:$('runtimeInferenceEnabled').checked,show_roi:$('runtimeShowRoi').checked};
 runtimeOptionsDraft=values;runtimeOptionsRequest=null;runtimeModelChoice=null;
 if(values.inference_enabled&&!actual.inference_enabled){
  const model=actual.available_model,fields=['run','batch','training_started_beijing','training_completed_beijing','fine_sha256','config_sha256'];
  if(!model||fields.some(key=>!model[key])){runtimeOptionsRequest={values,error:'没有完整的可选模型训练记录，请先显式部署一个模型；不会自动选择最新或历史版本。'};renderRuntimeOptions(state.board);return;}
  runtimeModelChoice={values:clone(values),selection:Object.fromEntries(fields.map(key=>[key,model[key]]))};
  $('runtimeModelDetails').textContent=`场景：${model.scene||model.project_name||'未记录'}\n训练开始（北京时间）：${model.training_started_beijing}\n训练完成（北京时间）：${model.training_completed_beijing}\n训练运行：${model.run}\n采样批次：${model.batch}\nRKNN SHA256：${model.fine_sha256}\n配置 SHA256：${model.config_sha256}`;
  renderRuntimeOptions(state.board);return;
 }
 await submitRuntimeOptions(values);
}
function openSettings(){
 runtimeModelChoice=null;
 if (!runtimeOptionsRequest || runtimeOptionsRequest.error || runtimeOptionsRequest.complete) runtimeOptionsDraft=null;
 const s=state.settings;
 modal('全局选项与连接设置',$('runtimeOptionsTemplate').innerHTML+'<h3>连接与训练</h3>'+[['host','板卡地址'],['username','SSH用户名'],['key','本机SSH私钥文件路径'],['remote','运行目录（连接时优先发现实际目录）'],['steps','GPU训练步数']].map(([k,l])=>`<label>${l}<input id="setting_${k}" value="${esc(s[k])}"></label>`).join('')+'<p>只在本机监听。私钥内容不上传到网页。</p>','保存连接与训练设置',async()=>{const values={};for(const k of ['host','username','key','remote','steps'])values[k]=$('setting_'+k).value;await action('settings',values);toast('设置已保存');});
 for(const id of ['runtimeInferenceEnabled','runtimeShowRoi'])$(id).onchange=()=>{runtimeOptionsDraft={inference_enabled:$('runtimeInferenceEnabled').checked,show_roi:$('runtimeShowRoi').checked};runtimeOptionsRequest=null;runtimeModelChoice=null;renderRuntimeOptions(state.board);};
 bind('applyRuntimeOptions',prepareRuntimeOptions);bind('retryRuntimeOptions',prepareRuntimeOptions);
 bind('confirmRuntimeModel',()=>{const choice=runtimeModelChoice;return choice?submitRuntimeOptions(choice.values,choice.selection):null;});
 bind('cancelRuntimeModel',()=>{runtimeModelChoice=null;runtimeOptionsDraft.inference_enabled=state.board.runtime_options.inference_enabled;renderRuntimeOptions(state.board);});
 renderRuntimeOptions(state.board);
}
function updateAvailability(){
 renderRuntimeOptions(state?.board);
 for(const n of [1,4]){const button=$('getPreview'+n);if(!button.dataset.pending)button.disabled=!token||!flowId||previewBusy||Boolean(state?.flow?.runtime_isolation&&state.flow.runtime_isolation.state!=='ready');}
 const p=state?.flow;if(!p)return;const disable=(id,condition,reason)=>{const b=$(id);if(!b||b.dataset.pending)return;b.disabled=condition;b.title=condition?reason:'';};
 disable('capture15',!p.profile,'请先在第一步固定成像参数。');disable('captureMore',!p.profile,'请先在第一步固定成像参数。');disable('assist',!helperChoice||p.helper_selection?.key!==helperChoice,'请先确认本次使用的画框助手。');
 for(const id of ['saveDraft','confirmFrame','excludeFrame'])disable(id,!frameId,'请先选择图像');disable('deleteBox',!selectedBox,'请先选中元件框');disable('undoBox',!undo.length,'尚无可撤销操作');
 disable('testModel',!selectedRun()?.manifest?.training_completed_beijing||!selectedRun()?.int8_test_available,'请明确选择已生成INT8 RKNN的训练版本；不回退PT');disable('convertModel',!selectedRun()?.manifest?.training_completed_beijing,'请选择训练版本。');disable('downloadModel',!selectedRun()?.convert_available,'请先完成RKNN转换');disable('prepareDeploy',!selectedRun()?.convert_available,'请先选择已转换的模型');disable('feedback',!testId,'请先选择测试组');disable('reviewTest',!testId,'请先选择测试组');
 disable('applyActionOutput',!state.board?.connected||!state.board?.action_output?.ready||!actionModelSha||state.jobs.some(job=>job.kind==='action_output'&&['queued','running'].includes(job.state)),'请读取已就绪的 TX GPIO0_C1 输出接口，或等待上一笔设置应用完成');
 const isolation=p.runtime_isolation,waiting=isolation&&isolation.state!=='ready';
 for(const el of document.querySelectorAll('#cameraFields input'))el.disabled=Boolean(waiting);
 for(const id of ['autoOn','autoOff','autoExposure'])disable(id,Boolean(waiting),'请先完成纯预览切换。');
 for(const id of ['freeze','applyLegal'])
  disable(id,Boolean(waiting)||!state.board?.connected,waiting?'先完成或重试进入纯预览。':'请先读取板端实际状态。');
 if(waiting)for(const id of ['capture15','captureMore','testModel','prepareDeploy','applyProcess','applyActionOutput'])disable(id,true,'先完成或重试进入纯预览。');
 if(isolation&&isolation.mode!=='inference'){disable('applyActionOutput',true,'先明确选择并部署本流程模型。');$('actionEnabled').disabled=true;}
}
bind('newFlow', newFlow);
bind('welcomeNew', newFlow);
bind('welcomeHistory', () => $('historyOpen').click());
bind('chooseRun', () => { setStep(3); $('runSelect').focus(); });
bind('retryPreview',()=>action('preview'));
bind('historyOpen',()=>modal('打开已有流程',state?.flows?.length?state.flows.map(p=>`<button type="button" class="flow-choice" data-flow="${p.id}">${esc(p.name)}<small>${esc(p.updated_at)} · 步骤 ${p.stage}</small></button>`).join(''):'<p>还没有历史流程。</p>','',null));
$('modalBody').addEventListener('click',async e=>{const b=e.target.closest('[data-flow]');if(!b)return;try{await saveDraftIfNeeded();await switchFlow(b.dataset.flow);$('modal').close();}catch(err){$('modalError').textContent=err.message;$('modalError').hidden=false;}});
bind('settingsOpen',openSettings);
bind('refreshBoard',async()=>{boardAutoConnect=true;history.replaceState(null,'','/?flow='+flowId);await refreshBoard();});
bind('getPreview1',()=>refreshPreview(1));
bind('getPreview4',()=>refreshPreview(4));
bind('centerRect',()=>{rects.train=[704,396,512,288];syncRect('train');});
bind('autoOn',()=>setCameraAutomatic(true));
bind('autoOff',()=>setCameraAutomatic(false));
bind('freeze',async()=>{
 if(processDirty.size||processPending)throw Error('公共处理还有未提交修改，请先应用并检查画面');
 await saveCameraIfNeeded();
 if((state.jobs||[]).some(j=>['camera','readback','freeze'].includes(j.kind)&&['queued','running'].includes(j.state)))throw Error('成像操作尚未完成，请等完成提示并检查画面后再固定');
 if(!state.board?.connected)throw Error('请先读取板端并检查画面');
 if(state.board.camera.isp.wb_auto!==false)throw Error('板端还是旧白平衡状态，请先点击“自动调整”或修改R/B，等待保存并检查画面后再固定。');
 return action('freeze',{rectangle:rects.train.slice()},'freeze');
});
bind('capture15',async()=>{await saveDraftIfNeeded();await action('capture',{append:false},'capture');});
bind('captureMore',async()=>{await saveDraftIfNeeded();await action('capture',{append:true},'capture');});
bind('assist',async()=>{await saveDraftIfNeeded();await action('assist');});
bind('selectHelper',()=>{const h=state.helpers.find(h=>h.key===$('helperSelect').value);if(!h?.trained_at)throw Error('请选择有训练时间记录的画框助手。');return action('helper_select',{key:h.key,trained_at:h.trained_at,sha256:h.sha256,confirmed:true}).then(()=>{helperChoice=h.key;renderHelpers();toast('本次画框助手已确认。');});});
bind('trainHelper',async()=>{await saveDraftIfNeeded();await action('helper_train');});$('helperSelect').onchange=()=>{helperChoice='';renderHelpers();updateAvailability();};
bind('addPose',()=>action('label',{operation:'add'}));
bind('undoBox',()=>{if(undo.length){draft=undo.pop();dirty=true;selectedBox=null;renderBoxes();}});
bind('deleteBox',()=>{if(!selectedBox)return;remember();draft=draft.filter(o=>o.id!==selectedBox);selectedBox=null;dirty=true;renderBoxes();});
bind('saveDraft',()=>saveAnnotation(false));
bind('confirmFrame',async()=>{await saveAnnotation(true);const rows=currentFrames(),index=rows.findIndex(f=>f.id===frameId),next=[...rows.slice(index+1),...rows.slice(0,index)].find(f=>!f.reviewed&&!f.excluded);if(next){frameId=next.id;frameRevision=-1;selectedBox=null;undo=[];renderAnnotation();fingerprints.frames='';renderFrames();}});
bind('excludeFrame',async()=>{await saveDraftIfNeeded();await action('exclude',{frame:frameId,excluded:!selectedFrame().excluded});});
bind('toTrain',async()=>{await saveDraftIfNeeded();setStep(3);});bind('toDeploy',()=>setStep(4));
bind('trainModel',async()=>{await saveDraftIfNeeded();await action('train');});
$('runSelect').onchange=()=>{runId=$('runSelect').value;testId='';fingerprints.tests='';renderRuns();renderTests();updateAvailability();};
$('testSelect').onchange=()=>{testId=$('testSelect').value;fingerprints.tests='';renderTests();};
bind('convertModel',()=>action('convert',{run:runId}));
bind('downloadModel',()=>{const name=selectedRun()?.model_file;if(!name)throw Error('请先完成并校验RKNN转换');const link=document.createElement('a');link.href=`/api/download?flow=${flowId}&run=${runId}&name=${encodeURIComponent(name)}`;link.download=name;link.click();});
bind('testModel',()=>action('test',{...runArgs(),int8_sha256:selectedRun()?.manifest?.output_hashes?.['detector_int8.rknn']},'test'));
bind('feedback',async()=>{const frames=[...$('testGrid').querySelectorAll('input:checked')].map(e=>e.dataset.testframe);if(!frames.length)throw Error('请先勾选需要纠错的图像');await action('feedback',{test:testId,frames});frameId=frames[0];frameRevision=-1;setStep(2);toast('请人工修正这些图像的框与姿态，再重新训练');});
bind('reviewTest',()=>action('test_review',{test:testId,confirmed:true}).then(()=>toast('已记录人工检查')));
bind('prepareDeploy',async()=>{const result=await action('prepare_deploy',runArgs());const s=result.selection;modal('这次使用这个时间训练的模型？',`<p>切换本次明确选择的模型，并将断电自启动更新为此版本；保留现场设置。新流程的电磁阀保持关闭，部署完成后再单独启用。</p><pre class="model-details">${esc('场景：'+s.project_name+'\n训练开始（北京时间）：'+s.training_started_beijing+'\n训练完成（北京时间）：'+s.training_completed_beijing+'\n采样批次：'+s.batch+'\nRKNN SHA256：'+s.fine_sha256+'\n配置 SHA256：'+s.config_sha256)}</pre><p>切换前会校验实际运行版本和文件白名单；需要重启时沿用当前服务入口。运行程序更新与模型切换分开执行。</p>`,'明确选择并切换模型',()=>action('deploy',{nonce:result.nonce,confirmed:true},'deploy'));});
bind('applyLegal',()=>action('legal',{values:legalValues()},'legal'));
bind('applyActionOutput',()=>action('action_output',{values:actionValues()}));
for(const id of ['actionEnabled','actionDelay','actionPulse'])$(id).oninput=()=>{actionDirty=true;};
for(const id of ['confidence','trackHits'])$(id).oninput=()=>markLegalEdit(id);
bind('stopJob',()=>{const last=state.jobs.filter(j=>['queued','running'].includes(j.state)).at(-1);return last?action('stop',{job:last.id}):null;});
document.addEventListener('keydown',e=>{if(step!==2||['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)||$('modal').open)return;if(e.key==='Delete'&&selectedBox){e.preventDefault();$('deleteBox').click();}if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();$('undoBox').click();}});
window.addEventListener('beforeunload',e=>{if(dirty||cameraDirty.size||cameraSavePromise||legalDirty.size||processDirty.size||processPending||actionDirty){e.preventDefault();e.returnValue='';}});
async function init() {
 for (const n of [1,4]) $('liveMount'+n).append($('snapshotStandby').content.cloneNode(true));
 setupRect('train', 'trainRect');
 setupRect('legal', 'legalRect');
 setupCamera();
 setupProcess();
 try {
  await bootstrap();
  if (!flowId) {
   state = await api('/api/state');
   flowId = state.flows[0]?.id || null;
  }
  if (flowId) { await switchFlow(flowId); refreshBoard(); }
  else render();
 } catch (e) {
  flowId = null;
  await refresh();
  render();
  notify(e.message, true);
 }
 async function pollState() {
  if (!document.hidden) await refresh();
  const busy = state?.jobs?.some(job => job.state === 'queued' || job.state === 'running');
  setTimeout(pollState, busy ? 700 : 3000);
 }
 setTimeout(pollState, 3000);
 setInterval(() => { if (!document.hidden && flowId && [1,4].includes(step)) refreshBoard(); }, 3000);
}
init();
