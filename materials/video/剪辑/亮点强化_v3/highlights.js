'use strict';
const C={bg:'#11202a',panel:'#19323d',inner:'#10252f',ink:'#edf5f4',muted:'#a2bac3',a:'#71dbce',gold:'#ecd293',line:'#395b67'};
const clamp=x=>Math.max(0,Math.min(1,x));const ease=x=>{x=clamp(x);return x*x*(3-2*x)};
function text(g,s,x,y,size=30,color=C.ink,align='center'){g.font=`${size}px "Microsoft YaHei",sans-serif`;g.fillStyle=color;g.textAlign=align;g.textBaseline='middle';g.fillText(s,x,y)}
function box(g,x,y,w,h,fill=C.panel,stroke=C.line,r=18){g.beginPath();g.roundRect(x,y,w,h,r);g.fillStyle=fill;g.fill();g.strokeStyle=stroke;g.lineWidth=2;g.stroke()}
function appear(g,t,start,fn){g.save();g.globalAlpha=ease((t-start)/1.2);fn();g.restore()}
function wire(g,pts,t,col=C.a){g.strokeStyle=col;g.lineWidth=3;g.beginPath();pts.forEach(([x,y],i)=>i?g.lineTo(x,y):g.moveTo(x,y));g.stroke();const p=pts.at(-1),q=pts.at(-2),a=Math.atan2(p[1]-q[1],p[0]-q[0]);g.beginPath();g.moveTo(...p);g.lineTo(p[0]-14*Math.cos(a-.4),p[1]-14*Math.sin(a-.4));g.lineTo(p[0]-14*Math.cos(a+.4),p[1]-14*Math.sin(a+.4));g.closePath();g.fillStyle=col;g.fill();let lengths=[],total=0;for(let i=1;i<pts.length;i++){const l=Math.hypot(pts[i][0]-pts[i-1][0],pts[i][1]-pts[i-1][1]);lengths.push(l);total+=l}for(let j=0;j<3;j++){let d=((t*.18+j/3)%1)*total,i=0;while(i<lengths.length-1&&d>lengths[i])d-=lengths[i++];const u=d/lengths[i];g.beginPath();g.arc(pts[i][0]+u*(pts[i+1][0]-pts[i][0]),pts[i][1]+u*(pts[i+1][1]-pts[i][1]),4,0,Math.PI*2);g.fill()}}
function resistor(g,x,y,angle=0,light=.7){g.save();g.translate(x,y);g.rotate(angle);box(g,-52,-26,104,52,`rgb(${Math.round(170*light)},${Math.round(190*light)},${Math.round(195*light)})`,C.muted,5);g.fillStyle='#e1e8e5';g.fillRect(-58,-20,12,40);g.fillRect(46,-20,12,40);text(g,'473',0,0,21,'#213840');g.restore()}
function caption(g,t,entries){for(const [a,b,ls]of entries)if(t>=a&&t<b){g.save();g.globalAlpha=Math.min(ease((t-a)/.4),ease((b-t)/.4));ls.forEach((s,i)=>text(g,s,960,967+i*46,34));g.restore()}}
const durations={parameter:22,async:22,tracking:30,small:6};
function draw(g,t,w,h,part){g.save();g.scale(w/1920,h/1080);g.fillStyle=C.bg;g.fillRect(0,0,1920,1080);const gr=g.createRadialGradient(950,480,80,950,480,1100);gr.addColorStop(0,'#23414d');gr.addColorStop(1,C.bg);g.fillStyle=gr;g.fillRect(0,0,1920,1080);
 if(part==='parameter'){
  text(g,'亮点一 · 可在线调节的 FPGA 图像处理',960,100,46);text(g,'控制台配置 → RK 下发 → FPGA 处理 → 状态反馈',960,160,25,C.muted);
  const xs=[140,740,1340];['现场控制台','RK · 控制桥接','FPGA · 图像处理'].forEach((s,i)=>appear(g,t,i*.7,()=>{box(g,xs[i],320,440,420);text(g,s,xs[i]+220,365,32,i===2?C.a:C.ink)}));
  appear(g,t,1,()=>{text(g,'成像参数',360,435,25,C.muted);g.strokeStyle=C.line;g.lineWidth=7;g.beginPath();g.moveTo(205,498);g.lineTo(515,498);g.stroke();const v=.24+.47*ease((t-3)/4);g.fillStyle=C.gold;g.beginPath();g.arc(205+310*v,498,12,0,Math.PI*2);g.fill();box(g,210,570,300,95,C.inner);text(g,t<6?'调整参数':'配置已更新',360,618,28,C.gold)});
  appear(g,t,2,()=>{text(g,'下发处理配置',960,474,30,C.gold);text(g,'读取板端状态',960,591,30,C.a)});
  appear(g,t,3,()=>{box(g,1400,417,320,240,C.inner);const l=.44+.5*ease((t-7)/3);resistor(g,1560,524,0,l);text(g,'处理效果示意',1560,624,22,C.muted)});
  appear(g,t,4,()=>{wire(g,[[580,470],[740,470]],t,C.gold);wire(g,[[1180,470],[1340,470]],t,C.gold)});
  appear(g,t,10,()=>{wire(g,[[1340,650],[1180,650]],t,C.a);wire(g,[[740,650],[580,650]],t,C.a);text(g,'状态回读 · 现场观察',960,853,25,C.a)});
  caption(g,t,[[.8,10.6,['通过控制台调整 FPGA 图像处理参数，','经由 RK 下发配置，观察处理效果。']],[11.3,21.5,['参数下发与状态回读构成双向交互，','让 FPGA 处理链路能够在现场调试。']]]);
 }
 if(part==='async'){
  text(g,'亮点三 · 显示与推理独立运行',960,100,46);text(g,'并行调度示意 · 不表示实际帧率或耗时比例',960,160,23,C.muted);
  box(g,130,310,1660,200);box(g,130,615,1660,210);text(g,'预览链路',280,365,30,C.a);text(g,'持续更新',280,420,24,C.muted);text(g,'推理链路',280,675,30,C.gold);text(g,'异步执行',280,730,24,C.muted);
  const active=Math.max(0,t-2);for(let i=0;i<6;i++){const x=490+i*200-(active*45%200);g.save();g.beginPath();g.rect(440,335,1260,150);g.clip();box(g,x,348,162,122,C.inner,C.a,10);resistor(g,x+81,401,.1);g.restore()}
  appear(g,t,2,()=>{wire(g,[[630,510],[630,615]],t,C.gold);text(g,'异步取图',780,563,23,C.gold)});
  appear(g,t,3,()=>{box(g,480,657,400,112,C.inner,C.gold);text(g,'定位与姿态判断',680,692,28);const phase=(Math.max(0,t-3)%5)/5;g.fillStyle=C.line;g.fillRect(520,733,320,5);g.fillStyle=C.gold;g.fillRect(520,733,320*phase,5);wire(g,[[880,713],[1060,713]],t,C.gold);box(g,1060,657,300,112,C.inner);text(g,'结果缓存',1210,710,30)});
  appear(g,t,8,()=>{wire(g,[[1360,713],[1600,713],[1600,510]],t,C.a);text(g,'结果更新',1490,599,23,C.a)});
  caption(g,t,[[.8,10.5,['显示链路持续接收新画面，','推理链路独立取图并执行识别。']],[11.2,21.5,['识别完成后异步更新结果缓存，','让模型处理与现场预览分别调度。']]]);
 }
 if(part==='tracking'){
  text(g,'亮点四 · 连续确认，再执行',960,100,46);text(g,'跨帧处理逻辑示意 · 确认条件以实际配置为准',960,160,23,C.muted);
  const starts=[1,6,11];for(let i=0;i<3;i++)appear(g,t,starts[i],()=>{const x=130+i*560;box(g,x,300,500,310);text(g,`连续观测 ${i+1}`,x+250,340,28,C.muted);resistor(g,x+220+i*20,450,.08);g.strokeStyle=C.a;g.lineWidth=2;g.strokeRect(x+142+i*20,400,156,100);text(g,'同一工件 · ID 07',x+250,551,27,C.a);if(i<2)wire(g,[[x+500,450],[x+560,450]],t,C.a)});
  appear(g,t,12,()=>{box(g,130,690,720,145);text(g,t<17?'关联身份，积累姿态观测':'满足确认条件，生成控制事件',490,740,29,t<17?C.ink:C.gold);text(g,t<17?'保留同一工件的连续处理状态':'记录已处理状态',490,790,24,C.muted)});
  appear(g,t,16,()=>{wire(g,[[850,763],[990,763]],t,C.gold);box(g,990,690,800,145);text(g,'外接控制输出',1190,724,27,C.ink);g.strokeStyle=C.a;g.lineWidth=3;g.beginPath();g.moveTo(1040,795);g.lineTo(1760,795);g.stroke();if(t>=18){g.strokeStyle=C.gold;g.lineWidth=5;g.beginPath();g.moveTo(1300,795);g.lineTo(1300,748);g.lineTo(1370,748);g.lineTo(1370,795);g.stroke();text(g,'一次控制事件',1570,747,26,C.gold)}});
  appear(g,t,23,()=>{text(g,'继续跟踪 · 检查已处理状态',960,884,27,C.a)});
  caption(g,t,[[.8,11,['通过跨帧关联，持续观察同一个工件，','结合连续图像确认姿态。']],[11.8,21.8,['达到确认条件后输出控制事件，','并记录该工件的处理状态。']],[22.5,29.5,['后续帧继续关联同一工件，','利用处理状态减少重复触发。']]]);
 }
 if(part==='small'){text(g,'亮点二 · 小样本现场部署',960,460,55);text(g,'采图 → 标注 → 训练测试 → 明确选型 → 部署',960,565,29,C.a)}
 const f=1-Math.min(ease(t/.5),ease((durations[part]-t)/.5));if(f>0){g.globalAlpha=f;g.fillStyle=C.bg;g.fillRect(0,0,1920,1080)}g.restore();}
module.exports={durations,draw};


