                                                                               
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../console_web/app.js'),'utf8').replace(/\ninit\(\);\s*$/,'\n');
const elements=new Map();
function element(id){
 if(!elements.has(id))elements.set(id,{id,value:'',disabled:false,hidden:false,checked:false,dataset:{},
  addEventListener(){},querySelectorAll(){return[];},classList:{toggle(){},add(){},remove(){}}});
 return elements.get(id);
}
const document={getElementById:element,querySelectorAll(){return[];},addEventListener(){},activeElement:null};
const context=vm.createContext({document,location:{search:'?connect=manual'},URLSearchParams,structuredClone,
 crypto:{randomUUID:()=> 'offline'},history:{replaceState(){}},window:{addEventListener(){}},
 setTimeout(){},clearTimeout(){},setInterval(){},console,fetch(){throw Error('Unexpected HTTP call');}});
vm.runInContext(source,context);const run=code=>vm.runInContext(code,context);
run("state={flow:{frames:[],runs:[],tests:[],profile:null,helper_selection:null,runtime_isolation:{state:'pending',mode:'preview'}},jobs:[],board:{connected:true,runtime:{inference_enabled:false},action_output:{ready:true}}};token='fixture';flowId='fixture'");
element('confidence').value='85';element('trackHits').value='3';
run("legalInputSize=null;rects.legal=[0,792,512,288]");
assert.deepEqual(Array.from(run('legalValues().rectangle')),[0,792,512,288]);
run('rects.legal=[0,0,720,720]');assert.equal(run('legalValues().rectangle[2]'),720);
run('rects.legal=[0,0,721,720]');assert.throws(()=>run('legalValues()'),/518,400/);
run('state.board.runtime.inference_enabled=true;legalInputSize=[512,288];rects.legal=[0,0,600,300]');
assert.throws(()=>run('legalValues()'),/model input/);
run('legalInputSize=null');assert.throws(()=>run('legalValues()'),/input dimensions/);
run('updateAvailability()');
assert.equal(element('freeze').disabled,true);assert.equal(element('applyLegal').disabled,true);
assert.equal(element('getPreview1').disabled,true);assert.equal(element('applyActionOutput').disabled,true);
run("state.flow.runtime_isolation.state='ready';state.board.runtime.inference_enabled=false;updateAvailability();renderRuntimeIsolation()");
assert.equal(element('freeze').disabled,false);assert.equal(element('applyLegal').disabled,false);
assert.equal(element('getPreview1').disabled,false);assert.equal(element('applyActionOutput').disabled,true);
assert.match(element('flowRuntimeStatus').textContent,/不加载历史模型/);
run("state.flow.runtime_isolation={state:'error',mode:'preview',message:'fixture failed'};renderRuntimeIsolation()");
assert.equal(element('retryPreview').hidden,false);assert.match(element('flowRuntimeStatus').textContent,/fixture failed/);
console.log('PASS preview native ROI bounds, model-size guard, pending/ready controls and failure retry');
