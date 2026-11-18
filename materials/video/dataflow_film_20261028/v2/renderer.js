/* One persistent technical tree, one continuous camera path. No slide scenes. */
(function(root){
'use strict';
const W=1920,H=1080,DURATION=130;
const C={bg:'#05090f',ink:'#e5f0f4',muted:'#7898aa',line:'#294953',image:'#5ce4d0',model:'#b6a0ff',control:'#e3bc81'};
let staticLayers=null;
function prepareStaticLayers(createCanvas,width,height){
 const base=createCanvas(width,height),vignette=createCanvas(width,height),b=base.getContext('2d'),v=vignette.getContext('2d');
 b.scale(width/W,height/H);b.fillStyle=C.bg;b.fillRect(0,0,W,H);
 const glow=b.createRadialGradient(1090,530,10,1090,530,1000);glow.addColorStop(0,'#10232b');glow.addColorStop(.55,'#09121c');glow.addColorStop(1,C.bg);b.fillStyle=glow;b.fillRect(0,0,W,H);
 v.scale(width/W,height/H);const gr=v.createRadialGradient(960,540,440,960,540,1090);gr.addColorStop(0,'#05090f00');gr.addColorStop(1,'#05090fb0');v.fillStyle=gr;v.fillRect(0,0,W,H);
 staticLayers={base,vignette,width,height};
}
const nodes=[],edges=[],byId={};
function N(id,parent,x,y,title,detail='',branch='image',rank=2,glyph=''){
 const n={id,x,y,title,detail,branch,rank,glyph,index:nodes.length};nodes.push(n);byId[id]=n;if(parent)edges.push({a:byId[parent],b:n});return n;
}
N('sensor',null,0,0,'IMX415','1920 × 1080','image',0,'sensor');
N('sensor_geometry','sensor',520,-420,'全视野坐标','1920 × 1080 / x y');
N('source','sensor',520,390,'source_frame_id','逐帧身份');
N('isp','sensor',1000,0,'FPGA · ISP','公共像素流水','image',0,'isp');
N('exp','isp',1450,-470,'曝光 / 增益','sensor control');
N('wb','isp',1450,-270,'白平衡','R / B gain');
N('enhance','isp',1850,0,'RGB 增强','直方图映射','image',1,'hist');
N('lanes','enhance',2200,370,'4 pixels / clock','四 lane 并行');
N('foreground','enhance',2650,0,'全分辨率前景','背景亮度 × 1/4','image',1,'pixels');
N('sobel','foreground',3050,-450,'Sobel','边缘标黄','image',2,'kernel');
N('morph','foreground',3050,470,'3 × 3 形态学','opening 默认','image',2,'kernel');
N('morph_modes','morph',3590,700,'erosion · dilation','opening · closing','image',3);
N('split','foreground',3700,0,'公共 RGB','三路分流','image',0,'fork');

N('ch0','split',4350,-1800,'CH 0 · 预览','RAW / JPEG','image',0,'sensor');
N('raw','ch0',5050,-1800,'RGB888','6,220,800 B / frame','image',1,'pixels');
N('ddr0','raw',5750,-1800,'DDR · 32 MiB','preview ring','image',1,'memory');
N('dma0','ddr0',6450,-1800,'DMA · 2 × 6 MiB','coherent slots','image',1,'memory');
N('preview_rx','dma0',7150,-1800,'RK · preview','source ID / CRC','image',1,'identity');
N('rga','preview_rx',7900,-1800,'RGA → X11','display buffer','image',1,'display');
N('hdmi','rga',8650,-1800,'HDMI','新源帧去重','image',0,'display');
N('redraw','hdmi',9200,-2110,'submitted / completed','重绘率单列','image',2);
N('screen_snap','hdmi',9200,-1510,'Web 快照','显示支路','image',2);
N('jpeg','ch0',5000,-2380,'JPEG · 可选','3 × 640 × 1080','image',1,'strips');
N('jpeg_ring','jpeg',5770,-2380,'3 × 4 MiB','复用预览 12 MiB','image',2,'memory');
N('decode3','jpeg_ring',6550,-2380,'3 路解码','原生竖条','image',2,'strips');
N('same_id','decode3',7350,-2380,'同 source ID 合帧','1920 × 1080','image',1,'identity');
edges.push({a:byId.same_id,b:byId.rga,secondary:true});

N('ch1','split',4350,0,'CH 1 · 推理','continuous native ROI','image',0,'crop');
N('ddr1','ch1',5050,0,'DDR · 8 MiB','独立 ROI ring','image',1,'memory');
N('dma1','ddr1',5750,0,'DMA · 2 MiB','0xC00000–0xE00000','image',1,'memory');
N('limit1','ddr1',5300,420,'area ≤ 518,400 px','RGB ≤ 1,555,200 B','image',2);
N('sched','dma1',6550,0,'有界调度','OrderedWholeFrames','image',1,'queue');
N('capacity','sched',6800,-480,'capacity = 3 / 4','默认 3','image',2,'memory');
N('pending','sched',7250,-480,'pending 可替换','running / completed 保留','image',3);
N('npu','sched',7400,0,'RK3576 · NPU','2 workers','image',0,'npu');
N('core0','npu',7800,-440,'CORE 0','RKNN context','image',2,'npu');
N('core1','npu',7800,440,'CORE 1','RKNN context','image',2,'npu');
N('tensor','npu',7050,550,'uint8 · NHWC','[1, 288, 512, 3]¹','image',2);
N('native','tensor',7500,820,'native_tiles_v1','原生裁剪¹','image',3);
N('post','npu',8350,0,'解码 / 原点映射','bbox + (x, y)','image',1,'boxes');
N('head_io','post',8700,-480,'5 + C · 72 × 128','stride 4¹','image',2);
N('publish','post',9200,0,'按源帧顺序发布','完整结果 / serial collector','image',1,'queue');
N('track','publish',10100,0,'ID / 跟踪','独立跟踪进程','image',1,'track');
N('geom','track',10450,-450,'几何 / 外观','identity lifetime','image',2);
N('pose','track',10850,300,'姿态共识','≥3 票 · agreement ≥0.75','image',1,'votes');
N('margin','pose',11400,570,'vote margin ≥0.4','独立源帧','image',3);
N('ledger','track',10950,-180,'计数账本','passage_ledger','image',1,'bars');
N('gpio','ledger',11700,-600,'GPIO','事件输出','image',0,'pulse');
N('chart','ledger',11700,-180,'图表 / 显示缓存','异步 UI','image',2,'bars');
N('evidence','pose',11700,250,'采证 / 存储','source + ROI + version','image',2,'identity');

N('transport','split',4700,-900,'共享 DDR / PCIe','preview priority','control',1,'memory');
N('cdc','transport',5350,-1200,'CDC FIFO × 3','512 entries / queue','control',2,'queue');
N('pool','transport',5350,-760,'16 MiB coherent','16 × 1 MiB SGM','control',1,'memory');
N('meta','pool',6060,-760,'Protocol 8 · 96 B','24 × u32 metadata','control',1,'metadata');
N('identity','meta',6650,-1030,'source / request / epoch','processing rev / enhance rev','control',2,'identity');
N('commit','meta',6650,-650,'invalidate → payload → commit','CRC + 96 B recheck','control',2,'queue');
N('stop','pool',5900,-370,'STOP barrier','3 channels drained','control',3);

N('ch2','split',4350,1850,'CH 2 · 采图','one-shot request','image',0,'crop');
N('ddr2','ch2',5050,1850,'DDR · 8 MiB','独立采图 ring','image',1,'memory');
N('dma2','ddr2',5750,1850,'DMA · 2 MiB','0xE00000–0x1000000','image',1,'memory');
N('req2','dma2',6000,1370,'new request_id','每次独立提交','image',2);
N('pc','dma2',6500,1850,'PC · VisionTrainer','native RGB / SHA256','model',0,'data');
N('freeze','pc',7000,1390,'冻结场景','ISP / ROI / revision','model',2,'identity');
N('dataset','pc',7300,1850,'15 张采集','source ID 去重','model',1,'data');
N('annotate','dataset',8100,1850,'框 / 姿态标注','人工确认','model',1,'boxes');
N('split_data','annotate',8450,1330,'按源图划分','train / validation','model',2);
N('crop512','annotate',8450,2310,'512 × 288 裁剪','stride 256 × 144','model',2,'crop');
N('gpu','annotate',9050,1850,'GPU · CUDA','random initialization','model',0,'npu');
N('augment','gpu',9600,1270,'augmentation = off','原图不缩放 / 不填充','model',2);
N('net','gpu',9750,2400,'ResistorNet','MobileNetV2 + FPN','model',1,'network');
N('backbone','net',10450,2080,'features[:14]','weights = None','model',2,'network');
N('tap','backbone',11050,1810,'tap 3 / 6 / 13','特征图 ↑2 / ↑4 · nearest','model',3);
N('concat','net',10450,2650,'concat · 152 ch','stride 4','model',2,'network');
N('conv1','concat',11100,2670,'1×1 · 152 → 48','ReLU','model',3);
N('dw5','conv1',11700,2670,'DW 5×5 · 48','ReLU','model',3,'kernel');
N('conv2','dw5',12300,2670,'1×1 · 48 → 64','ReLU','model',3);
N('dw3','conv2',12900,2670,'DW 3×3 · 64','ReLU','model',3,'kernel');
N('head','dw3',13500,2670,'1×1 · 64 → 5+C','detection / pose','model',2,'network');
N('sigmoid','head',14100,2410,'sigmoid · 5','objectness / w h / dx dy','model',3);
N('logits','head',14100,2940,'logits · C','pose classes','model',3);
N('loss','gpu',9750,3350,'LOSS','AdamW','model',1,'bars');
N('loss_obj','loss',10500,3110,'L object','focal','model',2);
N('loss_wh','loss',10500,3350,'6 × L size','smooth L1 / log ratio','model',2);
N('loss_xy','loss',10500,3590,'1.5 × L offset','L1','model',2);
N('loss_pose','loss',10500,3830,'L pose','cross entropy','model',2);
N('test','gpu',10100,1100,'10 张新图测试','PC GPU / 人工核对','model',1,'data');
N('export','test',10950,1100,'ONNX → RKNN','opset 12','model',1,'model');
N('select','export',11750,1100,'明确选型','北京时间 / 场景 / SHA256','model',1,'identity');
N('deploy','select',12600,1100,'MODEL ONLY','rknn + config.json','model',0,'model');
N('runtime','deploy',13200,820,'runtime 保留','现场参数保留','model',2);
N('control','pc',7400,3430,'控制面','SSH / SFTP · 实读回写','control',1,'pulse');
N('ctrl_isp','control',8150,3100,'曝光 / 白平衡 / 增强','shared processing revision','control',2);
N('ctrl_roi','control',8150,3400,'ROI x / y','w / h = model input','control',2);
N('ctrl_threshold','control',8150,3700,'置信度 / 确认次数','热更新','control',2);
edges.push({a:byId.deploy,b:byId.npu,secondary:true,modelReturn:true});
edges.push({a:byId.control,b:byId.isp,secondary:true,controlReturn:true});

const cameraKeys=[
 [0,0,90,1.65,-.045],[4,450,-20,1.25,-.025],[10,1450,-80,.95,.018],
 [17,2700,80,.78,.02],[23,3950,0,.245,-.016],[29,5450,-2090,.8,.018],
 [35,7550,-2110,.75,-.025],[41,6200,-850,.78,.012],[47,7000,0,.8,.025],
 [53,8350,0,.84,-.018],[59,10300,-30,.78,.02],[65,11500,0,.78,-.015],
 [71,6100,1850,.8,.025],[77,8050,1850,.82,-.022],[83,9900,2380,.78,.018],[85,10650,2240,.8,.015],
 [88,11550,2670,1.0,.01],[92,13600,2650,1.02,-.018],
 [97,10000,3530,.84,.012],[102,10900,1100,.8,-.02],[107,12400,1100,.88,.018],
 [111,12600,1100,.83,.01],[116,8100,3410,.85,-.018],[120,7200,2000,.43,.015],
 [125,7400,100,.38,-.01],[130,7100,500,.115,0]
];
// Compact the horizontal spacing while preserving the semantic branch layout.
for(const node of nodes)node.x*=.85;
for(const key of cameraKeys)key[1]*=.85;
const chapters=[
 {start:0,end:10,name:'采集 / ISP'},{start:10,end:23,name:'公共图像流水'},
 {start:23,end:35,name:'三路 / 预览'},{start:35,end:47,name:'传输 / 协议'},
 {start:47,end:59,name:'NPU / 调度'},{start:59,end:71,name:'跟踪 / 输出'},
 {start:71,end:83,name:'采图 / 标注'},{start:83,end:97,name:'网络结构'},
 {start:97,end:111,name:'训练 / 选型'},{start:111,end:121,name:'控制 / 回传'},{start:121,end:130,name:'系统全树'}
];
const clamp=(n,a=0,b=1)=>Math.max(a,Math.min(b,n));
const smooth=n=>{n=clamp(n);return n*n*(3-2*n);};
const lerp=(a,b,t)=>a+(b-a)*t;
const mod=(a,b)=>((a%b)+b)%b;
function getCamera(t){
 let i=0;while(i<cameraKeys.length-2&&t>cameraKeys[i+1][0])i++;
 const b=cameraKeys[i],c=cameraKeys[i+1],h=c[0]-b[0],p=clamp((t-b[0])/h),p2=p*p,p3=p2*p;
 const values=[1,2,3,4].map(j=>{
  const val=k=>j===3?Math.log(cameraKeys[k][j]):cameraKeys[k][j];
  const tangent=k=>{if(k===0||k===cameraKeys.length-1)return 0;const h0=cameraKeys[k][0]-cameraKeys[k-1][0],h1=cameraKeys[k+1][0]-cameraKeys[k][0],d0=(val(k)-val(k-1))/h0,d1=(val(k+1)-val(k))/h1;if(d0*d1<=0)return 0;const w1=2*h1+h0,w2=h1+2*h0;return(w1+w2)/(w1/d0+w2/d1);};
  const v=(2*p3-3*p2+1)*val(i)+(p3-2*p2+p)*h*tangent(i)+(-2*p3+3*p2)*val(i+1)+(p3-p2)*h*tangent(i+1);return j===3?Math.exp(v):v;
 });
 return {x:values[0],y:values[1],z:values[2],angle:values[3]};
}
function text(g,s,x,y,size,color=C.ink,weight=400,align='left',mono=false){g.font=`${weight} ${size}px "${mono&&!/[\u3400-\u9fff]/.test(s)?'Consolas':'Microsoft YaHei'}",sans-serif`;g.textBaseline='alphabetic';g.textAlign=align;g.fillStyle=color;g.fillText(s,x,y);}
function line(g,x,y,a,b,c,w=1){g.beginPath();g.moveTo(x,y);g.lineTo(a,b);g.strokeStyle=c;g.lineWidth=w;g.stroke();}
function circle(g,x,y,r,c,fill=true,w=1){g.beginPath();g.arc(x,y,r,0,Math.PI*2);g.lineWidth=w;if(fill){g.fillStyle=c;g.fill();}else{g.strokeStyle=c;g.stroke();}}
function alpha(g,a,fn){g.save();g.globalAlpha*=clamp(a);fn();g.restore();}
function pathFor(e){
 if(e.modelReturn)return [[e.a.x,e.a.y],[e.a.x+450,e.a.y-1400],[e.b.x+750,e.b.y-1400],[e.b.x,e.b.y]];
 if(e.controlReturn)return [[e.a.x,e.a.y],[e.a.x-2300,e.a.y+650],[e.b.x+650,e.b.y+2900],[e.b.x,e.b.y]];
 const dx=e.b.x-e.a.x;return [[e.a.x,e.a.y],[e.a.x+dx*.45,e.a.y],[e.b.x-dx*.45,e.b.y],[e.b.x,e.b.y]];
}
function bez(p,q){const u=1-q;return {x:u*u*u*p[0][0]+3*u*u*q*p[1][0]+3*u*q*q*p[2][0]+q*q*q*p[3][0],y:u*u*u*p[0][1]+3*u*u*q*p[1][1]+3*u*q*q*p[2][1]+q*q*q*p[3][1]};}
function visibility(n,cam){const screenX=(n.x-cam.x)*cam.z,screenY=(n.y-cam.y)*cam.z;return Math.abs(screenX)<1400&&Math.abs(screenY)<850;}
function detailLevel(rank,z){return rank===0?1:rank===1?smooth((z-.19)/.2):rank===2?smooth((z-.44)/.19):smooth((z-.69)/.2);}
function glyph(g,type,x,y,t,color,seed){
 g.save();g.translate(x,y);g.strokeStyle=color;g.fillStyle=color;
 if(type==='sensor'||type==='pixels'||type==='crop'){
  const cols=14,rows=8,s=11;
  for(let row=0;row<rows;row++)for(let col=0;col<cols;col++){
   const d=Math.hypot(col-6.5,row-3.5),value=d>2.0&&d<3.5?.7:.08;g.globalAlpha*=1;g.fillStyle=`rgba(100,220,210,${value})`;g.fillRect(col*s,row*s,8,8);
  }
  const yy=mod(t*25,rows*s);line(g,-10,yy,cols*s+8,yy,color,1.6);
  if(type==='crop'){g.setLineDash([5,4]);g.strokeStyle=color;g.strokeRect(43,10,77,66);g.setLineDash([]);}
 }else if(type==='hist'||type==='isp'||type==='bars'){
  for(let i=0;i<18;i++){const h=12+68*Math.exp(-Math.pow((i-8-Math.sin(t*.5)*3)/5,2));alpha(g,.2+.7*i/18,()=>{g.fillStyle=color;g.fillRect(i*9,84-h,5,h);});}
  line(g,-5,86,170,86,color,1);
 }else if(type==='kernel'){
  for(let i=0;i<9;i++){const on=(i+Math.floor(t*2+seed))%4!==0;alpha(g,on?.72:.12,()=>{g.fillStyle=color;g.fillRect(i%3*25,Math.floor(i/3)*25,18,18);});}
 }else if(type==='memory'||type==='queue'||type==='data'){
  const count=type==='queue'?4:8;
  for(let i=0;i<count;i++){const hot=(i+Math.floor(t*3+seed))%count<2;g.save();g.translate(i*19,-i*4);g.transform(1,0,-.23,1,0,0);alpha(g,hot?.78:.13,()=>{g.fillStyle=color;g.fillRect(0,0,12,65);});g.restore();}
 }else if(type==='metadata'||type==='identity'){
  for(let i=0;i<24;i++){alpha(g,i>=16&&i<=20?.8:.13+.16*Math.sin(i+t),()=>{g.fillStyle=color;g.fillRect(i%8*18,Math.floor(i/8)*20,13,12);});}
 }else if(type==='npu'){
  g.save();g.transform(1,.12,-.28,.84,0,0);
  for(let y=0;y<6;y++)for(let x=0;x<9;x++){alpha(g,.13+.6*Math.pow(Math.max(0,Math.sin(x*.8+y*.7-t*3)),2),()=>{g.fillStyle=color;g.fillRect(x*16,y*16,11,11);});}
  g.strokeStyle=color;g.lineWidth=1;g.strokeRect(-8,-8,155,108);g.restore();
 }else if(type==='network'||type==='model'){
  const count=type==='model'?3:6;
  for(let i=0;i<count;i++){g.save();g.translate(i*28,-i*8);g.transform(1,.10,-.22,1,0,0);alpha(g,.12+.1*(1+Math.sin(t*2-i)),()=>{g.fillStyle=color;g.fillRect(0,0,28,88-i*5);});g.strokeStyle=color;g.lineWidth=.8;g.strokeRect(0,0,28,88-i*5);g.restore();}
 }else if(type==='strips'){
  for(let i=0;i<3;i++){g.strokeStyle=color;g.lineWidth=1;g.strokeRect(i*43,0,35,80);alpha(g,.12+.1*Math.sin(t+i),()=>{g.fillStyle=color;g.fillRect(i*43,0,35,80);});line(g,i*43,mod(t*28+i*6,80),i*43+35,mod(t*28+i*6,80),color,2);}
 }else if(type==='display'||type==='boxes'){
  const dx=Math.sin(t*.8)*15;g.strokeStyle=color;g.lineWidth=1;g.strokeRect(0,0,156,88);for(let i=0;i<3;i++){g.strokeRect(20+i*42+dx,16+(i%2)*24,25,25);circle(g,32+i*42+dx,28+(i%2)*24,3,color);}
 }else if(type==='track'){
  for(let j=0;j<3;j++){g.beginPath();for(let i=0;i<160;i++){let yy=Math.sin(i*.02+j*.6)*23+j*25;i?g.lineTo(i,yy):g.moveTo(i,yy);}g.strokeStyle=color;g.globalAlpha=.5;g.stroke();const pos=mod(t*25+j*45,150);circle(g,pos,Math.sin(pos*.02+j*.6)*23+j*25,5,color);}
 }else if(type==='votes'){
  for(let i=0;i<5;i++){let a=mod(i+Math.floor(t*.4),5)<3;circle(g,i*29,22,9,color,a,1.5);}
 }else if(type==='pulse'){
  g.beginPath();for(let i=0;i<180;i++){let p=mod(i/180+t*.22,1),yy=p>.3&&p<.6?0:50;i?g.lineTo(i,yy):g.moveTo(i,yy);}g.lineWidth=2;g.strokeStyle=color;g.stroke();
 }else if(type==='fork'){
  for(let i=-1;i<=1;i++)line(g,0,40,140,40+i*50,color,1.2);
 }
 g.restore();
}
function background(g,t,cam){
 if(staticLayers)g.drawImage(staticLayers.base,0,0,W,H);
 else{g.fillStyle=C.bg;g.fillRect(0,0,W,H);const glow=g.createRadialGradient(1090,530,10,1090,530,1000);glow.addColorStop(0,'#10232b');glow.addColorStop(.55,'#09121c');glow.addColorStop(1,C.bg);g.fillStyle=glow;g.fillRect(0,0,W,H);}
 const ox=mod(-cam.x*cam.z*.12,85),oy=mod(-cam.y*cam.z*.12,85);
 for(let x=ox;x<W;x+=85)for(let y=oy;y<H;y+=85)circle(g,x,y,.8,'#27414b');
 for(let i=0;i<24;i++){const x=mod(i*487.43-cam.x*.02+t*3,2050)-50,y=mod(i*283.17-cam.y*.02,1180)-50;alpha(g,.1+(i%3)*.05,()=>circle(g,x,y,i%4?1:2,C.image));}
}
function drawTree(g,t,cam){
 g.save();g.translate(W/2,H/2);g.rotate(cam.angle);g.scale(cam.z,cam.z);g.translate(-cam.x,-cam.y);
 for(const e of edges){
  const p=pathFor(e),minX=Math.min(...p.map(v=>v[0])),maxX=Math.max(...p.map(v=>v[0])),minY=Math.min(...p.map(v=>v[1])),maxY=Math.max(...p.map(v=>v[1]));
  if((maxX-cam.x)*cam.z< -1300||(minX-cam.x)*cam.z>1300||(maxY-cam.y)*cam.z< -800||(minY-cam.y)*cam.z>800)continue;
  const color=e.modelReturn?C.model:e.controlReturn?C.control:C[e.b.branch],rank=Math.max(e.a.rank,e.b.rank),lod=detailLevel(rank,cam.z),weight=e.secondary?.32:.18+lod*.55;
  g.beginPath();g.moveTo(...p[0]);g.bezierCurveTo(...p[1],...p[2],...p[3]);g.strokeStyle=color;g.lineWidth=(e.b.rank<=1?1.4:1)/cam.z;g.globalAlpha=weight;if(e.secondary)g.setLineDash([6/cam.z,10/cam.z]);g.stroke();g.setLineDash([]);g.globalAlpha=1;
  const speed=e.modelReturn?.045:e.controlReturn?.025:.1+e.b.index%5*.017,count=rank<=1?3:1;
  for(let k=0;k<count;k++){
   const q=mod(t*speed+k/count+e.b.index*.173,1),pos=bez(p,q);
   for(let j=7;j>=0;j--){const tail=bez(p,Math.max(0,q-j*.006));alpha(g,weight*(1-j/8),()=>circle(g,tail.x,tail.y,(j?1.6:3.5)/cam.z,color));}
   if(rank<=1){alpha(g,.12*lod,()=>circle(g,pos.x,pos.y,12/cam.z,color));}
  }
 }
 for(const n of nodes){
  if(!visibility(n,cam))continue;
  if(n.id==='ch1'&&cam.z<.18)continue;
  const color=C[n.branch],lod=detailLevel(n.rank,cam.z),dx=(n.x-cam.x)*cam.z,dy=(n.y-cam.y)*cam.z,focus=Math.exp(-(dx*dx/1.4e6+dy*dy/6e5));
  const r=(n.rank===0?8:n.rank===1?5:3)/cam.z;
  circle(g,n.x,n.y,r,color,true);alpha(g,.12+focus*.12,()=>circle(g,n.x,n.y,r*3.8,color,false,1.0/cam.z));
  if(lod<.015)continue;
  const edgeFade=clamp((1020-Math.abs(dx))/150)*clamp((520-Math.abs(dy))/90);
  alpha(g,lod*edgeFade*(.55+.45*focus),()=>{
   const main=n.rank<=1,scale=n.rank===0?Math.max(1,.53/cam.z):1;
   const size=(n.rank===0?42:n.rank===1?35:n.rank===2?29:27)*scale;
   const tx=n.x+(main?0:18),ty=n.y-(main?37:16),align=main?'center':'left';
   // A narrow background stroke keeps thin branches from crossing glyphs.
   g.font=`${main?500:400} ${size}px "Microsoft YaHei",sans-serif`;g.textAlign=align;g.textBaseline='alphabetic';g.strokeStyle='#071018';g.lineWidth=6/cam.z;g.lineJoin='round';g.strokeText(n.title,tx,ty);
   text(g,n.title,tx,ty,size,C.ink,main?500:400,align);
   if(cam.z>.38&&n.detail){const detailSize=(n.rank<=1?23:21);text(g,n.detail,tx,n.y+(main?43:25),detailSize,color,400,align,true);}
   if(n.glyph&&cam.z>.52){alpha(g,smooth((cam.z-.52)/.2)*.8,()=>glyph(g,n.glyph,n.x+(main?-75:25),n.y+(main?95:67),t,color,n.index));}
  });
 }
 g.restore();
}
function overlay(g,t,cam){
 // Only a small locator and provenance mark: all technical content is on the tree.
 text(g,'VISION / TECHNICAL TREE',64,55,18,C.muted,400,'left',true);
 text(g,'02',1855,55,21,C.image,400,'right',true);
 const name=chapters.find(ch=>t<ch.end)?.name||chapters.at(-1).name;
 text(g,name,64,89,23,C.ink,400);
 line(g,64,1029,1856,1029,'#233c48',1);line(g,64,1029,64+1792*t/DURATION,1029,C.image,2);
 text(g,'离线候选 · 结构示意',64,1061,17,C.muted);
 if(t>=43&&t<99)text(g,'¹ 512 × 288 原生裁剪模型接口',960,1061,16,C.muted,400,'center');
 text(g,`${String(Math.floor(t)).padStart(3,'0')} / 130`,1855,1061,18,C.muted,400,'right',true);
}
function draw(g,time,width=W,height=H,cameraOverride=null){
 const t=clamp(time,0,DURATION-.0001),cam=cameraOverride||getCamera(t);
 g.save();g.setTransform(width/W,0,0,height/H,0,0);background(g,t,cam);drawTree(g,t,cam);
 // Soft frame edges maintain legibility while the camera passes through branches.
 if(staticLayers)g.drawImage(staticLayers.vignette,0,0,W,H);
 else{const vign=g.createRadialGradient(960,540,440,960,540,1090);vign.addColorStop(0,'#05090f00');vign.addColorStop(1,'#05090fb0');g.fillStyle=vign;g.fillRect(0,0,W,H);}
 overlay(g,t,cam);g.restore();
}
const api={draw,W,H,DURATION,chapters,nodes,edges:edges.map(e=>({from:e.a.id,to:e.b.id,secondary:!!e.secondary})),getCamera,prepareStaticLayers};
if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.DataflowFilm=api;
})(typeof globalThis!=='undefined'?globalThis:this);
