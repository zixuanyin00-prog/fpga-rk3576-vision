'use strict';

const previous = require('../重剪_v9/animations_v9.cjs');
const {createCanvas, GlobalFonts} = require('../../dataflow_film_20261028/node_modules/@napi-rs/canvas');

const durations = {title:4, application:3, end:5, parameter_short:6, flow_short:4, tracking_short:7, trigger_short:7};
const C = {bg:'#0d1d29', panel:'#17313f', deep:'#102632', line:'#355768', ink:'#edf5f3', muted:'#a5bdc7', teal:'#78dfcb', gold:'#efcf8a'};
const T = {
  parameter:['双向调参','控制台','RK','FPGA','成像参数','控制桥接','图像处理','配置下发','状态回读'],
  flow:['从图像到执行','相机','FPGA','RK','执行设备','图像输入','采集 · 预处理','定位与分类','控制器 · 电磁阀','NPU'],
  tracking:['同一工件，连续确认','真实观测 1','真实观测 2','真实观测 3','ID 07','连续观测积累','类别已确认','同一身份 · 连续真实观测'],
  trigger:['确认类别，真实过线后触发','真实位置越过触发线','触发线','过线前','已真实过线','触发条件','所选类别已确认','控制输出已启用','真实位置已过线','设定延时','控制脉冲','脉宽','过线已记录','ID 07']
};
const visibleText = [...new Set([...previous.visibleText, ...Object.values(T).flat()])];
const clamp = value => Math.max(0, Math.min(1, value));
const ease = value => {const p=clamp(value); return p*p*(3-2*p);};
const mix = (a,b,p) => a+(b-a)*p;

function text(g, value, x, y, size=34, color=C.ink, align='center', weight=500) {
  g.font=`${weight} ${size}px "Microsoft YaHei",sans-serif`;
  g.textAlign=align; g.textBaseline='middle'; g.fillStyle=color; g.fillText(value,x,y);
}
function box(g,x,y,w,h,fill=C.panel,stroke=C.line,r=20) {
  g.beginPath(); g.roundRect(x,y,w,h,r); g.fillStyle=fill; g.fill();
  if(stroke) {g.strokeStyle=stroke; g.lineWidth=1.8; g.stroke();}
}
function reveal(g,t,at,fn,duration=.45) {
  g.save(); g.globalAlpha*=ease((t-at)/duration); fn(); g.restore();
}
function check(g,x,y,color=C.teal,size=1) {
  g.save(); g.strokeStyle=color; g.lineWidth=4*size; g.lineCap='round'; g.lineJoin='round';
  g.beginPath(); g.moveTo(x-11*size,y); g.lineTo(x-3*size,y+8*size); g.lineTo(x+14*size,y-11*size); g.stroke(); g.restore();
}
function arrow(g,x1,y1,x2,y2,color=C.teal,packet=null) {
  const angle=Math.atan2(y2-y1,x2-x1);
  g.save(); g.strokeStyle=color; g.fillStyle=color; g.lineWidth=2.8;
  g.beginPath(); g.moveTo(x1,y1); g.lineTo(x2,y2); g.stroke();
  g.beginPath(); g.moveTo(x2,y2); g.lineTo(x2-12*Math.cos(angle-.4),y2-12*Math.sin(angle-.4));
  g.lineTo(x2-12*Math.cos(angle+.4),y2-12*Math.sin(angle+.4)); g.closePath(); g.fill();
  if(packet!==null && packet>0 && packet<1) {
    g.shadowColor=color; g.shadowBlur=15; g.beginPath(); g.arc(mix(x1,x2,packet),mix(y1,y2,packet),5,0,Math.PI*2); g.fill();
  }
  g.restore();
}
function resistor(g,x,y,scale=1) {
  g.save(); g.translate(x,y); g.scale(scale,scale);
  box(g,-68,-32,136,64,'#a4b6b7','#d5e5df',7);
  g.fillStyle='#e2ebe7'; g.fillRect(-69,-26,18,52); g.fillRect(51,-26,18,52); g.restore();
}
function badge(g,value,x,y,w=160,color=C.teal) {
  box(g,x-w/2,y-24,w,48,C.deep,color,24); text(g,value,x,y,27,color);
}
function background(g) {
  g.fillStyle=C.bg; g.fillRect(0,0,1920,1080);
  const gradient=g.createRadialGradient(960,400,70,960,470,1050);
  gradient.addColorStop(0,'#203d4b'); gradient.addColorStop(.65,'#112834'); gradient.addColorStop(1,C.bg);
  g.fillStyle=gradient; g.fillRect(0,0,1920,1080);
}
function heading(g,value) {
  text(g,value,960,150,53,C.ink,'center',600);
  g.fillStyle=C.teal; g.fillRect(914,220,92,3);
}

function parameter(g,t) {
  const q=T.parameter; heading(g,q[0]);
  const xs=[115,745,1375];
  xs.forEach((x,i)=>reveal(g,t,.08*i,()=>{
    box(g,x,330,430,390); text(g,q[i+1],x+215,391,43,i===2?C.teal:C.ink,'center',600);
    g.fillStyle=C.line; g.fillRect(x+35,441,360,1);
    text(g,q[i+4],x+215,489,31,C.muted);
  }));
  reveal(g,t,.3,()=>{
    for(let i=0;i<2;i++) {
      const y=560+i*66, value=mix(.30+i*.2,.64-i*.2,ease((t-.55)/.95));
      g.strokeStyle=C.line; g.lineWidth=5; g.beginPath(); g.moveTo(188,y); g.lineTo(472,y); g.stroke();
      g.fillStyle=i?C.gold:C.teal; g.beginPath(); g.arc(188+284*value,y,10,0,Math.PI*2); g.fill();
    }
    box(g,815,540,290,105,C.deep,C.line,15);
    for(let i=0;i<3;i++) {g.fillStyle=i===1?C.gold:C.teal; g.beginPath(); g.arc(887+i*74,593,8,0,Math.PI*2); g.fill();}
    box(g,1440,540,300,105,C.deep,C.teal,15);
    g.strokeStyle=C.teal; g.lineWidth=3;
    for(let i=0;i<3;i++) {g.beginPath(); g.moveTo(1480,565+i*27); g.lineTo(1700,565+i*27); g.stroke();}
  });
  reveal(g,t,.75,()=>{
    text(g,q[7],960,281,33,C.gold);
    arrow(g,555,465,735,465,C.gold,clamp((t-.85)/.6));
    arrow(g,1185,465,1365,465,C.gold,clamp((t-1.45)/.6));
  });
  reveal(g,t,2.15,()=>{
    arrow(g,1365,680,1185,680,C.teal,clamp((t-2.3)/.6));
    arrow(g,735,680,555,680,C.teal,clamp((t-2.9)/.6));
    text(g,q[8],960,806,35,C.teal);
  });
  reveal(g,t,3.35,()=>check(g,1050,806,C.teal,1.05));
}

function chip(g,x,y,label,color) {
  box(g,x-47,y-47,94,94,C.deep,color,12);
  g.strokeStyle=color; g.lineWidth=3;
  for(let i=-1;i<=1;i++) {
    const p=i*25;
    g.beginPath(); g.moveTo(x+p,y-62); g.lineTo(x+p,y-48); g.moveTo(x+p,y+48); g.lineTo(x+p,y+62);
    g.moveTo(x-62,y+p); g.lineTo(x-48,y+p); g.moveTo(x+48,y+p); g.lineTo(x+62,y+p); g.stroke();
  }
  if(label)text(g,label,x,y,26,color);
}
function flow(g,t) {
  const q=T.flow; heading(g,q[0]);
  const nodes=[{x:85,w:320,name:q[1],detail:q[5]}, {x:525,w:360,name:q[2],detail:q[6]},
    {x:1005,w:360,name:q[3],detail:q[7]}, {x:1485,w:350,name:q[4],detail:q[8]}];
  nodes.forEach((node,i)=>reveal(g,t,i*.08,()=>{
    const center=node.x+node.w/2, color=i>1?C.gold:C.teal;
    box(g,node.x,340,node.w,350,C.panel,color); text(g,node.name,center,402,42,color,'center',600);
    if(i===0) {
      box(g,center-56,485,100,67,C.deep,C.teal,11);
      g.beginPath(); g.moveTo(center+44,505); g.lineTo(center+74,488); g.lineTo(center+74,548); g.lineTo(center+44,531); g.closePath(); g.fillStyle=C.teal; g.fill();
    } else if(i===1 || i===2) chip(g,center,520,i===2?q[9]:'',color);
    else {
      g.strokeStyle=C.gold; g.lineWidth=4; g.lineJoin='round';
      g.beginPath(); g.moveTo(center-80,540); g.lineTo(center-35,540); g.lineTo(center-35,490); g.lineTo(center+35,490); g.lineTo(center+35,540); g.lineTo(center+80,540); g.stroke();
    }
    text(g,node.detail,center,624,31,C.muted);
  }));
  for(let i=0;i<3;i++)reveal(g,t,.45+i*.15,()=>{
    const left=nodes[i].x+nodes[i].w+12, right=nodes[i+1].x-12;
    arrow(g,left,520,right,520,i===2?C.gold:C.teal,clamp((t-.65-i*.4)/.45));
  });
}

function tracking(g,t) {
  const q=T.tracking; heading(g,q[0]);
  const positions=[170,260,350], ats=[.2,1.35,2.5];
  for(let i=0;i<3;i++)reveal(g,t,ats[i],()=>{
    const x=110+i*590;
    box(g,x,310,520,350); text(g,q[1+i],x+260,364,35,C.muted);
    g.strokeStyle=C.line; g.lineWidth=3;
    for(const y of [433,542]) {g.beginPath(); g.moveTo(x+32,y); g.lineTo(x+488,y); g.stroke();}
    resistor(g,x+positions[i],486,.88);
    g.strokeStyle=C.teal; g.lineWidth=2.8; g.strokeRect(x+positions[i]-72,438,144,96);
    badge(g,q[4],x+260,609);
    if(i>0)arrow(g,x-70,486,x-2,486,C.teal);
  });
  reveal(g,t,.45,()=>{
    const confirmed=t>=4.1;
    box(g,610,735,700,90,C.deep,confirmed?C.teal:C.line,45);
    text(g,confirmed?q[6]:q[5],960,780,37,confirmed?C.teal:C.muted);
    if(confirmed)check(g,1225,780,C.teal,1.0);
    text(g,q[7],960,875,29,C.muted);
  });
}

function condition(g,label,x,y,active) {
  box(g,x,y-19,38,38,C.deep,active?C.teal:C.line,8);
  if(active)check(g,x+19,y,C.teal,.7);
  text(g,label,x+66,y,30,active?C.ink:C.muted,'left');
}
function trigger(g,t) {
  const q=T.trigger; heading(g,q[0]);
  const crossAt=1.8, delayAt=2.25, pulseAt=3.05, recordedAt=4.4;
  reveal(g,t,.1,()=>{
    box(g,110,290,1030,410); text(g,q[1],625,341,35,C.ink);
    g.strokeStyle=C.line; g.lineWidth=3;
    for(const y of [450,575]) {g.beginPath(); g.moveTo(160,y); g.lineTo(1090,y); g.stroke();}
    const x=mix(440,860,ease((t-.8)/2));
    g.save(); g.setLineDash([9,9]); g.strokeStyle=C.gold; g.lineWidth=2.5;
    g.beginPath(); g.moveTo(650,407); g.lineTo(650,597); g.stroke(); g.restore();
    text(g,q[2],650,388,27,C.gold);
    resistor(g,x,513,1.05); g.strokeStyle=C.teal; g.lineWidth=2.8; g.strokeRect(x-86,456,172,114);
    badge(g,q[13],x,626,160);
    text(g,t<crossAt?q[3]:q[4],290,635,29,t<crossAt?C.muted:C.teal);
    box(g,1220,290,590,410); text(g,q[5],1515,341,35,C.ink);
    condition(g,q[6],1270,438,t>=.5);
    condition(g,q[7],1270,527,t>=.5);
    condition(g,q[8],1270,616,t>=crossAt);
  });
  reveal(g,t,delayAt,()=>{
    box(g,110,755,500,145,C.deep,C.gold); text(g,q[9],360,799,33,C.gold);
    g.strokeStyle=C.line; g.lineWidth=5; g.beginPath(); g.moveTo(190,851); g.lineTo(530,851); g.stroke();
    g.strokeStyle=C.gold; g.beginPath(); g.moveTo(190,851); g.lineTo(190+340*ease((t-delayAt)/.8),851); g.stroke();
    arrow(g,620,829,700,829,C.gold);
  });
  reveal(g,t,pulseAt,()=>{
    box(g,710,755,500,145,C.deep,C.gold); text(g,q[10],960,791,33,C.gold);
    const points=[[760,838],[886,838],[886,810],[1034,810],[1034,838],[1160,838]];
    g.strokeStyle=C.gold; g.lineWidth=4; g.lineJoin='round'; g.beginPath();
    points.forEach((p,i)=>i?g.lineTo(...p):g.moveTo(...p)); g.stroke();
    g.strokeStyle=C.muted; g.lineWidth=1.5; g.beginPath(); g.moveTo(886,850); g.lineTo(886,857); g.lineTo(1034,857); g.lineTo(1034,850); g.stroke();
    text(g,q[11],960,881,24,C.muted);
  });
  reveal(g,t,recordedAt,()=>{
    arrow(g,1220,829,1300,829,C.teal);
    box(g,1310,755,500,145,C.deep,C.teal); text(g,q[12],1560,805,34,C.teal); check(g,1560,858,C.teal,1.1);
  });
}

function draw(g,t,w=1920,h=1080,name='title') {
  if(name==='title'||name==='application'||name==='end')return previous.draw(g,t,w,h,name);
  g.reset(); g.save(); g.scale(w/1920,h/1080); background(g);
  ({parameter_short:parameter,flow_short:flow,tracking_short:tracking,trigger_short:trigger}[name])(g,t);
  // This reserved region receives the root composition's subtitles.
  g.fillStyle=C.bg; g.fillRect(0,930,1920,150);
  const fade=1-Math.min(ease(t/.22),ease((durations[name]-t)/.22));
  if(fade>0) {g.globalAlpha=fade; g.fillStyle=C.bg; g.fillRect(0,0,1920,1080);}
  g.restore();
}

module.exports={durations,draw,visibleText};

if(require.main===module) {
  const fs=require('node:fs'), path=require('node:path');
  GlobalFonts.registerFromPath('C:/Windows/Fonts/msyh.ttc','Microsoft YaHei');
  const canvas=createCanvas(1920,1080), g=canvas.getContext('2d');
  for(const [name,t] of [['parameter_short',2.0],['parameter_short',5.0],['flow_short',1.2],['flow_short',3.0],['tracking_short',2.4],['tracking_short',6.0],['trigger_short',2.3],['trigger_short',6.0]]) {
    draw(g,t,1920,1080,name);
    fs.writeFileSync(path.join(__dirname,`preview_v10_${name}_${t}.png`),canvas.toBuffer('image/png'));
  }
}
