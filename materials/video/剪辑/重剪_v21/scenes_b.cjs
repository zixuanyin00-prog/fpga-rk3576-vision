'use strict';

// Layout, typography and narrative only. Every solid is supplied by geometry_b.cjs.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {createCanvas, loadImage, GlobalFonts} = require('../../dataflow_film_20261028/node_modules/@napi-rs/canvas');
const geometry = require('./geometry_b.cjs');

const C = {bg:'#cbdde9', ink:'#183747', muted:'#536f7e', blue:'#346e9f', green:'#39b83f', darkGreen:'#287b38', silver:'#f2f7f8', line:'#8caab9', faint:'#a8c0cc'};
const clamp = x => Math.max(0, Math.min(1, x));
const smooth = x => {x=clamp(x); return x*x*x*(10+x*(-15+6*x));};
const mix = (a,b,p) => a+(b-a)*p;
const observationTimes = [.18,.94,1.7];
const trackingProgress = t => mix(.1,.9,smooth(t/2.4));
const durations = {opening_title:2, chapter_board:1.5, chapter_console:1.2, isp_methods:6, fewshot_value:5.5, chapter_logic:1.2, compression_pcie:5.4, tracking_short:3.2, trigger_short:3.2, dimensions:5, alerts:6.1, application:2.7, end:5};
const chapters = {
  chapter_board:{number:'01',title:'板卡介绍',subtitle:'RK · FPGA · 控制板',variant:0},
  chapter_console:{number:'02',title:'控制台操作',subtitle:'画面 · 标注 · 训练 · 运行',variant:1},
  chapter_logic:{number:'03',title:'识别与工业检测',subtitle:'识别 · 测量 · 告警',variant:2},
  application:{number:'04',title:'实战与应用',subtitle:'姿态分拣 · 类别分拣',variant:3}
};
const visibleText = {
  opening_title:['FPGA 实时视觉识别','与工件自动分拣','现场成像  ·  小样本学习  ·  识别联动'],
  ...Object.fromEntries(Object.entries(chapters).map(([name,c])=>[name,[c.number,c.title,c.subtitle]])),
  isp_methods:['FPGA ISP 图像预处理','四像素并行流水线','去马赛克','白平衡','高斯平滑','直方图映射','前景分割','灰度 / 二值形态学','Sobel 边缘提取','彩色轮廓叠加','数字增益 · 亮度 · 对比度'],
  fewshot_value:['小样本学习，快速适配','15张现场原图','训练','同一模型 · 3类姿态','A','B','C','现场采样 → 小样本训练 → 快速部署'],
  compression_pcie:['PCIe 高速互通与双模式压缩','原生 RGB 推理支路','FPGA','采集与处理','PCIe','图像 DMA 上行','参数下发','状态回读','RK','识别与显示','可选压缩链路 · 预览与归档','预览 / 归档','JPEG 有损','LZ77 无损','PCIe → RK','接收与解码'],
  tracking_short:['同一工件，连续确认','真实观测 1','真实观测 2','真实观测 3','ID 07','连续观测积累','类别已确认','同一身份 · 连续真实观测','同一摄像头','连续帧'],
  trigger_short:['确认类别，真实过线后触发','真实位置越过触发线','触发线','过线前','已真实过线','触发条件','所选类别已确认','控制输出已启用','真实位置已过线','设定延时','控制脉冲','脉宽','过线已记录','ID 07'],
  dimensions:['多目标像素尺寸量化','逐目标检测框','x1, y1','x2, y2','w','h','原生坐标','w = x2 − x1','h = y2 − y1','单位：px','逐目标输出位置与像素宽高'],
  alerts:['边缘异常提示与留证','识别终态事件','终态未确认','身份丢失','RK 边缘端','异常状态提示','真实原图缓存','网口回传','SFTP → 电脑','可选SD卡留存','需启用并挂载'],
  end:['从现场适配，到自动分拣','图像处理与高速互通','ISP · 双模式压缩 · PCIe','现场快速适配','15张原图 · 三类姿态','小样本学习 · 快速部署','多目标工业分析','像素尺寸 · 边缘异常提示','网口回传 · 可选SD留存','多目标高速分拣','实测1200工件/min','各组召回率99.997%']
};
let samples = [];
let observationFrames = [];

async function init() {
  for(const [file,alias] of [['msyh.ttc','CJK'],['msyhbd.ttc','CJK Heavy'],['bahnschrift.ttf','DIN']]) GlobalFonts.registerFromPath('C:/Windows/Fonts/'+file,alias);
  samples = await Promise.all(Array.from({length:15},(_,i)=>loadImage(path.join(__dirname,'../重剪_v13/sample_assets',`native_${i}.png`))));
  // These are three frozen image-plane observations of ONE model, not three live objects.
  observationFrames = observationTimes.map(at=>{
    const progress=trackingProgress(at);
    const canvas=createCanvas(512,288),g=canvas.getContext('2d');
    g.fillStyle='#e6eff2';g.fillRect(0,0,512,288);
    g.strokeStyle='#bdcfd5';g.lineWidth=2;
    for(const y of [65,220]) {g.beginPath();g.moveTo(0,y);g.lineTo(512,y);g.stroke();}
    geometry.drawModel(g,'resistor',{x:0,y:0,w:512,h:288,t:0,progress,view:'top',snapshot:true});
    return canvas;
  });
}

function text(g,value,x,y,size=36,color=C.ink,weight=500,align='left',font='CJK') {
  g.font=`${weight} ${size}px "${font}",sans-serif`;g.textAlign=align;g.textBaseline='middle';g.fillStyle=color;g.fillText(value,x,y);
}
function reveal(g,t,at,fn,span=.24) {
  const p=smooth((t-at)/span);if(p<=0)return;
  g.save();g.globalAlpha*=p;g.translate(0,10*(1-p));fn(p);g.restore();
}
function line(g,points,color=C.line,width=2) {
  g.beginPath();points.forEach(([x,y],i)=>i?g.lineTo(x,y):g.moveTo(x,y));g.strokeStyle=color;g.lineWidth=width;g.lineCap='round';g.lineJoin='round';g.stroke();
}
function arrow(g,x1,y1,x2,y2,color=C.blue,width=2.4,double=false) {
  line(g,[[x1,y1],[x2,y2]],color,width);
  const a=Math.atan2(y2-y1,x2-x1);
  for(const [x,y,r] of double?[[x2,y2,a],[x1,y1,a+Math.PI]]:[[x2,y2,a]]) line(g,[[x-10*Math.cos(r-.42),y-10*Math.sin(r-.42)],[x,y],[x-10*Math.cos(r+.42),y-10*Math.sin(r+.42)]],color,width);
}
function packet(g,points,progress,color=C.green,radius=5) {
  const segments=points.slice(1).map((p,i)=>Math.hypot(p[0]-points[i][0],p[1]-points[i][1]));
  let distance=clamp(progress)*segments.reduce((a,b)=>a+b,0),i=0;
  while(i<segments.length-1&&distance>segments[i]) {distance-=segments[i];i++;}
  const q=segments[i]>0?distance/segments[i]:0;
  g.fillStyle=color;g.beginPath();g.arc(mix(points[i][0],points[i+1][0],q),mix(points[i][1],points[i+1][1],q),radius,0,Math.PI*2);g.fill();
}
function model(g,kind,x,y,w,h,t,opts={}) {
  g.save();const result=geometry.drawModel(g,kind,{x,y,w,h,t,...opts});g.restore();return result;
}
function heading(g,value,t,size=56) {
  reveal(g,t,0,()=>text(g,value,120,126,size,C.ink,700,'left','CJK Heavy'),.22);
  reveal(g,t,.06,()=>line(g,[[120,192],[180,192]],C.green,5),.26);
}
function background(g,t) {
  g.reset();g.fillStyle=C.bg;g.fillRect(0,0,1920,1080);
  const light=g.createRadialGradient(640+50*Math.sin(t*.36),130,60,850,450,1480);
  light.addColorStop(0,'rgba(255,255,255,.36)');light.addColorStop(.6,'rgba(255,255,255,.09)');light.addColorStop(1,'rgba(255,255,255,0)');g.fillStyle=light;g.fillRect(0,0,1920,1080);
}

function opening(g,t,duration) {
  model(g,'hero',990,100,865,790,t,{variant:0,progress:smooth(t/.72),energy:1.2});
  reveal(g,t,0,()=>text(g,visibleText.opening_title[0],120,365,78,C.ink,700,'left','CJK Heavy'),.24);
  reveal(g,t,.09,()=>text(g,visibleText.opening_title[1],120,474,78,C.ink,700,'left','CJK Heavy'),.24);
  reveal(g,t,.19,()=>line(g,[[124,591],[266,591]],C.green,5),.26);
  reveal(g,t,.24,()=>text(g,visibleText.opening_title[2],124,700,32,C.muted),.25);
}

function chapter(g,name,t,duration) {
  const c=chapters[name];
  model(g,'hero',1000,104,850,765,t,{variant:c.variant,progress:smooth(t/.46),energy:1.4});
  reveal(g,t,0,()=>text(g,c.number,120,310,144,C.blue,400,'left','DIN'),.15);
  reveal(g,t,.02,()=>text(g,c.title,120,486,name==='chapter_logic'?88:96,C.ink,700,'left','CJK Heavy'),.16);
  reveal(g,t,.05,()=>text(g,c.subtitle,126,618,37,C.muted),.15);
  reveal(g,t,.08,()=>{
    line(g,[[126,724],[596,724]],C.faint,1.5);
    for(let i=0;i<4;i++){g.fillStyle=i===Number(c.number)-1?C.green:C.faint;g.fillRect(126+i*49,768,i===Number(c.number)-1?30:12,4);}
  },.25);
}

function isp(g,t) {
  const q=visibleText.isp_methods;heading(g,q[0],t);
  reveal(g,t,.08,()=>text(g,q[1],960,265,34,C.blue,500,'center'));
  const labels=[q[2],q[3],q[4],q[5],q[6],q[7],q[8],q[9]];
  for(let i=0;i<8;i++) {
    const left=i<4,row=i%4,y=352+row*112,x=left?120:1370;
    reveal(g,t,.12+i*.055,()=>{
      text(g,labels[i],x,y,i===5?31:35,C.ink);
      const a=left?[480,y,705,y]:[1215,y,1328,y];
      line(g,[[a[0],a[1]],[a[2],a[3]]],C.line,1.8);
      for(let k=0;k<2;k++)packet(g,[[a[0],a[1]],[a[2],a[3]]],((t*.88+k*.5+i*.11)%1),row%2?C.blue:C.green,4);
    });
  }
  model(g,'chip',650,285,620,480,t,{variant:1,progress:smooth(t/.65),role:'isp'});
  reveal(g,t,.37,()=>{
    line(g,[[615,794],[1305,794]],C.faint,1.5);text(g,q[10],960,849,33,C.muted,500,'center');
  });
}

function fewshot(g,t) {
  if(samples.length!==15) throw new Error('Call and await init() before drawing fewshot_value.');
  const q=visibleText.fewshot_value;heading(g,q[0],t);
  reveal(g,t,.08,()=>text(g,q[1],120,265,35,C.muted));
  for(let i=0;i<15;i++)reveal(g,t,.1+i*.027,()=>{
    const x=120+(i%5)*173,y=332+Math.floor(i/5)*126;
    g.fillStyle='rgba(255,255,255,.42)';g.beginPath();g.roundRect(x-5,y-5,164,97,6);g.fill();
    g.drawImage(samples[i],x,y,154,86.625);
  },.23);
  const toward=[[990,514],[1080,514]],away=[[1328,514],[1405,514]];
  reveal(g,t,.45,()=>{
    arrow(g,990,514,1080,514,C.blue);arrow(g,1328,514,1405,514,C.blue);
    packet(g,toward,(t*.7)%1,C.green);packet(g,away,(t*.7+.3)%1,C.green);
  });
  model(g,'chip',1065,351,280,300,t,{variant:2,progress:smooth((t-.3)/.45),role:'training'});
  reveal(g,t,.5,()=>text(g,q[2],1205,700,34,C.blue,500,'center'));
  reveal(g,t,.38,()=>text(g,q[3],1430,265,32,C.muted));
  for(let i=0;i<3;i++)reveal(g,t,.6+i*.13,()=>{
    const y=370+i*126;text(g,q[4+i],1441,y,55,i===1?C.blue:C.darkGreen,600,'left','DIN');
    line(g,[[1513,y],[1777,y]],C.line,1.8);packet(g,[[1513,y],[1777,y]],(t*.5+i*.22)%1,i===1?C.blue:C.green,4);
  });
  reveal(g,t,.95,()=>{
    line(g,[[700,790],[1220,790]],C.faint,1.5);text(g,q[7],960,850,37,C.ink,500,'center');
  });
}

function compression(g,t) {
  const q=visibleText.compression_pcie;heading(g,q[0],t,54);
  reveal(g,t,.08,()=>text(g,q[1],960,255,34,C.blue,500,'center'));
  model(g,'chip',100,285,360,265,t,{variant:1,progress:smooth(t/.5),role:'fpga'});
  model(g,'chip',1460,285,360,265,t+.24,{variant:2,progress:smooth(t/.5),role:'rk'});
  reveal(g,t,.12,()=>{
    text(g,q[2],280,575,45,C.ink,600,'center','DIN');text(g,q[3],280,625,30,C.muted,400,'center');
    text(g,q[8],1640,575,45,C.ink,600,'center','DIN');text(g,q[9],1640,625,30,C.muted,400,'center');
  });
  reveal(g,t,.15,()=>{
    text(g,q[4],960,330,44,C.ink,600,'center','DIN');
    const rows=[{y:407,label:q[5],color:C.darkGreen,right:true},{y:498,label:q[6],color:C.blue,right:false},{y:589,label:q[7],color:C.darkGreen,right:true}];
    for(let i=0;i<rows.length;i++){
      const r=rows[i],a=r.right?488:1432,b=r.right?1432:488;
      arrow(g,a,r.y,b,r.y,r.color,2.1);g.fillStyle=C.bg;g.fillRect(806,r.y-29,308,58);text(g,r.label,960,r.y,29,r.color,500,'center');
      const phase=(t*.64+i*.27)%1;packet(g,[[a,r.y],[b,r.y]],phase,r.color,5);
    }
  });
  reveal(g,t,.33,()=>{
    line(g,[[120,695],[1800,695]],C.faint,1.5);text(g,q[10],120,738,31,C.muted);
    text(g,q[11],260,824,29,C.muted,500,'center');text(g,q[12],925,794,33,C.blue,500,'center');text(g,q[13],925,857,33,C.darkGreen,500,'center');
    text(g,q[14],1610,794,32,C.ink,500,'center');text(g,q[15],1610,845,27,C.muted,400,'center');
    const branches=[{y:794,color:C.blue},{y:857,color:C.darkGreen}];
    for(let i=0;i<2;i++) {
      const b=branches[i],a=[[396,824],[500,824],[500,b.y],[735,b.y]],z=[[1115,b.y],[1320,b.y],[1320,824],[1405,824]];
      line(g,a,b.color,1.6);arrow(g,700,b.y,735,b.y,b.color,1.6);line(g,z,b.color,1.6);
      packet(g,a,(t*.52+i*.5)%1,b.color,4);packet(g,z,(t*.52+i*.5+.2)%1,b.color,4);
    }
    arrow(g,1360,824,1405,824,C.line,1.8);
  });
}

function tracking(g,t) {
  if(observationFrames.length!==3)throw new Error('Call and await init() before drawing tracking_short.');
  const q=visibleText.tracking_short;heading(g,q[0],t);
  const progress=trackingProgress(t);
  const result=model(g,'tracking',72,225,1110,548,t,{progress});
  reveal(g,t,.08,()=>text(g,q[8],627,238,29,C.muted,400,'center'));
  reveal(g,t,.09,()=>text(g,q[9],1352,228,29,C.muted));
  for(let i=0;i<3;i++)reveal(g,t,observationTimes[i],()=>{
    const x=1303,y=289+i*212;
    text(g,q[1+i],x,y-21,29,C.muted);
    g.fillStyle='rgba(255,255,255,.42)';g.beginPath();g.roundRect(x-5,y-4,310,178,7);g.fill();
    g.drawImage(observationFrames[i],x,y,300,168.75);
    text(g,q[4],x+285,y+145,23,C.blue,600,'right','DIN');
    line(g,[[1269,y+48],[1290,y+48]],C.line,1.6);
    if(i>0)arrow(g,1256,y-94,1256,y+48,C.line,1.5);
  },.2);
  const p=result&&result.landmarks&&result.landmarks.object;
  if(p)reveal(g,t,.1,()=>{
    const x=Array.isArray(p)?p[0]:p.x,y=Array.isArray(p)?p[1]:p.y;
    line(g,[[x,y-31],[x,y-85]],C.blue,1.6);text(g,q[4],x,y-106,31,C.blue,600,'center','DIN');
  });
  reveal(g,t,.2,()=>{
    const confirmed=t>=2.45;
    text(g,confirmed?q[6]:q[5],630,810,46,confirmed?C.darkGreen:C.muted,600,'center','CJK Heavy');
    text(g,q[7],630,875,29,C.muted,400,'center');
  });
}

function trigger(g,t) {
  const q=visibleText.trigger_short;heading(g,q[0],t,53);
  reveal(g,t,.08,()=>text(g,q[1],120,255,31,C.muted));
  const crossAt=1.15,progress=mix(.1,.9,smooth((t-.15)/2));
  const result=model(g,'trigger',60,285,1120,425,t,{progress});
  const marks=result&&result.landmarks;
  if(marks&&marks.triggerLine){
    const points=marks.triggerLine.map(p=>Array.isArray(p)?p:[p.x,p.y]);
    g.save();g.setLineDash([8,7]);line(g,points,C.blue,2.6);g.restore();
    const [x,y]=points[0];
    reveal(g,t,.08,()=>text(g,q[2],x,y-35,27,C.blue,500,'center'));
  } else reveal(g,t,.08,()=>text(g,q[2],638,323,27,C.blue,500,'center'));
  reveal(g,t,.12,()=>text(g,t<crossAt?q[3]:q[4],627,712,31,t<crossAt?C.muted:C.darkGreen,500,'center'));
  if(marks&&marks.object){const p=marks.object,x=Array.isArray(p)?p[0]:p.x,y=Array.isArray(p)?p[1]:p.y;text(g,q[13],x,y-70,28,C.blue,600,'center','DIN');}
  reveal(g,t,.13,()=>text(g,q[5],1290,282,34,C.blue,500));
  const active=[t>=.2,t>=.2,t>=crossAt];
  for(let i=0;i<3;i++)reveal(g,t,.14+i*.04,()=>{
    const x=1290,y=376+i*112;
    g.strokeStyle=active[i]?C.darkGreen:C.line;g.lineWidth=2.4;g.beginPath();g.arc(x+17,y,17,0,Math.PI*2);g.stroke();
    if(active[i])line(g,[[x+7,y],[x+15,y+7],[x+28,y-9]],C.darkGreen,2.8);
    text(g,q[6+i],x+59,y,32,active[i]?C.ink:C.muted);
  });
  const ats=[1.38,1.84,2.35],xs=[120,734,1360];
  for(let i=0;i<3;i++)reveal(g,t,ats[i],()=>{
    const x=xs[i];text(g,q[i===0?9:i===1?10:12],x,801,33,i===2?C.darkGreen:C.blue,500);
    if(i===0){line(g,[[x,858],[x+355,858]],C.faint,3);line(g,[[x,858],[x+355*smooth((t-ats[0])/.45),858]],C.blue,4);}
    if(i===1){line(g,[[x,857],[x+78,857],[x+78,829],[x+238,829],[x+238,857],[x+350,857]],C.blue,3);text(g,q[11],x+158,885,23,C.muted,400,'center');}
    if(i<2)arrow(g,x+425,835,x+545,835,C.line,1.8);
    else line(g,[[x,854],[x+16,870],[x+47,838]],C.darkGreen,4);
  },.19);
}

function dimensions(g,t) {
  const q=visibleText.dimensions;heading(g,q[0],t);
  reveal(g,t,.08,()=>text(g,q[1],120,257,32,C.muted));
  const viewports=[{x:110,y:360,w:320,h:285},{x:493,y:347,w:350,h:310},{x:895,y:357,w:295,h:290}];
  viewports.forEach((v,i)=>{
    const result=model(g,'dimensions',v.x,v.y,v.w,v.h,t+i*.35,{progress:.5,variant:i});
    const bounds=result&&result.landmarks&&result.landmarks.bounds;
    const b=bounds||{x:v.x+v.w*.16,y:v.y+v.h*.30,w:v.w*.68,h:v.h*.40};
    reveal(g,t,.16+i*.08,()=>{
      const x=b.x-12,y=b.y-12,w=b.w+24,h=b.h+24;
      g.strokeStyle=i===1?C.blue:C.darkGreen;g.lineWidth=2;g.strokeRect(x,y,w,h);
      line(g,[[x,y-10],[x,y-64]],C.line,1.3);line(g,[[x+w,y-10],[x+w,y-64]],C.line,1.3);
      arrow(g,x,y-49,x+w,y-49,i===1?C.blue:C.darkGreen,1.8,true);text(g,q[4],x+w/2,y-76,28,C.ink,500,'center','DIN');
      line(g,[[x+w+7,y],[x+w+46,y]],C.line,1.3);line(g,[[x+w+7,y+h],[x+w+46,y+h]],C.line,1.3);
      arrow(g,x+w+35,y,x+w+35,y+h,i===1?C.blue:C.darkGreen,1.8,true);text(g,q[5],x+w+56,y+h/2,28,C.ink,500,'center','DIN');
      text(g,q[2],x,y-18,23,C.muted);text(g,q[3],x+w,y+h+26,23,C.muted,500,'right');
    });
  });
  reveal(g,t,.29,()=>{
    line(g,[[1270,302],[1270,753]],C.faint,1.5);
    text(g,q[6],1374,328,34,C.blue,500);text(g,q[7],1374,451,42,C.ink,400,'left','DIN');text(g,q[8],1374,548,42,C.ink,400,'left','DIN');
    text(g,q[9],1374,691,31,C.muted);text(g,q[10],960,854,36,C.ink,500,'center');
  });
}

function alerts(g,t) {
  const q=visibleText.alerts;heading(g,q[0],t);
  reveal(g,t,.08,()=>text(g,q[1],120,280,31,C.muted));
  for(let i=0;i<2;i++)reveal(g,t,.12+i*.08,()=>{
    const y=396+i*205;text(g,q[2+i],120,y,39,C.ink,500);line(g,[[120,y+56],[416,y+56]],C.line,1.5);
  });
  model(g,'chip',658,313,480,366,t,{variant:2,progress:smooth(t/.6),role:'edge'});
  reveal(g,t,.13,()=>{
    text(g,q[4],900,276,39,C.blue,600,'center');text(g,q[5],900,731,35,C.ink,500,'center');text(g,q[6],900,793,32,C.muted,500,'center');
  });
  const inputs=[[[434,396],[552,396],[552,499],[650,499]],[[434,601],[552,601],[552,499],[650,499]]];
  const network=[[1150,499],[1290,499],[1290,375],[1430,375]],sd=[[1150,499],[1290,499],[1290,676],[1430,676]];
  reveal(g,t,.25,()=>{
    for(let i=0;i<inputs.length;i++){line(g,inputs[i],C.blue,2);packet(g,inputs[i],(t*.56+i*.5)%1,C.blue,5);}
    line(g,network,C.darkGreen,2);g.save();g.setLineDash([8,8]);line(g,sd,C.blue,2);g.restore();
    arrow(g,1390,375,1430,375,C.darkGreen,2);arrow(g,1390,676,1430,676,C.blue,2);
    packet(g,network,(t*.42+.2)%1,C.green,5);packet(g,sd,(t*.4+.65)%1,C.blue,5);
    text(g,q[7],1472,351,39,C.ink,500);text(g,q[8],1472,413,29,C.muted);
    text(g,q[9],1472,651,36,C.ink,500);text(g,q[10],1472,714,29,C.muted);
  });
}

function end(g,t) {
  const q=visibleText.end;heading(g,q[0],t,58);
  model(g,'hero',45,235,637,640,t,{variant:0,progress:smooth(t/.8),energy:.9});
  const rows=[{title:q[1],details:[q[2]],color:C.darkGreen},{title:q[3],details:[q[4],q[5]],color:C.darkGreen},{title:q[6],details:[q[7],q[8]],color:C.blue},{title:q[9],details:[q[10],q[11]],color:C.blue}];
  rows.forEach((row,i)=>reveal(g,t,.12+i*.065,()=>{
    const x=774,y=278+i*157;g.fillStyle=row.color;g.fillRect(x-35,y-18,4,34);
    text(g,row.title,x,y,40,C.ink,600,'left','CJK Heavy');
    row.details.forEach((s,j)=>text(g,s,x,y+52+j*42,30,C.muted,400));
    if(i<3)line(g,[[x,y+120],[1788,y+120]],C.faint,1.3);
  },.24));
}

function draw(g,name,t,duration=durations[name]) {
  if(!Object.hasOwn(durations,name))throw new Error('Unknown B scene: '+name);
  if(!Number.isFinite(t)||!Number.isFinite(duration)||duration<=0)throw new Error('Invalid scene time or duration.');
  t=Math.max(0,Math.min(duration,t));background(g,t);
  if(chapters[name])chapter(g,name,t,duration);
  else ({opening_title:opening,isp_methods:isp,fewshot_value:fewshot,compression_pcie:compression,tracking_short:tracking,trigger_short:trigger,dimensions,alerts,end}[name])(g,t,duration);
  // All narrative labels end above y=910. The final 160 px belong to subtitles.
  g.save();g.globalAlpha=1;const fade=g.createLinearGradient(0,910,0,960);fade.addColorStop(0,'rgba(203,221,233,0)');fade.addColorStop(1,C.bg);g.fillStyle=fade;g.fillRect(0,910,1920,50);g.fillStyle=C.bg;g.fillRect(0,960,1920,120);g.restore();
  return g.canvas;
}

function writeManifest() {
  const timeline=JSON.parse(fs.readFileSync(path.join(__dirname,'../重剪_v19/timeline.json'),'utf8'));
  const sourceFiles=['scenes_b.cjs','geometry_b.cjs','../重剪_v19/edit_plan.json','../重剪_v19/timeline.json','../重剪_v17/isp_compression_v17.cjs','../重剪_v17/industrial_animations_v17.cjs','../重剪_v17/render_short_assets.cjs','../重剪_v17/end_v17.cjs','../重剪_v15/fewshot_v15.cjs','../重剪_v13/fewshot_v13.cjs','../重剪_v13/build_v13.py','../重剪_v12/chapter_cards_v12.cjs','../重剪_v9/animations_v9.cjs'];
  const manifest={
    width:1920,height:1080,fps:60,full_film_duration:179.8,replaced_scene_duration:Object.values(durations).reduce((a,b)=>a+b,0),style:'B',palette:C,
    geometry_owner:'geometry_b.cjs',geometry_contract:'drawModel(ctx, kind, {x,y,w,h,t,progress,variant,view}) -> {landmarks}; viewport uses absolute pixels; all solid geometry is supplied by the modeling module.',
    layout_owner:'scenes_b.cjs',subtitle_safe_area:{x:0,y:920,width:1920,height:160},
    rendering:'Evaluate draw(ctx,name,frame/60,duration) for every output frame. No baked video or duplicated still frames is used for animation. Historical observations are intentionally frozen image-plane snapshots after their acquisition time.',
    content_policy:'Original v19 visible content retained. Only tracking gains explicit same-camera / three-time-frame labels to repair the mistaken three-station interpretation. No 12+3 split or 70s timing is added.',
    visual_repairs:{tracking_short:'A single overhead camera and one live component; three fixed-camera historical image frames enter in acquisition order at their matching observed positions and retain ID 07. Added labels: 同一摄像头 / 连续帧.',trigger_short:'Virtual trigger line is explicitly drawn from the actual projected endpoints. Crossing time uses the same progress=.5 boundary as the component center.',compression_pcie:'JPEG and LZ77 are parallel optional branches that rejoin before PCIe → RK. The native RGB inference branch remains separate.'},
    sources:sourceFiles.map(p=>({file:path.resolve(__dirname,p),sha256:crypto.createHash('sha256').update(fs.readFileSync(path.join(__dirname,p))).digest('hex')})),
    scenes:timeline.filter(s=>Object.hasOwn(durations,s.name)).map(s=>({name:s.name,start:s.start,duration:s.duration,frames:Math.round(s.duration*60),visible_text:visibleText[s.name],source:s.source.file})),
    samples:Array.from({length:15},(_,i)=>{const file=path.resolve(__dirname,'../重剪_v13/sample_assets',`native_${i}.png`);return {file,width:512,height:288,sha256:crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')};}),
    sample_provenance:{original_rgb_directory:path.resolve(__dirname,'../../VisionTrainer/console_data/111f819a9c4541abb56ed33d6fe2b280/captures/33cafd67277b40f6814c251ab3a622b4/3b396a9e25d047bd980a7c4e4e66b4ce'),conversion_source:'../重剪_v13/fewshot_v13.cjs',pixel_verification:'sample_provenance_verified.json',display:'All 15 real source PNGs fitted to 154 x 86.625 px tiles; content is not synthesized.'},
    timing:{chapter_entry_seconds:.2,opening_entry_seconds:.49,tracking_observations_seconds:observationTimes,tracking_snapshot_progress:observationTimes.map(trackingProgress),tracking_confirmed_seconds:2.45,trigger_cross_seconds:1.15,trigger_delay_seconds:1.38,trigger_pulse_seconds:1.84,trigger_recorded_seconds:2.35},
    retained_photographic_scenes:timeline.filter(s=>!Object.hasOwn(durations,s.name)).map(s=>s.name),
    verification_scope:'Rendering code and local content audit only; no model load, board operation or FPGA change.'
  };
  fs.writeFileSync(path.join(__dirname,'content_manifest.json'),JSON.stringify(manifest,null,2),'utf8');return manifest;
}

module.exports={init,draw,durations,visibleText,writeManifest};
if(require.main===module) {
  (async()=>{
    await init();writeManifest();
    if(process.argv.includes('--manifest'))return;
    const names=process.argv.filter(x=>Object.hasOwn(durations,x));
    const canvas=createCanvas(1920,1080),g=canvas.getContext('2d');
    for(const name of names.length?names:['opening_title','chapter_logic','tracking_short','fewshot_value']) {
      const t=name==='tracking_short'?2.65:Math.min(durations[name]*.72,2.2);draw(g,name,t,durations[name]);fs.writeFileSync(path.join(__dirname,`${name}_b_preview.png`),canvas.toBuffer('image/png'));
    }
  })().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;});
}
