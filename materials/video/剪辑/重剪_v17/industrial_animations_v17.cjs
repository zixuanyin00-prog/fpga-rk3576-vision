'use strict';

const fs=require('node:fs');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const {createCanvas,GlobalFonts}=require('../../dataflow_film_20261028/node_modules/@napi-rs/canvas');
GlobalFonts.registerFromPath('C:/Windows/Fonts/msyh.ttc','Microsoft YaHei');

const C={bg:'#0d1d29',panel:'#142d38',deep:'#102632',ink:'#edf5f3',muted:'#a5bdc7',line:'#355768',teal:'#78dfcb',gold:'#efcf8a'};
const durations={dimensions:6,alerts:8};
const visibleText={
  dimensions:['多目标像素尺寸量化','逐目标检测框','x1, y1','x2, y2','w','h','原生坐标','w = x2 − x1','h = y2 − y1','单位：px','逐目标输出位置与像素宽高'],
  alerts:['边缘异常提示与留证','识别终态事件','终态未确认','身份丢失','RK 边缘端','异常状态提示','真实原图缓存','网口回传','SFTP → 电脑','可选SD卡留存','需启用并挂载']
};
const ease=v=>{const x=Math.max(0,Math.min(1,v));return x*x*(3-2*x);};

function text(g,value,x,y,size=32,color=C.ink,align='center',weight=500){
  g.textAlign=align;g.textBaseline='middle';g.font=`${weight} ${size}px "Microsoft YaHei",sans-serif`;g.fillStyle=color;g.fillText(value,x,y);
}
function panel(g,x,y,w,h,color=C.line,fill=C.panel,r=18){
  g.beginPath();g.roundRect(x,y,w,h,r);g.fillStyle=fill;g.fill();g.strokeStyle=color;g.lineWidth=1.6;g.stroke();
}
function background(g,title,t){
  g.reset();g.fillStyle=C.bg;g.fillRect(0,0,1920,1080);
  const glow=g.createRadialGradient(960,430,70,960,440,1080);glow.addColorStop(0,'#1b3845');glow.addColorStop(.7,'#112833');glow.addColorStop(1,C.bg);
  g.fillStyle=glow;g.fillRect(0,0,1920,1080);
  g.save();g.globalAlpha=ease(t/.35);text(g,title,960,145,54,C.ink,'center',600);g.fillStyle=C.teal;g.fillRect(914,213,92,3);g.restore();
}
function line(g,points,color=C.line,width=2){
  g.beginPath();points.forEach(([x,y],i)=>i?g.lineTo(x,y):g.moveTo(x,y));g.strokeStyle=color;g.lineWidth=width;g.lineJoin='round';g.lineCap='round';g.stroke();
}
function arrow(g,x1,y1,x2,y2,color=C.teal,double=false){
  line(g,[[x1,y1],[x2,y2]],color,2.5);
  const a=Math.atan2(y2-y1,x2-x1),head=(x,y,angle)=>{line(g,[[x-11*Math.cos(angle-.42),y-11*Math.sin(angle-.42)],[x,y],[x-11*Math.cos(angle+.42),y-11*Math.sin(angle+.42)]],color,2.5);};
  head(x2,y2,a);if(double)head(x1,y1,a+Math.PI);
}
function packet(g,points,progress,color=C.gold){
  if(progress<=0||progress>=1)return;
  const lengths=points.slice(1).map((p,i)=>Math.hypot(p[0]-points[i][0],p[1]-points[i][1]));let distance=progress*lengths.reduce((a,b)=>a+b,0),at=0;
  while(at<lengths.length-1&&distance>lengths[at]){distance-=lengths[at];at++;}
  const q=distance/lengths[at],x=points[at][0]+(points[at+1][0]-points[at][0])*q,y=points[at][1]+(points[at+1][1]-points[at][1])*q;
  g.save();g.shadowColor=color;g.shadowBlur=15;g.fillStyle=color;g.beginPath();g.arc(x,y,5.5,0,Math.PI*2);g.fill();g.restore();
}

function dimensions(g,t){
  const q=visibleText.dimensions;background(g,q[0],t);
  g.save();g.globalAlpha=ease((t-.15)/.35);panel(g,110,285,1050,505);text(g,q[1],635,335,33,C.muted);g.restore();
  const targets=[{x:310,w:190,h:140},{x:650,w:230,h:115},{x:990,w:180,h:170}];
  targets.forEach((o,i)=>{
    g.save();g.globalAlpha=ease((t-.2-i*.11)/.4);
    const left=o.x-o.w/2,right=o.x+o.w/2,top=520-o.h/2,bottom=520+o.h/2;
    // Illustrative workpieces have no measured numerical dimensions.
    panel(g,left+24,top+32,o.w-48,o.h-64,'#a5bdc7','#779398',7);
    g.fillStyle='#c5d3d1';g.fillRect(left+24,top+36,18,o.h-72);g.fillRect(right-42,top+36,18,o.h-72);
    const active=Math.floor(Math.max(0,t-1.2)/1.35)%3===i&&t>1.2;
    g.strokeStyle=active?C.gold:C.teal;g.lineWidth=2;g.strokeRect(left,top,o.w,o.h);
    line(g,[[left,top-7],[left,383]],C.line,1.5);line(g,[[right,top-7],[right,383]],C.line,1.5);
    arrow(g,left,392,right,392,active?C.gold:C.teal,true);text(g,q[4],o.x,369,29,active?C.gold:C.teal);
    line(g,[[right+7,top],[right+41,top]],C.line,1.5);line(g,[[right+7,bottom],[right+41,bottom]],C.line,1.5);
    arrow(g,right+31,top,right+31,bottom,active?C.gold:C.teal,true);text(g,q[5],right+53,520,29,active?C.gold:C.teal);
    text(g,q[2],left,top-17,23,C.muted,'left');text(g,q[3],right,bottom+26,23,C.muted,'right');
    g.restore();
  });
  g.save();g.globalAlpha=ease((t-.43)/.4);panel(g,1225,285,585,505,C.teal);
  text(g,q[6],1517,349,35,C.teal);
  text(g,q[7],1517,452,46,C.ink);text(g,q[8],1517,544,46,C.ink);
  line(g,[[1290,615],[1745,615]],C.line,1.5);text(g,q[9],1517,680,34,C.muted);
  g.restore();
  g.save();g.globalAlpha=ease((t-.6)/.35);text(g,q[10],960,845,34,C.ink);g.restore();
}

function alerts(g,t){
  const q=visibleText.alerts;background(g,q[0],t);
  g.save();g.globalAlpha=ease((t-.15)/.4);
  text(g,q[1],300,282,31,C.muted);
  panel(g,110,340,380,120,C.gold);text(g,q[2],300,400,38,C.ink);
  panel(g,110,530,380,120,C.gold);text(g,q[3],300,590,38,C.ink);
  panel(g,655,330,560,340,C.teal);text(g,q[4],935,390,39,C.teal,'center',600);
  line(g,[[715,435],[1155,435]],C.line,1.5);
  text(g,q[5],935,499,35,C.ink);text(g,q[6],935,579,35,C.ink);
  panel(g,1440,305,375,180,C.teal);text(g,q[7],1627,365,35,C.ink);text(g,q[8],1627,427,31,C.muted);
  panel(g,1440,570,375,180,C.gold);text(g,q[9],1627,630,34,C.ink);text(g,q[10],1627,693,28,C.muted);
  g.restore();
  g.save();g.globalAlpha=ease((t-.3)/.4);
  const top=[[490,400],[565,400],[565,500],[645,500]],low=[[490,590],[565,590],[565,500],[645,500]];
  line(g,top,C.gold,2.5);line(g,low,C.gold,2.5);arrow(g,609,500,645,500,C.gold);
  const network=[[1225,500],[1320,500],[1320,395],[1428,395]],sd=[[1225,500],[1320,500],[1320,660],[1428,660]];
  line(g,network,C.teal,2.5);line(g,sd,C.gold,2.5);arrow(g,1395,395,1428,395,C.teal);arrow(g,1395,660,1428,660,C.gold);
  // Moving dots illustrate routing only; no success/commit indicator is shown.
  const cycle=(t-1.05)%3.0;
  packet(g,Math.floor(Math.max(0,t-1.05)/3)%2?low:top,cycle/.9,C.gold);
  packet(g,network,(cycle-1.03)/1.15,C.teal);packet(g,sd,(cycle-1.35)/1.15,C.gold);
  g.restore();
}

async function main(){
  const canvas=createCanvas(1920,1080),g=canvas.getContext('2d');
  const selected=process.argv.find(v=>Object.hasOwn(durations,v));
  const names=selected?[selected]:Object.keys(durations);
  for(const name of names){
    const draw=name==='dimensions'?dimensions:alerts;
    draw(g,name==='dimensions'?3.2:3.5);
    fs.writeFileSync(path.join(__dirname,`${name}_preview.png`),canvas.toBuffer('image/png'));
    fs.writeFileSync(path.join(__dirname,`${name}_visible_text.json`),JSON.stringify({duration_seconds:durations[name],visible_text:visibleText[name],diagram_scope:name==='dimensions'?'Native-coordinate per-target bounding box geometry; illustrative vectors, no measured values':'Terminal recognition-event routing; SD optional after enabling and mounting'},null,2),'utf8');
    if(process.argv.includes('--preview'))continue;
    const ff=path.resolve(__dirname,'../../dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe');
    const proc=spawn(ff,['-hide_banner','-loglevel','warning','-y','-f','rawvideo','-pix_fmt','rgba','-s','1920x1080','-r','60','-i','pipe:0','-an','-c:v','h264_nvenc','-preset','p5','-cq','19','-b:v','0','-pix_fmt','yuv420p','-video_track_timescale','60000','-frames:v',String(durations[name]*60),path.join(__dirname,name+'.mp4')],{cwd:__dirname,stdio:['pipe','ignore','pipe'],windowsHide:true});
    proc.stderr.on('data',d=>process.stderr.write(d));const done=new Promise(resolve=>proc.on('close',resolve));
    for(let frame=0;frame<durations[name]*60;frame++){draw(g,frame/60);if(!proc.stdin.write(canvas.data()))await once(proc.stdin,'drain');}
    proc.stdin.end();if(await done!==0)throw new Error('Animation render failed: '+name);
    console.log(`${name}.mp4: ${durations[name]}s / ${durations[name]*60}frames`,durations[name]*60);
  }
}

module.exports={durations,visibleText,dimensions,alerts};
if(require.main===module)main().catch(error=>{console.error(error);process.exitCode=1;});
