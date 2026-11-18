'use strict';

// C is a flat optical / scientific-publication composition. The only 3D
// objects are the frozen Astra models, converted to a warm monochrome print.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {createCanvas,loadImage,GlobalFonts}=require('../../dataflow_film_20261028/node_modules/@napi-rs/canvas');
const geometry=require('../重剪_v21/geometry_b.cjs');
const {durations,visibleText}=require('../重剪_v21/scenes_b.cjs');
const C={black:'#100f0c',white:'#f2e6c9',red:'#e54828',yellow:'#edb731',ink:'#242119',muted:'#b5aa91',rule:'#5d5748'};
const clamp=x=>Math.max(0,Math.min(1,x));
const smooth=x=>{x=clamp(x);return x*x*x*(10+x*(-15+6*x));};
const mix=(a,b,p)=>a+(b-a)*p;
const observationTimes=[.18,.94,1.7];
const trackingProgress=t=>mix(.1,.9,smooth(t/2.4));
const chapters={chapter_board:{number:'01',title:'板卡介绍',subtitle:'RK · FPGA · 控制板'},chapter_console:{number:'02',title:'控制台操作',subtitle:'画面 · 标注 · 训练 · 运行'},chapter_logic:{number:'03',title:'识别与工业检测',subtitle:'识别 · 测量 · 告警'},application:{number:'04',title:'实战与应用',subtitle:'姿态分拣 · 类别分拣'}};
const surfaces=new Map(),staticModels=new Map();
let samples=[],observationFrames=[];

function text(g,s,x,y,size=34,color=C.white,weight=500,align='left',font='CJK') {
  g.font=`${weight} ${size}px "${font}",sans-serif`;g.textAlign=align;g.textBaseline='middle';g.fillStyle=color;g.fillText(s,x,y);
}
function line(g,points,color=C.white,width=2) {
  g.beginPath();points.forEach(([x,y],i)=>i?g.lineTo(x,y):g.moveTo(x,y));g.strokeStyle=color;g.lineWidth=width;g.lineCap='butt';g.lineJoin='miter';g.stroke();
}
function poly(g,points,fill,stroke,width=1) {
  g.beginPath();points.forEach(([x,y],i)=>i?g.lineTo(x,y):g.moveTo(x,y));g.closePath();if(fill){g.fillStyle=fill;g.fill();}if(stroke){g.strokeStyle=stroke;g.lineWidth=width;g.stroke();}
}
function sheet(g,x,y,w,h,fill=C.white,cut=24,shadow=true) {
  const points=[[x+cut,y],[x+w,y],[x+w,y+h-cut],[x+w-cut,y+h],[x,y+h],[x,y+cut]];
  if(shadow)poly(g,points.map(([a,b])=>[a+8,b+6]),C.red);
  poly(g,points,fill);
}
function arrow(g,x1,y1,x2,y2,color=C.white,width=2,double=false) {
  line(g,[[x1,y1],[x2,y2]],color,width);const a=Math.atan2(y2-y1,x2-x1);
  for(const [x,y,q]of double?[[x2,y2,a],[x1,y1,a+Math.PI]]:[[x2,y2,a]])line(g,[[x-10*Math.cos(q-.42),y-10*Math.sin(q-.42)],[x,y],[x-10*Math.cos(q+.42),y-10*Math.sin(q+.42)]],color,width);
}
function reveal(g,t,at,fn,span=.18) {
  const p=smooth((t-at)/span);if(p<=0)return;g.save();g.globalAlpha*=p;fn(p);g.restore();
}
function wipe(g,t,at,x,y,w,h,fn,span=.2) {
  const p=smooth((t-at)/span);if(p<=0)return;g.save();g.beginPath();g.rect(x,y,w*p,h);g.clip();fn(p);g.restore();
}
function packet(g,points,phase,color=C.yellow) {
  const lengths=points.slice(1).map((p,i)=>Math.hypot(p[0]-points[i][0],p[1]-points[i][1]));let d=clamp(phase)*lengths.reduce((a,b)=>a+b,0),i=0;
  while(i<lengths.length-1&&d>lengths[i]){d-=lengths[i];i++;}const q=lengths[i]?d/lengths[i]:0,x=mix(points[i][0],points[i+1][0],q),y=mix(points[i][1],points[i+1][1],q);
  g.save();g.globalAlpha*=Math.min(1,phase*12,(1-phase)*12);g.fillStyle=color;g.fillRect(x-5,y-5,10,10);g.restore();
}
function model(g,kind,x,y,w,h,options={}) {
  const key=[kind,w,h,options.view||'',options.tone||'white',options.progress??.5,options.variant||0].join('|');
  let result=options.static?staticModels.get(key):null;
  if(!result){
    const size=`${w}x${h}`;let surface=surfaces.get(size);
    if(!surface){const canvas=createCanvas(w,h);surface={canvas,ctx:canvas.getContext('2d')};surfaces.set(size,surface);}
    const a=surface.ctx;a.reset();a.clearRect(0,0,w,h);
    const rendered=geometry.drawModel(a,kind,{...options,x:0,y:0,w,h,t:options.t??0,quality:1.5});
    const image=a.getImageData(0,0,w,h),d=image.data;
    const high=options.tone==='red'?[229,72,40]:options.tone==='yellow'?[237,183,49]:[242,230,201],low=[30,28,22];
    for(let i=0;i<d.length;i+=4){if(!d[i+3])continue;const p=clamp((d[i]*.2126+d[i+1]*.7152+d[i+2]*.0722-16)/225);d[i]=mix(low[0],high[0],p);d[i+1]=mix(low[1],high[1],p);d[i+2]=mix(low[2],high[2],p);}
    a.putImageData(image,0,0);const snapshot=options.static?createCanvas(w,h):surface.canvas;if(options.static)snapshot.getContext('2d').drawImage(surface.canvas,0,0);result={canvas:snapshot,landmarks:rendered.landmarks};
    if(options.static)staticModels.set(key,result);
  }
  g.drawImage(result.canvas,x,y);
  const landmarks={};
  for(const [name,v]of Object.entries(result.landmarks)){
    if(name==='bounds')landmarks[name]={x:v.x+x,y:v.y+y,w:v.w,h:v.h};
    else if(Array.isArray(v))landmarks[name]=v.map(p=>({x:p.x+x,y:p.y+y,z:p.z}));
    else landmarks[name]={x:v.x+x,y:v.y+y,z:v.z};
  }
  return landmarks;
}

async function init() {
  for(const [file,alias]of [['msyh.ttc','CJK'],['msyhbd.ttc','CJK Heavy'],['bahnschrift.ttf','DIN']])GlobalFonts.registerFromPath('C:/Windows/Fonts/'+file,alias);
  samples=await Promise.all(Array.from({length:15},(_,i)=>loadImage(path.join(__dirname,'../重剪_v13/sample_assets',`native_${i}.png`))));
  observationFrames=observationTimes.map(at=>{
    const c=createCanvas(512,288),g=c.getContext('2d');g.fillStyle=C.ink;g.fillRect(0,0,512,288);
    line(g,[[0,65],[512,65]],C.rule,2);line(g,[[0,220],[512,220]],C.rule,2);
    model(g,'resistor',0,0,512,288,{view:'top',progress:trackingProgress(at),static:true});return c;
  });
}
function background(g) {
  g.reset();g.fillStyle=C.black;g.fillRect(0,0,1920,1080);
}
function heading(g,s,t,size=56) {
  wipe(g,t,0,120,81,1680,95,()=>text(g,s,120,125,size,C.white,700,'left','CJK Heavy'),.18);
  reveal(g,t,.03,p=>{line(g,[[120,191],[1796,191]],C.rule,1.4);line(g,[[120,191],[120+282*p,191]],C.red,6);});
}
function aperture(g,cx,cy,r,t,number='') {
  const p=smooth(t/.32),rot=(1-p)*.16,points=(rad,angle=0,dx=0,dy=0)=>Array.from({length:6},(_,i)=>{const a=(30+i*60)*Math.PI/180+angle;return[cx+dx+Math.cos(a)*rad,cy+dy+Math.sin(a)*rad];});
  g.save();g.beginPath();g.rect(cx-r-35,cy-r-40,2*r+120,(2*r+90)*p);g.clip();
  poly(g,points(r+12,0,9,6),null,C.red,7);
  const outer=points(r,rot),inner=points(r-57,-rot*.5);
  for(let i=0;i<6;i++)poly(g,[outer[i],outer[(i+1)%6],inner[(i+1)%6],inner[i]],i%3===0?C.yellow:C.white,C.black,2);
  poly(g,points(r-75),null,C.white,1.1);poly(g,points(r-88),null,C.red,1.2);
  for(const dy of [-137,137])line(g,[[cx+r-15,cy+dy],[cx+r+61,cy+dy]],C.red,7);
  for(let i=0;i<9;i++)line(g,[[cx+r+49,cy-105+i*27],[cx+r+49+(i%4===0?23:10),cy-105+i*27]],C.white,1.5);
  if(number)text(g,number,cx,cy,245,C.white,500,'center','DIN');g.restore();
}
function chapter(g,name,t) {
  const c=chapters[name],p=smooth(t/.18);aperture(g,1370,458,322,t,c.number);
  reveal(g,t,0,()=>{g.fillStyle=C.red;g.fillRect(124,277,342*p,11);g.fillStyle=C.white;g.fillRect(124,302,68*p,4);},.18);
  wipe(g,t,.02,120,381,850,150,()=>text(g,c.title,120,453,name==='chapter_logic'?88:96,C.white,700,'left','CJK Heavy'),.16);
  wipe(g,t,.04,124,550,850,80,()=>text(g,c.subtitle,126,586,39,C.yellow),.16);
  reveal(g,t,.08,()=>{
    line(g,[[124,726],[855,726]],C.muted,1.4);line(g,[[124,711],[124,741]],C.red,3);line(g,[[855,711],[855,741]],C.red,3);
    for(let i=0;i<24;i++){const x=124+i*731/23;line(g,[[x,726],[x,726+(i%4===0?23:9)]],i%4===0?C.white:C.muted,1.3);}
    line(g,[[124,874],[1796,874]],C.rule,1.2);line(g,[[124,874],[222,874]],C.red,5);
  },.17);
}
function opening(g,t) {
  const q=visibleText.opening_title;aperture(g,1450,454,285,t);
  wipe(g,t,0,120,277,1100,111,()=>text(g,q[0],120,337,80,C.white,700,'left','CJK Heavy'),.2);
  wipe(g,t,.055,120,392,1100,116,()=>text(g,q[1],120,452,84,C.white,700,'left','CJK Heavy'),.2);
  wipe(g,t,.11,124,600,1050,84,()=>text(g,q[2],124,641,32,C.yellow),.2);
  reveal(g,t,.14,p=>{
    line(g,[[124,552],[124+610*p,552]],C.red,8);
    line(g,[[124,801],[985,801]],C.rule,1.5);
    for(let i=0;i<28;i++){const x=124+i*861/27;line(g,[[x,801],[x,801+(i%3===0?22:9)]],i%3===0?C.white:C.muted,1.3);}
  });
}
function isp(g,t) {
  const q=visibleText.isp_methods;heading(g,q[0],t);
  wipe(g,t,.06,120,278,530,485,()=>{
    sheet(g,124,282,520,467,C.ink,30);model(g,'chip',135,331,490,310,{static:true});
    for(let i=0;i<4;i++){line(g,[[164,664+i*18],[602,664+i*18]],i===1?C.red:C.white,i===1?3:1.5);packet(g,[[164,664+i*18],[602,664+i*18]],(t*.65+i*.2)%1,C.yellow);}
  },.24);
  reveal(g,t,.1,()=>text(g,q[1],759,271,35,C.yellow));
  for(let i=0;i<8;i++)wipe(g,t,.12+i*.035,757+(i%2)*530,337+Math.floor(i/2)*108,495,92,()=>{
    const x=759+(i%2)*530,y=374+Math.floor(i/2)*108;
    line(g,[[x,y-27],[x,y+26]],i%2?C.yellow:C.red,5);text(g,q[i+2],x+27,y,i===5?31:34,C.white);
    line(g,[[x+27,y+45],[x+462,y+45]],C.rule,1.2);
  },.2);
  reveal(g,t,.48,()=>{line(g,[[124,798],[1796,798]],C.rule,1.4);text(g,q[10],960,853,34,C.muted,500,'center');});
}
function fewshot(g,t) {
  if(samples.length!==15)throw new Error('Call and await init() before drawing fewshot_value.');
  const q=visibleText.fewshot_value;heading(g,q[0],t);
  reveal(g,t,.06,()=>{text(g,q[1],124,260,34,C.muted);sheet(g,124,299,986,442,C.white,28);});
  for(let i=0;i<15;i++)wipe(g,t,.1+i*.025,148+(i%5)*187,329+Math.floor(i/5)*129,170,98,()=>{
    g.drawImage(samples[i],148+(i%5)*187,330+Math.floor(i/5)*129,170,95.625);
  },.2);
  reveal(g,t,.3,()=>text(g,q[3],1200,261,32,C.muted));
  wipe(g,t,.36,1130,336,350,380,()=>{model(g,'chip',1152,356,310,263,{static:true});text(g,q[2],1307,665,32,C.yellow,500,'center');},.24);
  reveal(g,t,.42,()=>{
    arrow(g,1125,494,1173,494,C.red,2.4);arrow(g,1467,494,1519,494,C.red,2.4);
    packet(g,[[1125,494],[1173,494]],(t*.65)%1,C.yellow);packet(g,[[1467,494],[1519,494]],(t*.65+.4)%1,C.yellow);
  });
  for(let i=0;i<3;i++)wipe(g,t,.48+i*.12,1541,330+i*129,250,100,()=>{
    const y=378+i*129;text(g,q[4+i],1548,y,57,C.yellow,600,'left','DIN');line(g,[[1618,y],[1794,y]],C.white,1.4);packet(g,[[1618,y],[1794,y]],(t*.5+i*.25)%1,C.red);
  },.2);
  wipe(g,t,.95,124,798,1672,94,()=>{line(g,[[124,798],[1796,798]],C.rule,1.4);text(g,q[7],960,852,37,C.white,500,'center');},.25);
}
function compression(g,t) {
  const q=visibleText.compression_pcie;heading(g,q[0],t,54);
  reveal(g,t,.06,()=>text(g,q[1],124,254,31,C.yellow));
  wipe(g,t,.08,120,292,310,360,()=>{model(g,'chip',124,305,300,240,{tone:'red',static:true});text(g,q[2],274,576,41,C.white,600,'center','DIN');text(g,q[3],274,626,29,C.muted,400,'center');},.22);
  wipe(g,t,.11,1480,292,310,360,()=>{model(g,'chip',1482,305,300,240,{static:true});text(g,q[8],1632,576,41,C.white,600,'center','DIN');text(g,q[9],1632,626,29,C.muted,400,'center');},.22);
  reveal(g,t,.12,()=>{
    text(g,q[4],960,288,39,C.white,600,'center','DIN');
    for(const [i,r]of [{y:370,label:q[5],right:true,color:C.yellow},{y:463,label:q[6],right:false,color:C.red},{y:556,label:q[7],right:true,color:C.yellow}].entries()){
      const a=r.right?478:1444,b=r.right?1444:478;arrow(g,a,r.y,b,r.y,r.color,2.1);g.fillStyle=C.white;g.fillRect(803,r.y-26,314,52);text(g,r.label,960,r.y,29,C.ink,500,'center');
      packet(g,[[a,r.y],[b,r.y]],(t*.57+i*.26)%1,r.color);
    }
  });
  reveal(g,t,.3,()=>{
    line(g,[[124,676],[1796,676]],C.rule,1.4);text(g,q[10],124,714,30,C.muted);
    text(g,q[11],260,820,29,C.white,500,'center');text(g,q[12],925,783,32,C.yellow,500,'center');text(g,q[13],925,854,32,C.white,500,'center');
    text(g,q[14],1615,788,31,C.white,500,'center');text(g,q[15],1615,845,27,C.muted,400,'center');
    for(const [i,b]of [{y:783,color:C.yellow},{y:854,color:C.white}].entries()){
      const a=[[396,820],[500,820],[500,b.y],[735,b.y]],z=[[1115,b.y],[1310,b.y],[1310,820],[1412,820]];line(g,a,b.color,1.8);arrow(g,698,b.y,735,b.y,b.color,1.8);line(g,z,b.color,1.8);
      packet(g,a,(t*.5+i*.5)%1,C.red);packet(g,z,(t*.5+i*.5+.2)%1,C.red);
    }arrow(g,1370,820,1412,820,C.white,1.8);
  });
}
function tracking(g,t) {
  const q=visibleText.tracking_short;heading(g,q[0],t);
  const marks=model(g,'tracking',70,218,1140,395,{progress:trackingProgress(t)});
  reveal(g,t,.08,()=>{
    const c=marks.camera;line(g,[[c.x,c.y-31],[c.x,c.y-63]],C.red,1.5);text(g,q[8],c.x,c.y-85,28,C.muted,400,'center');
    const a=marks.object;line(g,[[a.x,a.y-24],[a.x,a.y-69]],C.red,1.8);text(g,q[4],a.x,a.y-88,31,C.yellow,600,'center','DIN');
  });
  reveal(g,t,.16,()=>{
    g.fillStyle=t>=2.45?C.yellow:C.red;g.fillRect(1304,344,8,94);
    text(g,t>=2.45?q[6]:q[5],1340,384,43,t>=2.45?C.yellow:C.white,600,'left','CJK Heavy');
    text(g,q[7],1304,488,28,C.muted);text(g,q[9],124,610,28,C.muted);
  });
  for(let i=0;i<3;i++)wipe(g,t,observationTimes[i],120+i*570,646,575,247,()=>{
    const x=124+i*570,y=650;sheet(g,x,y,526,230,C.white,25);
    text(g,q[1+i],x+23,y+27,27,C.ink,500);g.drawImage(observationFrames[i],x+23,y+57,270,151.875);
    text(g,q[4],x+410,y+130,40,C.ink,600,'center','DIN');line(g,[[x+332,y+169],[x+489,y+169]],C.red,5);
    if(i<2)arrow(g,x+532,y+120,x+563,y+120,C.red,2.4);
  },.2);
}
function trigger(g,t) {
  const q=visibleText.trigger_short;heading(g,q[0],t,53);
  reveal(g,t,.06,()=>{text(g,q[1],124,255,30,C.muted);sheet(g,124,293,1040,357,C.ink,26);});
  const marks=model(g,'trigger',134,307,1014,324,{progress:mix(.1,.9,smooth((t-.15)/2))});
  reveal(g,t,.08,()=>{
    const points=marks.triggerLine.map(p=>[p.x,p.y]);g.save();g.setLineDash([8,6]);line(g,points,C.red,3);g.restore();
    const p=marks.triggerLine[0],a=marks.object;line(g,[[p.x,p.y-5],[p.x,p.y-45],[p.x-38,p.y-45]],C.red,1.5);text(g,q[2],p.x-46,p.y-45,25,C.red,500,'right');
    line(g,[[a.x,a.y-20],[a.x,a.y-63]],C.yellow,1.6);text(g,q[13],a.x,a.y-83,28,C.yellow,600,'center','DIN');
    text(g,t<1.15?q[3]:q[4],644,692,31,t<1.15?C.muted:C.yellow,500,'center');
  });
  reveal(g,t,.1,()=>text(g,q[5],1283,281,33,C.yellow));
  for(let i=0;i<3;i++)reveal(g,t,.12+i*.03,()=>{
    const x=1284,y=372+i*109,active=i<2?t>=.2:t>=1.15;g.strokeStyle=active?C.yellow:C.rule;g.lineWidth=2;g.strokeRect(x,y-16,32,32);
    if(active)line(g,[[x+6,y],[x+13,y+7],[x+27,y-10]],C.yellow,3);text(g,q[6+i],x+59,y,31,active?C.white:C.muted);
  });
  for(const [i,at]of [1.38,1.84,2.35].entries())wipe(g,t,at,120+i*568,754,544,145,()=>{
    const x=124+i*568;g.fillStyle=i===2?C.yellow:C.white;g.fillRect(x,759,388,52);text(g,q[i===0?9:i===1?10:12],x+18,785,31,C.ink,600);
    if(i===0){line(g,[[x,856],[x+388,856]],C.rule,3);line(g,[[x,856],[x+388*smooth((t-at)/.45),856]],C.red,4);}
    if(i===1){line(g,[[x,856],[x+79,856],[x+79,832],[x+263,832],[x+263,856],[x+388,856]],C.yellow,3);text(g,q[11],x+171,885,23,C.muted,400,'center');}
    if(i===2)line(g,[[x+17,847],[x+35,865],[x+70,831]],C.yellow,4);
    if(i<2)arrow(g,x+421,827,x+533,827,C.red,2);
  },.18);
}
function dimensions(g,t) {
  const q=visibleText.dimensions;heading(g,q[0],t);
  reveal(g,t,.06,()=>text(g,q[1],124,255,32,C.muted));
  for(let i=0;i<3;i++){
    const marks=model(g,'dimensions',125+i*375,362,300,260,{static:true,variant:i}),b=marks.bounds,c=i===1?C.yellow:C.red;
    reveal(g,t,.12+i*.07,()=>{
      const x=b.x-12,y=b.y-12,w=b.w+24,h=b.h+24;g.strokeStyle=c;g.lineWidth=2.1;g.strokeRect(x,y,w,h);
      line(g,[[x,y-10],[x,y-62]],C.muted,1.2);line(g,[[x+w,y-10],[x+w,y-62]],C.muted,1.2);arrow(g,x,y-49,x+w,y-49,c,1.8,true);text(g,q[4],x+w/2,y-76,28,C.white,500,'center','DIN');
      line(g,[[x+w+7,y],[x+w+45,y]],C.muted,1.2);line(g,[[x+w+7,y+h],[x+w+45,y+h]],C.muted,1.2);arrow(g,x+w+35,y,x+w+35,y+h,c,1.8,true);text(g,q[5],x+w+57,y+h/2,28,C.white,500,'center','DIN');
      text(g,q[2],x,y-18,23,C.muted);text(g,q[3],x+w,y+h+28,23,C.muted,500,'right');
    });
  }
  wipe(g,t,.2,1274,292,525,467,()=>{
    sheet(g,1278,296,510,448,C.white,28);text(g,q[6],1315,348,33,C.ink,600);line(g,[[1315,394],[1740,394]],C.red,3);
    text(g,q[7],1315,465,41,C.ink,400,'left','DIN');text(g,q[8],1315,563,41,C.ink,400,'left','DIN');text(g,q[9],1315,687,30,C.ink);
  },.23);
  wipe(g,t,.4,124,801,1672,91,()=>{line(g,[[124,801],[1796,801]],C.rule,1.4);text(g,q[10],960,853,35,C.white,500,'center');},.22);
}
function alerts(g,t) {
  const q=visibleText.alerts;heading(g,q[0],t);
  reveal(g,t,.06,()=>text(g,q[1],124,267,31,C.muted));
  for(let i=0;i<2;i++)wipe(g,t,.1+i*.08,120,326+i*207,386,126,()=>{
    const y=330+i*207;sheet(g,124,y,370,111,C.white,20);text(g,q[2+i],148,y+55,35,C.ink);
  },.21);
  wipe(g,t,.16,650,315,574,368,()=>model(g,'alerts',654,319,560,352,{static:true}),.25);
  reveal(g,t,.1,()=>{text(g,q[4],932,269,37,C.yellow,600,'center');text(g,q[5],932,721,35,C.white,500,'center');text(g,q[6],932,788,31,C.muted,400,'center');});
  reveal(g,t,.28,()=>{
    const inputs=[[[506,386],[562,386],[562,499],[650,499]],[[506,593],[562,593],[562,499],[650,499]]],network=[[1226,499],[1320,499],[1320,389],[1426,389]],sd=[[1226,499],[1320,499],[1320,666],[1426,666]];
    for(let i=0;i<2;i++){line(g,inputs[i],C.white,1.8);packet(g,inputs[i],(t*.55+i*.5)%1,C.red);}
    line(g,network,C.yellow,2);g.save();g.setLineDash([8,7]);line(g,sd,C.white,1.8);g.restore();packet(g,network,(t*.4+.2)%1,C.red);packet(g,sd,(t*.4+.7)%1,C.yellow);
    arrow(g,1390,389,1426,389,C.yellow,2);arrow(g,1390,666,1426,666,C.white,1.8);
    sheet(g,1442,314,350,152,C.white,21);text(g,q[7],1472,360,36,C.ink);text(g,q[8],1472,420,29,C.ink);
    poly(g,[[1463,575],[1792,575],[1792,726],[1771,747],[1442,747],[1442,596]],C.ink,C.yellow,1.5);text(g,q[9],1472,629,33,C.white);text(g,q[10],1472,696,27,C.muted);
  });
}
function end(g,t) {
  const q=visibleText.end;heading(g,q[0],t,58);
  const cards=[{title:q[1],details:[q[2]],fill:C.white},{title:q[3],details:[q[4],q[5]],fill:C.ink},{title:q[6],details:[q[7],q[8]],fill:C.ink},{title:q[9],details:[q[10],q[11]],fill:C.yellow}];
  cards.forEach((card,i)=>wipe(g,t,.08+i*.055,120+(i%2)*861,279+Math.floor(i/2)*298,828,269,()=>{
    const x=124+(i%2)*861,y=283+Math.floor(i/2)*298,dark=card.fill===C.ink,ink=dark?C.white:C.ink;
    sheet(g,x,y,810,253,card.fill,28);line(g,[[x+34,y+37],[x+112,y+37]],C.red,6);text(g,card.title,x+35,y+88,39,ink,600,'left','CJK Heavy');
    card.details.forEach((s,j)=>text(g,s,x+35,y+153+j*46,30,dark?C.muted:C.ink,400));
  },.22));
}

function draw(g,name,t,duration=durations[name]) {
  if(!Object.hasOwn(durations,name))throw new Error('Unknown C scene: '+name);
  if(!Number.isFinite(t)||!Number.isFinite(duration)||duration<=0)throw new Error('Invalid scene time or duration.');
  t=Math.max(0,Math.min(duration,t));background(g);
  if(chapters[name])chapter(g,name,t,duration);
  else ({opening_title:opening,isp_methods:isp,fewshot_value:fewshot,compression_pcie:compression,tracking_short:tracking,trigger_short:trigger,dimensions,alerts,end}[name])(g,t,duration);
  g.save();g.globalAlpha=1;g.fillStyle=C.black;g.fillRect(0,920,1920,160);g.restore();return g.canvas;
}
function writeManifest() {
  const timeline=JSON.parse(fs.readFileSync(path.join(__dirname,'../重剪_v19/timeline.json'),'utf8'));
  const files=['scenes_c.cjs','../重剪_v21/geometry_b.cjs','../重剪_v21/scenes_b.cjs','../重剪_v21/content_audit.json','../视觉风格小样_v20/style_c.cjs','../视觉风格小样_v20/style_c_notes.md'];
  const manifest={style:'C optical scientific publication',width:1920,height:1080,fps:60,full_film_duration:179.8,replaced_scene_duration:48,palette:C,
    sources:files.map(p=>{const file=path.resolve(__dirname,p);return{file,sha256:crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')};}),
    geometry:'Only frozen ../重剪_v21/geometry_b.cjs objects; no added mesh. No hero sculpture or B palette. C warm monochrome mapping is applied to transparent model renders; all projected landmarks come from that same render.',
    timing:{sample_mode:'Direct frame/60 evaluation. No 15Hz or 20Hz time quantization.',chapter_text_settled:.2,chapter_aperture_settled:.32,chapter_reading_hold:'From .32s to end, intentional stable graphic',tracking_observations:observationTimes,tracking_snapshot_progress:observationTimes.map(trackingProgress),tracking_confirmed:2.45,trigger_cross:1.15,trigger_delay:1.38,trigger_pulse:1.84,trigger_record:2.35},
    intentional_holds:{opening_title:[.32,2],chapter_board:[.32,1.5],chapter_console:[.32,1.2],chapter_logic:[.32,1.2],application:[.32,2.7],tracking_short:[2.45,3.2],trigger_short:[2.53,3.2],dimensions:[.62,5],end:[.465,5]},
    continuous_action_scenes:{isp_methods:'Four parallel signal markers after mechanical entry.',fewshot_value:'Training and class signal paths; the 15 genuine sample photographs remain still.',compression_pcie:'Bidirectional signal paths and parallel optional compression routes.',tracking_short:'One live object moves at continuous progress; historical exposures stay fixed once acquired.',trigger_short:'The object moves across the exact projected virtual line before delay / pulse / record.',alerts:'Event and evidence-routing markers; optional SD is shown with a dashed route.'},
    subtitle_safe_area:{x:0,y:920,width:1920,height:160,background:C.black},
    scenes:timeline.filter(s=>Object.hasOwn(durations,s.name)).map(s=>({name:s.name,start:s.start,duration:s.duration,frames:Math.round(s.duration*60),visible_text:visibleText[s.name]})),
    authenticity:'Single overhead camera / one live resistor / three historical image-plane frames. Native RGB separate from parallel optional JPEG or LZ77. Virtual trigger line aligned with progress=.5. SD remains optional after enabling and mounting.',
    native_samples:'The unchanged 15 real 512x288 PNGs from ../重剪_v13/sample_assets; pixel-equality proof retained in ../重剪_v21/sample_provenance_verified.json.',
    retained_photographic_scenes:timeline.filter(s=>!Object.hasOwn(durations,s.name)).map(s=>s.name)
  };fs.writeFileSync(path.join(__dirname,'content_manifest.json'),JSON.stringify(manifest,null,2),'utf8');return manifest;
}
module.exports={init,draw,durations,visibleText,writeManifest};
if(require.main===module)(async()=>{
  await init();writeManifest();if(process.argv.includes('--manifest'))return;
  const canvas=createCanvas(1920,1080),g=canvas.getContext('2d'),names=process.argv.filter(s=>Object.hasOwn(durations,s));
  for(const name of names.length?names:['chapter_logic','tracking_short']){draw(g,name,name==='tracking_short'?2.7:durations[name]*.6);fs.writeFileSync(path.join(__dirname,name+'_c_preview.png'),canvas.toBuffer('image/png'));}
})().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;});
