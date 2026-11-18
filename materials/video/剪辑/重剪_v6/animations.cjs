'use strict';

const cards = require('./code_sources_v6.json');
const {createCanvas}=require('../../dataflow_film_20261028/node_modules/@napi-rs/canvas');
const durations = {title:5,end:6,parameter:18,coarsefine:14,async:20,tracking:26,collage:24};
const C={bg:'#0d1d29',panel:'#17313f',deep:'#102632',line:'#355768',ink:'#edf5f3',muted:'#a5bdc7',teal:'#78dfcb',gold:'#efcf8a'};
const T={
 title:['基于 FPGA 的实时视觉处理','与工件姿态识别系统','双向调参  ·  两阶段推理  ·  连续确认'],
 end:['从看见工件，到确认姿态','FPGA 实时视觉处理与工件姿态识别'],
 parameter:['配置下发，状态回读','控制台','RK','FPGA','成像参数','控制桥接','图像处理','配置命令','板端状态','采集','预处理','像素输出'],
 coarsefine:['先找到工件，再判断姿态','粗定位','全图定位工件','原生 RGB 裁剪','ROI','细推理','姿态判断','正反面','数字方向','473'],
 async:['显示持续运行，识别独立调度','显示链路','持续接收新画面','推理链路','独立执行识别','异步取图','定位与姿态识别','结果缓存','更新识别结果','473'],
 tracking:['同一工件，连续确认，一次触发','观测一','观测二','观测三','ID 07','跨帧关联','姿态确认','外接控制','连续观测','生成控制事件','已处理','继续关联','473'],
 collage:['每个模块，汇入同一条数据流','相机','FPGA','RK','执行设备','控制台','图像采集','像素预处理','粗定位','姿态识别','连续确认','成像调节  ·  现场训练  ·  运行监控']
};
const visibleText=[...new Set([...Object.values(T).flat(),...cards.flatMap(c=>[c.title,...c.lines])])];
const clamp=x=>Math.max(0,Math.min(1,x));
const ease=x=>{x=clamp(x);return x*x*(3-2*x)};
const mix=(a,b,t)=>a+(b-a)*t;
let cardImages;
const previewCanvas=createCanvas(1307,166),previewContext=previewCanvas.getContext('2d');

function text(g,s,x,y,size=30,col=C.ink,align='center'){
 g.font=`${size}px "Microsoft YaHei",sans-serif`;g.fillStyle=col;g.textAlign=align;g.textBaseline='middle';g.fillText(s,x,y);
}
function box(g,x,y,w,h,fill=C.panel,stroke=C.line,r=20){
 g.beginPath();g.roundRect(x,y,w,h,r);g.fillStyle=fill;g.fill();if(stroke){g.strokeStyle=stroke;g.lineWidth=1.5;g.stroke()}
}
function layer(g,a,fn){g.save();g.globalAlpha*=clamp(a);fn();g.restore()}
function appear(g,t,at,fn){layer(g,ease((t-at)/.8),fn)}
function wire(g,pts,t,col=C.teal,pulse=true){
 g.strokeStyle=col;g.lineWidth=2.5;g.beginPath();pts.forEach((p,i)=>i?g.lineTo(...p):g.moveTo(...p));g.stroke();
 const p=pts.at(-1),q=pts.at(-2),a=Math.atan2(p[1]-q[1],p[0]-q[0]);
 g.fillStyle=col;g.beginPath();g.moveTo(...p);g.lineTo(p[0]-12*Math.cos(a-.45),p[1]-12*Math.sin(a-.45));g.lineTo(p[0]-12*Math.cos(a+.45),p[1]-12*Math.sin(a+.45));g.closePath();g.fill();
 if(!pulse)return;const lengths=pts.slice(1).map((p,i)=>Math.hypot(p[0]-pts[i][0],p[1]-pts[i][1]));const total=lengths.reduce((a,b)=>a+b,0);
 let d=(t*.27%1)*total,i=0;while(i<lengths.length-1&&d>lengths[i])d-=lengths[i++];const u=d/lengths[i];
 g.shadowColor=col;g.shadowBlur=13;g.beginPath();g.arc(mix(pts[i][0],pts[i+1][0],u),mix(pts[i][1],pts[i+1][1],u),4,0,Math.PI*2);g.fill();g.shadowBlur=0;
}
function tick(g,x,y,col=C.teal,s=1){g.strokeStyle=col;g.lineWidth=4*s;g.lineCap='round';g.beginPath();g.moveTo(x-10*s,y);g.lineTo(x-2*s,y+8*s);g.lineTo(x+14*s,y-11*s);g.stroke();g.lineCap='butt'}
function part(g,x,y,a=0,s=1,print=true){
 g.save();g.translate(x,y);g.rotate(a);g.scale(s,s);g.shadowColor='#0009';g.shadowBlur=18;box(g,-64,-31,128,62,'#a4b6b7','#d5e5df',6);g.shadowBlur=0;
 g.fillStyle='#e2ebe7';g.fillRect(-66,-25,17,50);g.fillRect(49,-25,17,50);if(print)text(g,T.coarsefine[9],0,1,25,'#243c43');g.restore();
}
function bg(g){
 g.fillStyle=C.bg;g.fillRect(0,0,1920,1080);const b=g.createRadialGradient(970,410,80,970,470,1080);b.addColorStop(0,'#203d4b');b.addColorStop(.62,'#112834');b.addColorStop(1,C.bg);g.fillStyle=b;g.fillRect(0,0,1920,1080);
 g.strokeStyle='#afdad509';g.lineWidth=1;for(let x=90;x<1920;x+=90){g.beginPath();g.moveTo(x,210);g.lineTo(x,880);g.stroke()}
 for(let y=250;y<900;y+=90){g.beginPath();g.moveTo(90,y);g.lineTo(1830,y);g.stroke()}
 // Keep the subtitle safe area free of ornament and drawing.
 const shade=g.createLinearGradient(0,870,0,960);shade.addColorStop(0,'#0d1d2900');shade.addColorStop(1,C.bg);g.fillStyle=shade;g.fillRect(0,870,1920,210);
}
function title(g,t,end=false){
 const words=end?T.end:T.title;const a=ease(t/.8);layer(g,a,()=>{
 g.fillStyle=C.teal;g.fillRect(910,290,100,3);text(g,words[0],960,end?455:415,end?57:61);text(g,words[1],960,end?565:505,end?29:61,end?C.muted:C.ink);
 if(!end)text(g,words[2],960,642,29,C.teal);
 });
}
function parameter(g,t){
 const s=T.parameter;text(g,s[0],960,116,48);
 const xs=[115,740,1365];xs.forEach((x,i)=>appear(g,t,i*.4,()=>{box(g,x,285,440,450);text(g,s[1+i],x+220,341,39,i===2?C.teal:C.ink);g.fillStyle=C.line;g.fillRect(x+35,385,370,1)}));
 appear(g,t,.8,()=>{text(g,s[4],335,447,27,C.muted);for(let j=0;j<3;j++){const yy=505+j*62;g.strokeStyle=C.line;g.lineWidth=5;g.beginPath();g.moveTo(195,yy);g.lineTo(475,yy);g.stroke();g.fillStyle=j===1?C.gold:C.teal;g.beginPath();g.arc(195+[.32,.64,.44][j]*280,yy,9,0,Math.PI*2);g.fill()}});
 appear(g,t,1.5,()=>{text(g,s[5],960,447,27,C.muted);box(g,820,490,280,130,C.deep,C.line,12);for(let k=0;k<3;k++){g.fillStyle=k===1?C.gold:C.teal;g.beginPath();g.arc(885+k*75,555,8,0,Math.PI*2);g.fill()} });
 appear(g,t,2,()=>{text(g,s[6],1585,447,27,C.muted);const labels=s.slice(9);labels.forEach((v,i)=>{box(g,1400+i*125,516,120,73,C.deep,i===1?C.teal:C.line,10);text(g,v,1460+i*125,553,22,i===1?C.teal:C.ink)});wire(g,[[1521,553],[1524,553]],t,C.teal,false);wire(g,[[1646,553],[1649,553]],t,C.teal,false)});
 appear(g,t,4.8,()=>{wire(g,[[555,450],[740,450]],t,C.gold);wire(g,[[1180,450],[1365,450]],t,C.gold);text(g,s[7],960,238,28,C.gold)});
 appear(g,t,10.8,()=>{wire(g,[[1365,675],[1180,675]],t,C.teal);wire(g,[[740,675],[555,675]],t,C.teal);text(g,s[8],960,815,29,C.teal);tick(g,1585,658,C.teal,1.1)});
}
function coarsefine(g,t){
 const s=T.coarsefine;text(g,s[0],960,116,48);const detect=ease((t-1.3)/1.2),crop=ease((t-4.1)/1),fine=ease((t-8.2)/1);
 box(g,90,294,670,423);text(g,s[1],425,246,34,C.teal);text(g,s[2],425,766,27,C.muted);
 g.save();g.fillStyle=C.deep;g.fillRect(113,317,624,377);g.strokeStyle='#44616e';g.lineWidth=6;for(const y of[407,581]){g.beginPath();g.moveTo(113,y);g.lineTo(737,y);g.stroke()}
 part(g,256,450,-.14,.64);part(g,463,505,.13,.82);part(g,650,455,Math.PI,.6);
 layer(g,detect,()=>{g.strokeStyle=C.teal;g.lineWidth=2.5;for(const [x,y,w,h]of[[196,415,120,76],[388,450,150,109],[597,418,108,74]])g.strokeRect(x,y,w,h)});g.restore();
 appear(g,t,4,()=>{wire(g,[[760,505],[850,505]],t,C.gold);text(g,s[3],1020,246,32,C.gold);box(g,850,358,340,292,C.deep,C.gold,18);part(g,1020,505,.13,.82);text(g,s[4],1020,694,27,C.gold)});
 appear(g,t,5,()=>{g.save();g.setLineDash([5,7]);g.strokeStyle='#efcf8a80';g.lineWidth=1.5;g.beginPath();g.moveTo(538,450);g.lineTo(850,378);g.moveTo(538,559);g.lineTo(850,630);g.stroke();g.restore()});
 appear(g,t,8.2,()=>{wire(g,[[1190,505],[1280,505]],t,C.teal);box(g,1280,294,550,423);text(g,s[5],1555,246,34,C.teal);text(g,s[6],1555,350,31);part(g,1555,451,.13,.9);box(g,1320,556,220,98,C.deep,C.teal,12);box(g,1570,556,220,98,C.deep,C.teal,12);text(g,s[7],1430,604,27,C.teal);text(g,s[8],1680,604,27,C.teal);tick(g,1555,707,C.teal,1.2)});
}
function async(g,t){
 const s=T.async;text(g,s[0],960,116,48);box(g,110,291,1700,218);box(g,110,622,1700,229);text(g,s[1],275,351,33,C.teal);text(g,s[2],275,414,24,C.muted);text(g,s[3],275,679,33,C.gold);text(g,s[4],275,741,24,C.muted);
 previewContext.reset();for(let i=-1;i<8;i++){const x=39+i*210-(t*48%210);box(previewContext,x,12,180,139,C.deep,C.line,12);part(previewContext,x+90,82,Math.sin(i)*.1,.7)}g.drawImage(previewCanvas,451,317);
 appear(g,t,5.7,()=>{wire(g,[[600,509],[600,621]],t,C.gold);text(g,s[5],751,561,25,C.gold);box(g,485,668,495,125,C.deep,C.gold,14);text(g,s[6],732,711,31);g.fillStyle=C.line;g.fillRect(545,758,375,5);g.fillStyle=C.gold;g.fillRect(545,758,375*((Math.max(0,t-6)%5)/5),5)});
 appear(g,t,10.8,()=>{wire(g,[[980,730],[1105,730]],t,C.gold);box(g,1105,668,300,125,C.deep,C.teal,14);text(g,s[7],1255,730,31,C.teal)});
 appear(g,t,13,()=>{wire(g,[[1405,730],[1637,730],[1637,509]],t,C.teal);text(g,s[8],1555,576,25,C.teal)});
}
function tracking(g,t){
 const s=T.tracking;text(g,s[0],960,116,47);const ats=[.6,5,10];
 ats.forEach((at,i)=>appear(g,t,at,()=>{const x=110+i*590;box(g,x,279,520,327);text(g,s[i+1],x+260,326,30,C.muted);part(g,x+215+i*25,433,.1,.9);g.strokeStyle=C.teal;g.lineWidth=2;g.strokeRect(x+143+i*25,382,145,103);box(g,x+178,519,164,53,C.deep,C.teal,25);text(g,s[4],x+260,546,27,C.teal);if(i<2&&t>=ats[i+1])wire(g,[[x+520,435],[x+590,435]],t,C.teal)}));
 appear(g,t,1.8,()=>{box(g,110,684,520,150,C.deep);text(g,s[5],370,729,31);text(g,t<17.8?s[8]:s[10],370,787,27,t<17.8?C.muted:C.teal)});
 appear(g,t,14.5,()=>{wire(g,[[630,760],[700,760]],t,C.teal);box(g,700,684,520,150,C.deep,C.teal);text(g,s[6],960,729,31,C.teal);tick(g,960,785,C.teal,1.2)});
 appear(g,t,15.5,()=>{wire(g,[[1220,760],[1290,760]],t,C.gold);box(g,1290,684,520,150,C.deep,C.gold);text(g,s[7],1550,726,31,C.gold);g.strokeStyle=C.line;g.lineWidth=3;g.beginPath();g.moveTo(1350,796);g.lineTo(1750,796);g.stroke();if(t>16.8){const p=ease((t-16.8)/.8);g.strokeStyle=C.gold;g.lineWidth=4;g.beginPath();g.moveTo(1350,796);g.lineTo(1450,796);g.lineTo(1450,796-35*p);g.lineTo(1500,796-35*p);g.lineTo(1500,796);g.lineTo(1750,796);g.stroke()}});
 appear(g,t,22,()=>{text(g,s[11],960,889,26,C.teal);tick(g,295,647,C.teal,.8);text(g,s[10],395,647,22,C.teal)});
}
function collage(g,t){
 const s=T.collage;const pull=ease((t-1)/7),merge=ease((t-10)/6);
 if(!cardImages)cardImages=cards.map(c=>{const image=createCanvas(410,205),q=image.getContext('2d');box(q,1,1,408,203,'#142c3a','#4b7180',10);text(q,c.title,20,26,20,C.teal,'left');q.fillStyle=C.line;q.fillRect(15,48,380,1);q.font='13px Consolas';q.textAlign='left';q.textBaseline='middle';c.lines.slice(0,8).forEach((line,j)=>{q.fillStyle=/^\s*(def |class |assign |module |wire )/.test(line)?C.gold:'#b7ced5';q.fillText(line,20,70+j*16)});return image});
 layer(g,1-ease((t-10.5)/3.5),()=>{g.save();g.translate(960,540);const zoom=mix(1.9,.94,pull);g.scale(zoom,zoom);
 cardImages.forEach((image,i)=>{const bx=(i%4-1.5)*440,by=(Math.floor(i/4)-1)*234;const target=i<2?[625,500]:i===10?[960,810]:[1205,500];g.save();g.translate(mix(bx,(target[0]-960)/zoom,merge),mix(by,(target[1]-540)/zoom,merge));g.scale(1-.65*merge,1-.65*merge);g.drawImage(image,-205,-102);g.restore()});g.restore()});
 layer(g,ease((t-12.5)/3.5),()=>{
 box(g,90,412,230,184,C.deep,C.line);text(g,s[1],205,503,36);wire(g,[[320,505],[410,505]],t,C.teal);
 box(g,410,320,430,365,C.panel,C.teal);text(g,s[2],625,380,48,C.teal);text(g,s[6],625,480,30);text(g,s[7],625,558,30);wire(g,[[840,505],[990,505]],t,C.teal);
 box(g,990,320,430,365,C.panel,C.gold);text(g,s[3],1205,380,48,C.gold);[s[8],s[9],s[10]].forEach((v,i)=>text(g,v,1205,465+i*65,29));wire(g,[[1420,505],[1600,505]],t,C.gold);
 box(g,1600,412,230,184,C.deep,C.gold);text(g,s[4],1715,503,34);
 box(g,670,756,580,115,C.deep,C.line,18);text(g,s[5],960,793,30,C.teal);text(g,s[11],960,837,25,C.muted);wire(g,[[775,756],[775,685]],t,C.teal,false);wire(g,[[1145,756],[1145,685]],t,C.teal,false);
 });
 const top=g.createLinearGradient(0,160,0,240);top.addColorStop(0,C.bg);top.addColorStop(1,'#0d1d2900');g.fillStyle=C.bg;g.fillRect(0,0,1920,160);g.fillStyle=top;g.fillRect(0,160,1920,80);
 const bottom=g.createLinearGradient(0,880,0,940);bottom.addColorStop(0,'#0d1d2900');bottom.addColorStop(1,C.bg);g.fillStyle=bottom;g.fillRect(0,880,1920,60);g.fillStyle=C.bg;g.fillRect(0,940,1920,140);text(g,s[0],960,117,46);
}
function draw(g,t,w=1920,h=1080,partName='parameter'){
 g.reset();g.save();g.scale(w/1920,h/1080);bg(g);g.save();
 if(partName==='title'||partName==='end')title(g,t,partName==='end');else({parameter,coarsefine,async,tracking,collage}[partName])(g,t);
 g.restore();const fade=1-Math.min(ease(t/.35),ease((durations[partName]-t)/.35));if(fade>0){g.globalAlpha=fade;g.fillStyle=C.bg;g.fillRect(0,0,1920,1080)}g.restore();
}
module.exports={durations,draw,visibleText};
