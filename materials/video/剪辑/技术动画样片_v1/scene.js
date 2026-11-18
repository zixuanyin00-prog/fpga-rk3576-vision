(function(root){
'use strict';
const DURATION=42,W=1920,H=1080;
const C={bg:'#11202a',panel:'#172d38',inner:'#10232c',line:'#34515e',ink:'#edf5f4',muted:'#9ab3bd',a:'#71dbce',gold:'#ecd293'};
const clamp=(v,a=0,b=1)=>Math.max(a,Math.min(b,v));
const ease=x=>{x=clamp(x);return x*x*(3-2*x)};
function text(g,s,x,y,size=28,color=C.ink,align='left',weight=400){g.font=`${weight} ${size}px "Microsoft YaHei",sans-serif`;g.fillStyle=color;g.textAlign=align;g.textBaseline='middle';g.fillText(s,x,y)}
function box(g,x,y,w,h,r=18,fill=C.panel,stroke=C.line){g.beginPath();g.roundRect(x,y,w,h,r);g.fillStyle=fill;g.fill();if(stroke){g.strokeStyle=stroke;g.lineWidth=1.5;g.stroke()}}
function reveal(g,t,start,fn){const a=ease((t-start)/1.3);if(a<=0)return;g.save();g.globalAlpha=a;g.translate(0,(1-a)*14);fn();g.restore()}
function arrow(g,pts,t,start,color=C.a,reverse=false,dashed=false){
 const a=ease((t-start)/1.6);if(!a)return;g.save();g.globalAlpha=a;g.lineWidth=2.5;g.strokeStyle=color;
 if(dashed)g.setLineDash([8,10]);g.beginPath();pts.forEach(([x,y],i)=>i?g.lineTo(x,y):g.moveTo(x,y));g.stroke();g.setLineDash([]);
 const path=reverse?[...pts].reverse():pts;const end=path[path.length-1],before=path[path.length-2];const an=Math.atan2(end[1]-before[1],end[0]-before[0]);
 g.beginPath();g.moveTo(...end);g.lineTo(end[0]-12*Math.cos(an-.42),end[1]-12*Math.sin(an-.42));g.lineTo(end[0]-12*Math.cos(an+.42),end[1]-12*Math.sin(an+.42));g.closePath();g.fillStyle=color;g.fill();
 const lengths=[];let total=0;for(let i=1;i<path.length;i++){const len=Math.hypot(path[i][0]-path[i-1][0],path[i][1]-path[i-1][1]);lengths.push(len);total+=len}
 for(let p=0;p<3;p++){let distance=((t*.16+p/3)%1)*total;let i=0;while(i<lengths.length-1&&distance>lengths[i])distance-=lengths[i++];const f=distance/lengths[i];const x=path[i][0]+(path[i+1][0]-path[i][0])*f,y=path[i][1]+(path[i+1][1]-path[i][1])*f;g.beginPath();g.arc(x,y,3.5,0,Math.PI*2);g.fillStyle=color;g.fill()}
 g.restore();
}
function part(g,x,y,rotation=0,edge=false){g.save();g.translate(x,y);g.rotate(rotation);box(g,-27,-15,54,30,4,edge?C.inner:'#a9b5b6',edge?C.a:null);g.fillStyle=edge?C.a:'#dce3df';g.fillRect(-30,-11,6,22);g.fillRect(24,-11,6,22);if(!edge){g.fillStyle='#52666b';g.fillRect(-11,-2,22,4)}g.restore()}
function main(g,t){
 g.fillStyle=C.bg;g.fillRect(0,0,W,H);
 const gradient=g.createRadialGradient(930,430,40,930,430,1100);gradient.addColorStop(0,'#203b47');gradient.addColorStop(1,C.bg);g.fillStyle=gradient;g.fillRect(0,0,W,H);
 g.save();g.globalAlpha=.10;g.fillStyle=C.muted;for(let x=40;x<1920;x+=40)for(let y=140;y<920;y+=40)g.fillRect(x,y,1,1);g.restore();
 text(g,'从像素，到识别与执行',960,75,48,C.ink,'center',500);
 text(g,'FPGA 流式处理  /  RK 智能识别  /  显示与控制',960,130,23,C.muted,'center');

 reveal(g,t,0,()=>{
   box(g,75,360,260,270,22);text(g,'相机采集',205,400,30,C.ink,'center');
   box(g,106,440,198,125,10,C.inner);g.save();g.beginPath();g.rect(108,442,194,121);g.clip();
   for(let i=0;i<4;i++)part(g,115+i*62+(t*12%62),501,.10*Math.sin(i));g.restore();
   text(g,'连续图像输入',205,596,20,C.muted,'center');
 });
 arrow(g,[[335,495],[430,495]],t,2);reveal(g,t,2,()=>text(g,'MIPI',382,466,21,C.a,'center'));

 reveal(g,t,2.5,()=>{
   box(g,430,300,650,360,24,C.panel,C.a);
   text(g,'FPGA',460,345,40,C.a,'left',500);text(g,'流式采集与预处理',1045,349,25,C.ink,'right');
   g.strokeStyle=C.line;g.beginPath();g.moveTo(460,385);g.lineTo(1050,385);g.stroke();
   for(let i=0;i<3;i++){
    const x=460+i*200;const active=ease((t-(4+i*2.2))/1.5);
    box(g,x,415,180,171,13,C.inner,active>.1?C.a:C.line);
    text(g,['成像调节','特征增强','图像输出'][i],x+90,447,25,active>.1?C.ink:C.muted,'center');
    g.save();g.globalAlpha=.28+.72*active;
    if(i===0){for(let b=0;b<3;b++){g.fillStyle=['#dcaaa4','#a5d8b6','#94b9df'][b];g.fillRect(x+30+b*43,534-(26+b*13),24,26+b*13)}text(g,'RGB',x+90,561,16,C.muted,'center')}
    if(i===1){part(g,x+90,511,.10,true);g.strokeStyle=C.a;g.setLineDash([3,5]);g.strokeRect(x+40,482,100,59);g.setLineDash([]);text(g,'特征保留',x+90,561,16,C.muted,'center')}
    if(i===2){for(let row=0;row<3;row++)for(let col=0;col<5;col++){g.fillStyle=(row+col)%3===0?C.a:'#3d6870';g.fillRect(x+38+col*23,482+row*18,17,12)}text(g,'像素流',x+90,561,16,C.muted,'center')}
    g.restore();
   }
   text(g,'逐级处理，连续输出',755,624,22,C.muted,'center');
 });
 arrow(g,[[1080,495],[1170,495]],t,11);reveal(g,t,11,()=>text(g,'PCIe',1125,452,20,C.a,'center'));
 reveal(g,t,12,()=>{
   box(g,1170,300,650,360,24);text(g,'RK',1200,345,40,C.a,'left',500);text(g,'工件定位与姿态判断',1788,349,25,C.ink,'right');
   g.strokeStyle=C.line;g.beginPath();g.moveTo(1200,385);g.lineTo(1790,385);g.stroke();
   box(g,1200,415,245,171,13,C.inner);text(g,'粗定位',1322,447,25,C.ink,'center');
   part(g,1265,516,.2);part(g,1378,528,-.2);
   const q=ease((t-15)/2);g.save();g.globalAlpha=q;g.strokeStyle=C.a;g.lineWidth=2;g.strokeRect(1223,486,84,59);g.strokeRect(1336,497,85,63);g.restore();
   arrow(g,[[1445,505],[1500,505]],t,17);
   box(g,1500,415,290,171,13,C.inner);text(g,'姿态精判',1645,447,25,C.ink,'center');
   const z=ease((t-18)/2);g.save();g.globalAlpha=z;part(g,1645,515,Math.PI);g.strokeStyle=C.gold;g.beginPath();g.moveTo(1593,552);g.lineTo(1697,552);g.stroke();g.beginPath();g.moveTo(1697,552);g.lineTo(1685,546);g.lineTo(1685,558);g.closePath();g.fillStyle=C.gold;g.fill();g.restore();
   text(g,'位置 → 姿态 → 结果发布',1495,624,22,C.muted,'center');
 });

 arrow(g,[[1495,660],[1495,710],[1260,710],[1260,750]],t,23);
 arrow(g,[[1495,710],[1655,710],[1655,750]],t,25);
 reveal(g,t,23,()=>{
   box(g,1100,750,320,132,18);box(g,1120,776,80,54,5,C.inner,C.a);g.strokeStyle=C.a;g.beginPath();g.moveTo(1160,830);g.lineTo(1160,842);g.moveTo(1138,842);g.lineTo(1182,842);g.stroke();
   text(g,'HDMI 显示',1220,794,28);text(g,'独立预览 · 结果叠加',1220,838,18,C.muted);
 });
 reveal(g,t,25,()=>{
   box(g,1490,750,330,132,18);g.strokeStyle=C.a;g.lineWidth=2;g.beginPath();g.moveTo(1510,824);g.lineTo(1528,824);g.lineTo(1528,787);g.lineTo(1560,787);g.lineTo(1560,824);g.lineTo(1580,824);g.stroke();text(g,'外接控制',1600,794,28);text(g,'确认结果 → 控制输出',1600,838,17,C.muted);
 });
 reveal(g,t,27,()=>{
   box(g,430,750,580,132,18,C.inner);
   text(g,'跨帧关联  /  连续观测',720,792,27,C.ink,'center');
   text(g,'确认工件状态，再生成控制事件',720,837,21,C.muted,'center');
 });

 arrow(g,[[1495,300],[1495,220],[755,220],[755,300]],t,32,C.gold,false,true);
 arrow(g,[[790,300],[790,263],[1460,263],[1460,300]],t,34,C.gold,false,true);
 reveal(g,t,32,()=>{text(g,'参数下发',1125,197,24,C.gold,'center');text(g,'状态回读',1125,282,18,C.gold,'center')});
 const captions=[
  [0.8,10.7,['相机采集工件图像，首先进入 FPGA，','完成流式图像预处理。']],
  [11.5,22,['处理后的图像通过 PCIe 传入 RK，','先进行工件定位，再完成姿态判断。']],
  [22.8,32.2,['识别结果分别用于 HDMI 显示和外接控制，','预览与推理独立调度。']],
  [33,41.6,['RK 下发处理参数，FPGA 回传状态，','让现场调参与图像处理形成双向交互。']]
 ];
 for(const [a,b,lines] of captions)if(t>=a&&t<=b){g.save();g.globalAlpha=Math.min(ease((t-a)/.4),ease((b-t)/.4));lines.forEach((s,i)=>text(g,s,960,974+i*44,34,C.ink,'center'));g.restore()}
}
function draw(g,t,width=1920,height=1080){g.save();g.scale(width/W,height/H);main(g,t);const fade=1-Math.min(ease(t/.8),ease((DURATION-t)/.8));if(fade>0){g.globalAlpha=fade;g.fillStyle=C.bg;g.fillRect(0,0,W,H)}g.restore()}
const api={DURATION,draw};if(typeof module!=='undefined')module.exports=api;else root.TechFilm=api;
})(typeof window!=='undefined'?window:globalThis);
