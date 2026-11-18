                                                                                  
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../console_web/app.js'),'utf8').replace(/\ninit\(\);\s*$/,'\n');
const html=fs.readFileSync(path.join(__dirname,'../console_web/index.html'),'utf8');
for(const id of ['runtimeOptionsTemplate','runtimeInferenceEnabled','runtimeShowRoi','applyRuntimeOptions','retryRuntimeOptions','runtimeModelDetails','confirmRuntimeModel','cancelRuntimeModel'])assert.ok(html.includes(`id="${id}"`),id);

function fixture(model=null,inference=false){
 const elements=new Map();
 function element(id){
  if(!elements.has(id))elements.set(id,{id,value:'',disabled:false,hidden:false,checked:false,dataset:{},handlers:{},children:[],innerHTML:'',
   addEventListener(name,handler){this.handlers[name]=handler;},querySelectorAll(){return[];},querySelector(){return null;},
   classList:{toggle(){},add(){},remove(){}},showModal(){this.open=true;},close(){this.open=false;},
   replaceChildren(){this.children=[];},append(child){this.children.push(child);},setAttribute(name,value){this[name]=value;}});
  return elements.get(id);
 }
 element('runtimeOptionsTemplate').innerHTML=html.match(/<template id="runtimeOptionsTemplate">([\s\S]*?)<\/template>/)[1];
 const document={getElementById:element,querySelectorAll(){return[];},addEventListener(){},activeElement:null,
  createElementNS(_namespace,name){return{name,attrs:{},setAttribute(k,v){this.attrs[k]=v;}};}};
 const context=vm.createContext({document,location:{search:'?connect=manual'},URLSearchParams,structuredClone,
  crypto:{randomUUID:()=> 'offline'},history:{replaceState(){}},window:{addEventListener(){}},
  setTimeout(){},clearTimeout(){},setInterval(){},console,fetch(){throw Error('Unexpected HTTP call');}});
 vm.runInContext(source,context);const run=code=>vm.runInContext(code,context);
 context.options={inference_enabled:inference,show_roi:true,available_model:model};context.requests=[];
 run("state={settings:{host:'fixture',username:'fixture',key:'fixture',remote:'/fixture',steps:100},flow:{frames:[],runs:[],tests:[],labels:[]},jobs:[],board:{connected:true,runtime_options:options}};flowId='fixture';token='fixture';action=async(name,args)=>{requests.push({name,args});return{id:'runtime-job',kind:'runtime_options',state:'queued'};};openSettings()");
 return{context,run,element,requests:context.requests};
}
const model={run:'selected-run',batch:'selected-batch',training_started_beijing:'2026-10-31T10:00:00+08:00',
 training_completed_beijing:'2026-10-31T10:01:00+08:00',fine_sha256:'a'.repeat(64),config_sha256:'b'.repeat(64),scene:'fixture scene'};

(async()=>{
 const off=fixture();off.element('runtimeShowRoi').checked=false;await off.run('prepareRuntimeOptions()');
 assert.equal(off.requests.length,1);assert.equal(off.requests[0].name,'runtime_options');
 assert.equal(off.requests[0].args.values.show_roi,false);assert.equal(off.requests[0].args.values.inference_enabled,false);
 assert.equal(off.requests[0].args.confirm_model,undefined);assert.equal(off.element('runtimeOptionsStatus').dataset.state,'running');
 off.run("state.jobs=[{id:'runtime-job',state:'error',message:'fixture failure'}];renderRuntimeOptions(state.board)");
 assert.equal(off.element('retryRuntimeOptions').hidden,false);assert.match(off.element('runtimeOptionsStatus').textContent,/fixture failure/);
 console.log('PASS ROI/off changes call backend without model confirmation; running/error/retry states');

 const missing=fixture();missing.element('runtimeInferenceEnabled').checked=true;await missing.run('prepareRuntimeOptions()');
 assert.equal(missing.requests.length,0);assert.match(missing.element('runtimeOptionsStatus').textContent,/显式部署/);
 console.log('PASS missing model cannot select or enable one automatically');

 const cancel=fixture(model);cancel.element('runtimeInferenceEnabled').checked=true;await cancel.run('prepareRuntimeOptions()');
 assert.equal(cancel.requests.length,0);assert.equal(cancel.element('runtimeModelChoice').hidden,false);
 assert.ok(cancel.element('runtimeModelDetails').textContent.includes(model.training_started_beijing));
 assert.ok(cancel.element('runtimeModelDetails').textContent.includes(model.fine_sha256));
 assert.ok(cancel.element('runtimeModelDetails').textContent.includes(model.scene));
 await cancel.element('cancelRuntimeModel').handlers.click({currentTarget:cancel.element('cancelRuntimeModel')});
 assert.equal(cancel.requests.length,0);assert.equal(cancel.element('runtimeInferenceEnabled').checked,false);
 console.log('PASS off-to-on displays the actual record and cancellation makes no enabling request');

 const enabled=fixture(model);enabled.element('runtimeInferenceEnabled').checked=true;await enabled.run('prepareRuntimeOptions()');
 await enabled.element('confirmRuntimeModel').handlers.click({currentTarget:enabled.element('confirmRuntimeModel')});
 assert.equal(enabled.requests.length,1);assert.equal(enabled.requests[0].args.confirm_model,true);
 assert.deepEqual(Object.keys(enabled.requests[0].args.selection).sort(),['run','batch','training_started_beijing','training_completed_beijing','fine_sha256','config_sha256'].sort());
 for(const key of Object.keys(enabled.requests[0].args.selection))assert.equal(enabled.requests[0].args.selection[key],model[key]);
 enabled.run("state.board.runtime_options.inference_enabled=true;state.jobs=[{id:'runtime-job',state:'complete',result:{runtime_options:state.board.runtime_options}}];renderRuntimeOptions(state.board)");
 assert.equal(enabled.element('runtimeOptionsStatus').dataset.state,'success');assert.equal(enabled.element('runtimeInferenceEnabled').checked,true);
 assert.equal(enabled.element('applyRuntimeOptions').disabled,false);
 console.log('PASS explicit selection sends exact confirmation fields and success requires actual acknowledgment');

 const on=fixture(model,true);on.element('runtimeShowRoi').checked=false;await on.run('prepareRuntimeOptions()');
 assert.equal(on.requests.length,1);assert.equal(on.requests[0].args.confirm_model,undefined);
 console.log('PASS keeping inference on while changing ROI visibility does not reload or reconfirm a model');

 const overlay=fixture(model);const svg={children:[],replaceChildren(){this.children=[];},append(child){this.children.push(child);}};
 overlay.context.svg=svg;
 overlay.run("step=1;liveMeta={camera_area:[0,0,1920,1080]};liveSvg=()=>svg;state.board.runtime_options.show_roi=false;drawLiveSelection()");
 assert.equal(svg.children.length,0);
 overlay.run('state.board.runtime_options.show_roi=true;drawLiveSelection()');
 assert.equal(svg.children[0].attrs.class,'svg-selection');
 overlay.run("state.board.runtime_options.show_roi=false;drawBoxes(svg,[{bbox:[10,10,50,50],label:'A',confidence:.9}],[512,288])");
 assert.ok(svg.children.some(child=>child.attrs.class==='svg-box'));
 console.log('PASS show_roi hides the live ROI overlay while detection/annotation box drawing remains available');
})().catch(error=>{console.error(error);process.exitCode=1;});
