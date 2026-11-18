'use strict';

const W=1920,H=1080;
const C={black:'#100f0c',white:'#f2e6c9',red:'#e54828',yellow:'#edb731',ink:'#242119',muted:'#a99f89'};
const clamp=v=>Math.max(0,Math.min(1,v));
const lerp=(a,b,p)=>a+(b-a)*clamp(p);

function polygon(g,points,fill,stroke,width=1){
  g.beginPath();points.forEach(([x,y],i)=>i?g.lineTo(x,y):g.moveTo(x,y));g.closePath();
  if(fill){g.fillStyle=fill;g.fill();}if(stroke){g.strokeStyle=stroke;g.lineWidth=width;g.stroke();}
}
function hex(g,x,y,r,fill,stroke,width=1){
  const a=[];for(let i=0;i<6;i++){const q=(30+i*60)*Math.PI/180;a.push([x+Math.cos(q)*r,y+Math.sin(q)*r]);}
  polygon(g,a,fill,stroke,width);
}
function text(g,s,x,y,size,color=C.white,weight=500,align='left',font='CJK'){
  g.font=`${weight} ${size}px "${font}"`;g.fillStyle=color;g.textAlign=align;g.textBaseline='middle';g.fillText(s,x,y);
}
function line(g,x1,y1,x2,y2,color,width=2){g.strokeStyle=color;g.lineWidth=width;g.beginPath();g.moveTo(x1,y1);g.lineTo(x2,y2);g.stroke();}

function chapter(g,t){
  const q=Math.floor(t*15)/15;
  // Flat film masks arrive on separate optical passes, then hold for reading.
  const form=clamp(q/.4),title=clamp((q-.2)/.34),sub=clamp((q-.48)/.32);
  const cx=1317,cy=446,r=364;
  g.save();
  g.beginPath();g.rect(940,0,980,1000*form);g.clip();
  hex(g,cx+14,cy+8,r+11,null,C.red,5);
  hex(g,cx,cy,r,C.yellow);
  hex(g,cx,cy,r-41,C.black);
  hex(g,cx-3,cy+2,r-53,null,C.white,1.6);
  // Small physical gate stops, rather than a digital HUD or grid.
  line(g,1657,294,1740,294,C.red,9);line(g,1657,598,1740,598,C.red,9);
  for(let i=0;i<9;i++)line(g,1733,335+i*28,1733+(i%4===0?25:12),335+i*28,C.white,1.6);
  text(g,'03',cx-5,cy+5,270,C.white,600,'center','DIN');
  g.restore();
  g.fillStyle=C.red;g.fillRect(124,277,Math.round(342*form),11);
  g.fillStyle=C.white;g.fillRect(124,301,Math.round(68*form),4);
  g.save();g.beginPath();g.rect(121,380,850*title,150);g.clip();
  text(g,'识别与工业检测',121,451,91,C.white,700,'left','CJK Heavy');g.restore();
  g.save();g.beginPath();g.rect(124,550,800*sub,82);g.clip();
  text(g,'识别 · 测量 · 告警',126,586,41,C.yellow,400);g.restore();
  line(g,124,726,852,726,C.muted,1.4);
  line(g,124,711,124,741,C.red,3);line(g,852,711,852,741,C.red,3);
  for(let i=0;i<24;i++){const x=124+i*31.65;line(g,x,726,x,726+(i%4===0?24:9),i%4===0?C.white:C.muted,1.3);}
  line(g,124,875,1688,875,C.ink,1.5);line(g,124,875,222,875,C.red,5);
}

function workpiece(g,x,y){
  g.fillStyle=C.ink;g.fillRect(x-81,y-18,28,36);g.fillRect(x+53,y-18,28,36);
  g.fillStyle=C.black;g.fillRect(x-57,y-35,114,70);
  g.fillStyle=C.yellow;g.fillRect(x-48,y-26,96,52);
  g.fillStyle=C.red;g.fillRect(x-24,y-26,13,52);g.fillRect(x+9,y-26,10,52);
  line(g,x-43,y-20,x+41,y-20,C.white,2);
}

function tracking(g,t){
  const qt=Math.floor(t*20)/20;
  const top=[[158,318],[320,230],[1600,230],[1762,318],[1762,605],[1600,693],[320,693],[158,605]];
  const shadow=top.map(([x,y])=>[x+12,y+9]);
  polygon(g,shadow,C.red);polygon(g,top,C.white);
  line(g,124,187,1795,187,C.muted,1.1);line(g,124,187,402,187,C.red,6);
  text(g,'同一工件，连续确认',123,125,64,C.white,700,'left','CJK Heavy');
  // The gate print deliberately uses sparse engraved registration marks.
  for(let i=0;i<29;i++){
    const x=260+i*50;line(g,x,272,x,272+(i%4===0?20:8),C.ink,i%4===0?2:1);
    line(g,x,651,x,651-(i%4===0?20:8),C.ink,i%4===0?2:1);
  }
  line(g,202,405,1718,405,C.ink,2.2);line(g,202,555,1718,555,C.ink,2.2);
  // One physical workpiece travels continuously. Exposure marks retain history.
  const ats=[5.2,7.4,9.6],xs=[425,960,1495];
  let x;
  if(qt<5.2)x=lerp(240,425,(qt-4)/1.2);
  else if(qt<7.4)x=lerp(425,960,(qt-5.2)/2.2);
  else if(qt<9.6)x=lerp(960,1495,(qt-7.4)/2.2);
  else x=lerp(1495,1620,(qt-9.6)/1.3);
  for(let i=0;i<3;i++){
    if(t<ats[i])continue;
    const pulse=1-clamp((t-ats[i])/.4);
    line(g,xs[i],566,xs[i],722,C.red,2);
    line(g,xs[i]-10,712,xs[i]+10,712,C.red,3);
    text(g,`真实观测 ${i+1}`,xs[i],762,35,C.white,500,'center');
    // Brief optical overexposure appears only at the real observation moment.
    if(pulse>0){g.globalAlpha=pulse*.16;g.fillStyle=C.yellow;g.fillRect(xs[i]-91,338,182,251);g.globalAlpha=1;}
  }
  workpiece(g,x,480);
  const bx=x-92,by=422,bw=184,bh=116,leg=23;
  g.strokeStyle=C.red;g.lineWidth=3;
  for(const [sx,sy,dx,dy]of [[bx,by,1,1],[bx+bw,by,-1,1],[bx,by+bh,1,-1],[bx+bw,by+bh,-1,-1]]){
    g.beginPath();g.moveTo(sx+dx*leg,sy);g.lineTo(sx,sy);g.lineTo(sx,sy+dy*leg);g.stroke();
  }
  text(g,'ID 07',x,347,39,C.black,600,'center','DIN');
  line(g,x-51,374,x+51,374,C.red,4);
  const confirmed=t>=11.7;
  if(confirmed){
    g.fillStyle=C.yellow;g.fillRect(124,838,452,78);
    text(g,'类别已确认',154,877,43,C.black,700);
    line(g,517,870,530,885,C.black,5);line(g,530,885,552,858,C.black,5);
  }else{
    g.fillStyle=C.red;g.fillRect(124,840,8,74);text(g,'连续观测积累',156,877,43,C.white,500);
  }
  text(g,'同一身份 · 连续真实观测',1792,880,36,C.white,400,'right');
  line(g,124,952,1792,952,C.ink,1.2);line(g,1694,952,1792,952,C.red,5);
}

function grain(g,t){
  // Procedural film grain only: no image or texture asset is loaded.
  let seed=(Math.floor(t*15)+1)*19331;
  g.fillStyle=C.white;g.globalAlpha=.035;
  for(let i=0;i<520;i++){
    seed=(Math.imul(seed,1664525)+1013904223)|0;const x=(seed>>>0)%W;
    seed=(Math.imul(seed,1664525)+1013904223)|0;const y=(seed>>>0)%980;
    g.fillRect(x,y,1+(i%5===0),1);
  }
  g.globalAlpha=1;
}

function draw(g,t){
  g.reset();g.fillStyle=C.black;g.fillRect(0,0,W,H);
  if(t<3)chapter(g,t);
  else if(t<4){
    const p=Math.floor((t-3)*20)/20;
    let r;
    if(p<.45)r=2400*(1-p/.45);
    else r=2400*clamp((p-.5)/.5);
    if(r>0){
      g.save();hex(g,960,476,r);g.clip();
      if(p<.45)chapter(g,2.9);else tracking(g,4);
      g.restore();
      hex(g,960,476,r,null,C.red,18);hex(g,960,476,r+16,null,C.yellow,5);
    }
  }else tracking(g,t);
  grain(g,t);
}

const meta={
  title:'复古光学科研片头',
  palette:[C.black,C.white,C.red,C.yellow],
  motion:'60fps container; 15Hz chapter optical passes; 20Hz mechanical shutter and tracking; piecewise-linear motion, stable reading holds, small procedural film grain',
  sound_events:[
    {time:0.12,type:'relay_click',gain:0.65},{time:0.4,type:'paper_tape',gain:0.32},
    {time:0.8,type:'high_beep',gain:0.3},{time:3.05,type:'shutter_close',gain:0.6},
    {time:3.55,type:'shutter_open',gain:0.6},{time:5.2,type:'exposure_click',gain:0.65},
    {time:7.4,type:'exposure_click',gain:0.65},{time:9.6,type:'exposure_click',gain:0.65},
    {time:11.7,type:'confirmation_double_beep',gain:0.65}
  ],
  visible_text:['03','识别与工业检测','识别 · 测量 · 告警','同一工件，连续确认','真实观测 1','真实观测 2','真实观测 3','ID 07','连续观测积累','类别已确认','同一身份 · 连续真实观测'],
  reference_urls:['https://www.artofthetitle.com/title/the-andromeda-strain/']
};

module.exports={draw,meta};
