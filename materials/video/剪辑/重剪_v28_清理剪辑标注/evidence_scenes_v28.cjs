'use strict';

// Photographic evidence scenes. This module only reads archived local assets.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {createCanvas, loadImage, GlobalFonts} = require('../../dataflow_film_20261028/node_modules/@napi-rs/canvas');
const DATA_ROOT=path.resolve(__dirname,'../重剪_v24_清晰关联与慢放');
const C = {bg:'#cbdde9',ink:'#183747',muted:'#536f7e',blue:'#346e9f',green:'#39b83f',darkGreen:'#287b38',silver:'#f2f7f8',line:'#8caab9',faint:'#a8c0cc'};
const durations = {isp_methods:10.5,compression_pcie:6.4,tracking_short:5.2,dimensions:5.6,alerts:6.5};
const images = {};
const legacy='../重剪_v23_证据补强/';
const assets = {
  ispOriginal:legacy+'evidence_isp_compression/isp/00_archived_native_rgb.png',
  jpegDecoded:legacy+'evidence_isp_compression/jpeg_q90_decoded_native_0.png',
  dimensions:legacy+'evidence_tracking_dimensions/dimensions_source448296_native.png',
  network:legacy+'evidence_network_sd/original_native_crop_99x65.png'
};
const ispStages=[
  {at:.4,id:'01_gaussian_3x3',input:'00_archived_native_rgb',label:'高斯平滑',inputLabel:'RGB 输入',parameter:'3×3 高斯平滑'},
  {at:1.9,id:'02_luminance_mapping',input:'01_gaussian_3x3',label:'亮度映射',inputLabel:'高斯输出',parameter:'单调亮度映射'},
  {at:3.4,id:'04_foreground_y_lt_72',input:'03_luma_77_150_29',label:'阈值分割',inputLabel:'灰度输入',parameter:'阈值 Y < 72'},
  {at:4.9,id:'05_binary_open_cross3x3',input:'04_foreground_y_lt_72',label:'开运算',inputLabel:'阈值掩码',parameter:'十字形开运算'},
  {at:6.4,id:'06_sobel_l1_gt_96',input:'03_luma_77_150_29',label:'Sobel 边缘',inputLabel:'灰度输入',parameter:'Sobel 阈值 96'},
  {at:7.9,id:'07_edge_overlay',input:'02_luminance_mapping',label:'彩色轮廓',inputLabel:'亮度映射图',parameter:'彩色轮廓叠加'}
];
for(const id of ['00_archived_native_rgb','01_gaussian_3x3','02_luminance_mapping','03_luma_77_150_29','04_foreground_y_lt_72','05_binary_open_cross3x3','06_sobel_l1_gt_96','07_edge_overlay']) {
  assets['native_'+id]='isp_assets/native/'+id+'.png';
  assets['crop_'+id]='isp_assets/crop_2x/'+id+'.png';
}
for(const source of [89270,89278,89284]) {
  assets['track'+source]='tracking_evidence/sequence_native/track880_source'+source+'_native.png';
  assets['detail'+source]='tracking_evidence/sequence_native/track880_source'+source+'_detail_native.png';
}
const visibleText = {
  isp_methods:['ISP 图像预处理','六项处理效果对照','高斯平滑','亮度映射','阈值分割','开运算','Sobel 边缘','彩色轮廓','RGB 输入','高斯输出','灰度输入','阈值掩码','亮度映射图','输入','结果','全图位置','3×3 高斯平滑','单调亮度映射','阈值 Y < 72','十字形开运算','Sobel 阈值 96','彩色轮廓叠加','FPGA 流水线：去马赛克 · 白平衡 · 高斯平滑 · 直方图映射 · 前景分割 · 灰度 / 二值形态学','Sobel 边缘 · 彩色轮廓 · 数字增益 · 亮度 · 对比度'],
  compression_pcie:['同尺寸压缩：真实样本对照','原生 RGB 输入','JPEG 解码图','512 × 288','92.87%','压缩率','6,635,520 → 473,287 B','15 张 · 512 × 288','JPEG Q90 / 4:4:4','RGB888 → JPEG','原生 RGB 推理支路','FPGA 采集 / 处理','PCIe · RGB DMA','RK 识别 / 显示','可选预览与归档压缩','JPEG 有损','LZ77-16','15 / 15','YUYV 输入逐字节往返一致'],
  tracking_short:['跨帧关联：真实记录对照','2026-10-26','同一 session · ID 880','+0.000 s','+0.133 s','+0.233 s','源帧 89270','源帧 89278','源帧 89284','ID 880','原图位置','局部细节'],
  dimensions:['多工件像素宽高测量','同时给出多个工件在原图中的宽/高','2026-10-29 · 源帧 448296 · 原生 ROI 512 × 288','框序号','宽 w / px','高 h / px','1','2','3','4','88.43','62.76','90.43','64.23','88.35','56.55','101.76','68.14','w = x₂ − x₁     h = y₂ − y₁','单位：px'],
  alerts:['网口回传与异常留证','历史回传记录','电脑接收回执 · receipt.json','"received_at"','2026-11-02T19:09:40+08:00','"manifest_sha256"','4808b93f1024d136bf422c16ac858f3e','5ed406af7977c362f59e1fa188990375','"event_id"','0b15d69f8bb787d416ba2447e1b08d71','4f0f5fb36fb3d7a3e75e921ca1b2e6db','回执与清单对应 · SHA256 一致','对应原图','原生 99 × 65','frame.rgb','19,305 B','源帧 566648','100 / 100','归档文件长度与 SHA256 全匹配','支持可选 SD 留存']
};
let dimensionRecords;
let trackTrace;
let receipt;
let initialized = false;

async function init() {
  if(initialized)return;
  for(const [file,alias] of [['msyh.ttc','CJK'],['msyhbd.ttc','CJK Heavy'],['bahnschrift.ttf','DIN'],['consola.ttf','Mono']]) GlobalFonts.registerFromPath('C:/Windows/Fonts/'+file,alias);
  await Promise.all(Object.entries(assets).map(async([key,file])=>{images[key]=await loadImage(path.join(DATA_ROOT,file));}));
  dimensionRecords=JSON.parse(fs.readFileSync(path.join(DATA_ROOT,legacy+'evidence_tracking_dimensions/dimensions_exact_records.json'),'utf8'));
  trackTrace=JSON.parse(fs.readFileSync(path.join(DATA_ROOT,'tracking_evidence/sequence_native/traceable.json'),'utf8'));
  receipt=JSON.parse(fs.readFileSync(path.join(DATA_ROOT,legacy+'evidence_network_sd/source_records/0b15d69f8bb787d416ba2447e1b08d714f0f5fb36fb3d7a3e75e921ca1b2e6db/receipt.json'),'utf8'));
  initialized=true;
}

const clamp = x=>Math.max(0,Math.min(1,x));
const smooth = x=>{x=clamp(x);return x*x*x*(10+x*(-15+6*x));};
function txt(g,value,x,y,size=32,color=C.ink,weight=500,align='left',font='CJK') {
  g.font=`${weight} ${size}px "${font}",sans-serif`;g.textAlign=align;g.textBaseline='middle';g.fillStyle=color;g.fillText(value,x,y);
}
function line(g,x1,y1,x2,y2,color=C.line,width=2) {
  g.beginPath();g.moveTo(x1,y1);g.lineTo(x2,y2);g.strokeStyle=color;g.lineWidth=width;g.lineCap='round';g.stroke();
}
function box(g,x,y,w,h,fill=C.silver,stroke=C.line,r=14) {
  g.beginPath();g.roundRect(x,y,w,h,r);g.fillStyle=fill;g.fill();if(stroke){g.strokeStyle=stroke;g.lineWidth=1.4;g.stroke();}
}
function enter(g,t,at,fn,span=.3) {
  const p=smooth((t-at)/span);if(p<=0)return;g.save();g.globalAlpha*=p;g.translate(0,12*(1-p));fn(p);g.restore();
}
function card(g,t,at,x,y,w,h,fn,side=1,fill=C.silver) {
  const p=smooth((t-at)/.28);if(p<=0)return;
  const scale=.9+.1*p, dx=side*46*(1-p)*(1-p), dy=27*(1-p)+Math.sin(p*Math.PI)*11;
  g.save();g.globalAlpha*=p;g.translate(x+w/2+dx,y+h/2+dy);g.scale(scale,scale);g.translate(-x-w/2,-y-h/2);
  g.save();g.shadowColor=`rgba(24,55,71,${.10*p})`;g.shadowBlur=20-7*p;g.shadowOffsetY=9-4*p;box(g,x,y,w,h,fill,null,14);g.restore();
  box(g,x,y,w,h,fill,C.line,14);fn();g.restore();
}
function curve(g,points,progress,color=C.blue,width=2.2,dot=false) {
  const p=clamp(progress);if(p<=0)return;
  const [a,b,c,d]=points;let end=a;
  g.beginPath();g.moveTo(a[0],a[1]);
  for(let i=1;i<=48;i++){
    const q=p*i/48,r=1-q;end=[r*r*r*a[0]+3*r*r*q*b[0]+3*r*q*q*c[0]+q*q*q*d[0],r*r*r*a[1]+3*r*r*q*b[1]+3*r*q*q*c[1]+q*q*q*d[1]];g.lineTo(end[0],end[1]);
  }
  g.strokeStyle=color;g.lineWidth=width;g.lineCap='round';g.stroke();
  if(dot&&p<1){g.beginPath();g.arc(end[0],end[1],5,0,Math.PI*2);g.fillStyle=color;g.fill();}
}
function photo(g,key,x,y,w,h) {
  g.save();g.beginPath();g.roundRect(x,y,w,h,6);g.clip();g.imageSmoothingEnabled=true;g.imageSmoothingQuality='high';g.drawImage(images[key],x,y,w,h);g.restore();
  g.strokeStyle='rgba(24,55,71,.2)';g.lineWidth=1;g.strokeRect(x+.5,y+.5,w-1,h-1);
}
function pill(g,value,x,y,w,active=false,size=25) {
  box(g,x,y,w,44,active?'#e5f3e8':'#e5edf1',active?C.darkGreen:C.faint,22);txt(g,value,x+w/2,y+22,size,active?C.darkGreen:C.muted,500,'center');
}
function heading(g,title,label,t) {
  enter(g,t,0,()=>{txt(g,title,120,119,56,C.ink,700,'left','CJK Heavy');line(g,120,173,180,173,C.green,5);},.22);
  if(label)enter(g,t,.06,()=>txt(g,label,120,207,28,C.muted),.24);
}
function arrow(g,x1,y,x2) {
  line(g,x1,y,x2,y,C.blue,3);line(g,x2-11,y-7,x2,y,C.blue,3);line(g,x2-11,y+7,x2,y,C.blue,3);
}

function isp(g,t) {
  heading(g,'ISP 图像预处理','六项处理效果对照',t);
  let i=0;for(let j=1;j<ispStages.length;j++)if(t>=ispStages[j].at)i=j;
  const stage=ispStages[i],complete=t>=9.2;
  card(g,t,.02,104,242,1008,606,()=>{
    txt(g,stage.label,132,280,35,C.ink,700);txt(g,stage.parameter,1080,280,27,C.muted,500,'right');
    txt(g,'输入 · '+stage.inputLabel,132,329,27,C.muted);txt(g,'结果',632,329,27,C.darkGreen,600);
    photo(g,'crop_'+stage.input,132,355,448,252);photo(g,'crop_'+stage.input,632,355,448,252);
    const wipe=smooth((t-stage.at)/.62),sx=632+448*wipe;
    g.save();g.beginPath();g.rect(632,355,448*wipe,252);g.clip();photo(g,'crop_'+stage.id,632,355,448,252);g.restore();
    if(wipe>0&&wipe<1){
      line(g,sx,355,sx,607,'#e9f8ec',3);line(g,sx+2,355,sx+2,607,C.darkGreen,1);
      g.beginPath();g.arc(sx,481,15,0,Math.PI*2);g.fillStyle=C.silver;g.fill();g.strokeStyle=C.darkGreen;g.lineWidth=1.5;g.stroke();
      for(const side of [-1,1]){line(g,sx+side*4,476,sx+side*8,481,C.darkGreen,1.6);line(g,sx+side*8,481,sx+side*4,486,C.darkGreen,1.6);}
    }
    txt(g,'全图位置',132,637,25,C.muted);txt(g,'全图位置',632,637,25,C.muted);
    photo(g,'native_'+stage.input,132,663,288,162);photo(g,'native_'+stage.id,632,663,288,162);
    for(const x of [132,632]){g.strokeStyle=C.darkGreen;g.lineWidth=2;g.strokeRect(x+160*288/512,663+58*162/288,224*288/512,126*162/288);}
  },-1);
  ispStages.forEach((s,j)=>{
    const x=1152+(j%2)*336,y=242+Math.floor(j/2)*204,active=complete||j===i;
    card(g,t,.02+j*.025,x,y,312,198,()=>{
      txt(g,s.label,x+16,y+21,26,active?C.darkGreen:C.ink,active?700:500);
      photo(g,'native_'+s.id,x+16,y+39,280,157.5);
      if(active){g.strokeStyle=C.darkGreen;g.lineWidth=complete?2:3.5;g.beginPath();g.roundRect(x+1.5,y+1.5,309,195,14);g.stroke();}
    },1);
  });
  enter(g,t,.1,()=>{
    txt(g,'FPGA 流水线：去马赛克 · 白平衡 · 高斯平滑 · 直方图映射 · 前景分割 · 灰度 / 二值形态学',120,865,25,C.muted);
    txt(g,'Sobel 边缘 · 彩色轮廓 · 数字增益 · 亮度 · 对比度',120,896,25,C.muted);
  },.26);
}

function compression(g,t) {
  heading(g,'同尺寸压缩：真实样本对照','',t);
  card(g,t,.02,104,240,548,410,()=>{txt(g,'原生 RGB 输入',120,273,29,C.ink,700);photo(g,'ispOriginal',120,307,512,288);txt(g,'512 × 288',120,624,24,C.muted,500,'left','DIN');},-1);
  card(g,t,.06,674,240,548,410,()=>{txt(g,'JPEG 解码图',690,273,29,C.ink,700);photo(g,'jpegDecoded',690,307,512,288);txt(g,'512 × 288',690,624,24,C.muted,500,'left','DIN');});
  card(g,t,.1,1265,240,535,410,()=>{
    txt(g,'92.87%',1532,328,100,C.blue,600,'center','DIN');
    txt(g,'压缩率',1532,398,25,C.muted,500,'center');
    txt(g,'6,635,520 → 473,287 B',1532,454,31,C.ink,600,'center','DIN');
    txt(g,'15 张 · 512 × 288',1532,501,25,C.muted,500,'center');txt(g,'JPEG Q90 / 4:4:4',1532,537,25,C.muted,500,'center','DIN');
    const ratio=473287/6635520,p=smooth((t-.5)/1.2),w=465*(1-(1-ratio)*p);
    box(g,1300,577,465,22,'#d8e4ea',null,4);box(g,1300,577,w,22,C.blue,null,4);
    txt(g,'RGB888 → JPEG',1532,625,22,C.muted,500,'center','DIN');
  });
  enter(g,t,.1,()=>{
    txt(g,'原生 RGB 推理支路',120,682,26,C.muted);
    for(const [x,label] of [[120,'FPGA 采集 / 处理'],[495,'PCIe · RGB DMA'],[870,'RK 识别 / 显示']]){box(g,x,714,332,70,'#e5eff3',C.line,10);txt(g,label,x+166,749,28,C.ink,600,'center');}
    arrow(g,462,749,484);arrow(g,837,749,859);
    txt(g,'可选预览与归档压缩',120,843,25,C.muted);pill(g,'JPEG 有损',440,821,208,false,24);
  },.26);
  card(g,t,.12,1265,680,535,166,()=>{txt(g,'LZ77-16',1295,712,27,C.darkGreen,700);txt(g,'15 / 15',1295,764,46,C.darkGreen,600,'left','DIN');txt(g,'YUYV 输入逐字节往返一致',1295,820,25,C.muted);},1,'#e9f1ec');
  if(t>.45) {
    const q=clamp((t-.45)/1.35),x=452+418*q;
    if(q<1){g.beginPath();g.arc(x,749,4,0,Math.PI*2);g.fillStyle=C.blue;g.fill();}
  }
}

function tracking(g,t) {
  heading(g,'跨帧关联：真实记录对照','2026-10-26',t);
  enter(g,t,.08,()=>txt(g,'同一 session · ID 880',1800,207,28,C.muted,500,'right'));
  const starts=[.02,1.42,2.82];
  trackTrace.records.forEach((r,i)=>{
    const x=120+i*570;
    card(g,t,starts[i],x,253,540,575,()=>{
      const elapsed=r.capture_received_monotonic-trackTrace.records[0].capture_received_monotonic;
      txt(g,'源帧 '+r.source_frame_id,x+22,283,28,C.ink,600);txt(g,'+'+elapsed.toFixed(3)+' s',x+518,283,28,C.blue,600,'right','DIN');
      photo(g,'track'+r.source_frame_id,x+14,310,512,512*317/793);
      const scale=512/793,[x1,y1,x2,y2]=r.bbox_local_xyxy,bx=x+14+x1*scale,by=310+y1*scale,bw=(x2-x1)*scale,bh=(y2-y1)*scale;
      g.strokeStyle='#52e7a8';g.lineWidth=3;g.strokeRect(bx,by,bw,bh);
      box(g,bx,by-43,112,35,'#1e5b49',null,5);txt(g,'ID 880',bx+56,by-25.5,27,'#fff',700,'center','DIN');line(g,bx+56,by-8,bx+bw/2,by,'#baf8d4',1.8);
      txt(g,'原图位置',x+22,539,25,C.muted);txt(g,'局部细节',x+518,539,25,C.muted,500,'right');
      const crop=r.detail_crop_local_xyxy,dw=320,ds=dw/(crop[2]-crop[0]),dh=(crop[3]-crop[1])*ds,dx=x+(540-dw)/2,dy=568;
      photo(g,'detail'+r.source_frame_id,dx,dy,dw,dh);
      const dbx=dx+(x1-crop[0])*ds,dby=dy+(y1-crop[1])*ds,dbw=(x2-x1)*ds,dbh=(y2-y1)*ds;
      g.strokeStyle='#52e7a8';g.lineWidth=3;g.strokeRect(dbx,dby,dbw,dbh);
      box(g,dbx,dby-43,120,36,'#1e5b49',null,5);txt(g,'ID 880',dbx+60,dby-25,28,'#fff',700,'center','DIN');line(g,dbx+60,dby-7,dbx+dbw/2,dby,'#baf8d4',1.8);
    },i===0?-1:1);
    if(t>starts[i]+.28){
      const focus=.2+.8*(1-smooth((t-starts[i]-1.08)/.32));
      g.save();g.globalAlpha=focus;g.strokeStyle=C.darkGreen;g.lineWidth=3;g.beginPath();g.roundRect(x,253,540,575,14);g.stroke();g.restore();
    }
  });
  for(let i=0;i<2;i++)curve(g,[[390+i*570,861],[555+i*570,832],[795+i*570,832],[960+i*570,861]],smooth((t-1.2-i*1.4)/.55),C.darkGreen,2.5,true);
  [390,960,1530].forEach((x,i)=>{const p=smooth((t-starts[i]-.25)/.25);if(p>0){g.save();g.globalAlpha=p;g.beginPath();g.arc(x,861,6,0,Math.PI*2);g.fillStyle=C.green;g.fill();g.restore();}});
}

function dimensions(g,t) {
  heading(g,'多工件像素宽高测量','同时给出多个工件在原图中的宽/高',t);
  card(g,t,.02,104,266,1056,608,()=>photo(g,'dimensions',120,282,1024,576),-1);
  enter(g,t,.04,()=>txt(g,'2026-10-29 · 源帧 448296 · 原生 ROI 512 × 288',120,252,25,C.muted),.26);
  card(g,t,.08,1200,282,600,576,()=>{
    txt(g,'框序号',1240,329,25,C.muted,600);txt(g,'宽 w / px',1459,329,25,C.muted,600,'center');txt(g,'高 h / px',1684,329,25,C.muted,600,'center');line(g,1234,369,1766,369,C.line,1.5);
    dimensionRecords.dimensions.forEach((r,i)=>{
      const y=425+i*87,at=.5+i*.67,p=smooth((t-at)/.25),hold=1-smooth((t-at-.55)/.3);
      g.save();g.globalAlpha=p*(.15+.65*hold);box(g,1222,y-36,556,72,'#d8efdf',null,8);g.restore();
      txt(g,String(i+1),1281,y,37,C.ink,600,'center','DIN');txt(g,r.width_px.toFixed(2),1459,y,41,C.blue,600,'center','DIN');txt(g,r.height_px.toFixed(2),1684,y,41,C.blue,600,'center','DIN');
      if(i<3)line(g,1234,y+43,1766,y+43,C.faint,1);
    });
    txt(g,'w = x₂ − x₁     h = y₂ − y₁',1500,786,28,C.muted,500,'center','CJK');
    txt(g,'单位：px',1500,827,25,C.muted,500,'center');
  });
  if(t>.4)dimensionRecords.dimensions.forEach((r,i)=>{
    const [x1,y1,x2,y2]=r.bbox_xyxy_native_px,x=120+x1*2,y=282+y1*2,w=(x2-x1)*2,h=(y2-y1)*2,at=.5+i*.67,p=smooth((t-at)/.45);
    if(p<=0)return;
    const points=[[x,y],[x+w,y],[x+w,y+h],[x,y+h],[x,y]],lengths=[w,h,w,h];let left=(2*w+2*h)*p;
    g.beginPath();g.moveTo(x,y);
    for(let j=0;j<4;j++){const q=clamp(left/lengths[j]);g.lineTo(points[j][0]+(points[j+1][0]-points[j][0])*q,points[j][1]+(points[j+1][1]-points[j][1])*q);left-=lengths[j];if(q<1)break;}
    g.strokeStyle=C.green;g.lineWidth=3;g.lineJoin='round';g.stroke();
    g.save();g.globalAlpha=smooth((t-at)/.18);box(g,x,y-43,42,35,C.darkGreen,null,6);txt(g,String(i+1),x+21,y-25,25,'#fff',700,'center','DIN');g.restore();
    const fade=1-smooth((t-at-.6)/.35);
    if(fade>0){g.save();g.globalAlpha=fade*.8;curve(g,[[x+w/2,y-46],[x+w/2,318],[1160,350],[1201,425+i*87]],smooth((t-at-.15)/.35),C.darkGreen,1.7,true);g.restore();}
  });
}

function alerts(g,t) {
  heading(g,'网口回传与异常留证','历史回传记录',t);
  card(g,t,.02,120,260,1110,548,()=>{
    txt(g,'电脑接收回执 · receipt.json',160,304,32,C.ink,700);line(g,160,340,1190,340,C.line,1.5);
    for(const [at,y,h] of [[1.05,472,112],[2.0,610,105]]){
      const a=smooth((t-at)/.3)*(1-smooth((t-at-.7)/.4));g.save();g.globalAlpha=a*.72;box(g,145,y,1055,h,'#e0f0e4',null,7);g.restore();
    }
    txt(g,'"received_at"',160,381,27,C.blue,500,'left','Mono');txt(g,receipt.received_at,160,425,35,C.ink,500,'left','Mono');
    txt(g,'"manifest_sha256"',160,487,27,C.blue,500,'left','Mono');
    txt(g,receipt.manifest_sha256.slice(0,32),160,530,27,C.ink,500,'left','Mono');txt(g,receipt.manifest_sha256.slice(32),160,564,27,C.ink,500,'left','Mono');
    txt(g,'"event_id"',160,624,27,C.blue,500,'left','Mono');
    txt(g,receipt.event_id.slice(0,32),160,666,27,C.ink,500,'left','Mono');txt(g,receipt.event_id.slice(32),160,700,27,C.ink,500,'left','Mono');
    txt(g,'回执与清单对应 · SHA256 一致',160,765,30,C.darkGreen,600);
    const check=smooth((t-2.85)/.35);
    if(check>0){line(g,1152,764,1165,777,C.darkGreen,4);line(g,1165,777,1165+26*check,777-30*check,C.darkGreen,4);}
  },-1);
  card(g,t,.07,1270,260,530,320,()=>{
    txt(g,'对应原图',1302,304,29,C.ink,700);
    const a=smooth((t-.45)/.2)*(1-smooth((t-1.2)/.35));g.save();g.globalAlpha=a*.72;box(g,1559,340,215,93,'#e0f0e4',null,7);g.restore();
    photo(g,'network',1302,340,237.6,156);txt(g,'frame.rgb',1571,365,25,C.ink,600,'left','Mono');txt(g,'19,305 B',1571,411,30,C.blue,600,'left','DIN');txt(g,'原生 99 × 65',1302,539,24,C.muted);txt(g,'源帧 566648',1571,460,23,C.muted);
  });
  card(g,t,.12,1270,608,530,200,()=>{
    const a=smooth((t-3.2)/.3);g.save();g.globalAlpha=a*.6;box(g,1290,630,490,86,'#d9eedf',null,9);g.restore();
    txt(g,'100 / 100',1535,674,66,C.darkGreen,600,'center','DIN');txt(g,'归档文件长度与 SHA256 全匹配',1535,752,24,C.muted,500,'center');
  },1,'#e9f1ec');
  if(t>.4){
    curve(g,[[1270,418],[1245,418],[1260,530],[1230,530]],smooth((t-.65)/.65),C.darkGreen,2.4,true);
    curve(g,[[1230,700],[1250,700],[1245,707],[1270,707]],smooth((t-2.55)/.65),C.darkGreen,2.4,true);
  }
  enter(g,t,.1,()=>{box(g,120,837,1680,52,'#e5edf1',C.faint,10);txt(g,'支持可选 SD 留存',153,863,26,C.ink,600);},.26);
}

function draw(g,name,t,duration=durations[name]) {
  if(!initialized)throw new Error('Call evidence_scenes_v24.init() before draw()');
  g.reset();g.fillStyle=C.bg;g.fillRect(0,0,1920,1080);
  const light=g.createRadialGradient(640+25*Math.sin(t*.3),120,80,850,450,1480);light.addColorStop(0,'rgba(255,255,255,.36)');light.addColorStop(1,'rgba(255,255,255,0)');g.fillStyle=light;g.fillRect(0,0,1920,1080);
  ({isp_methods:isp,compression_pcie:compression,tracking_short:tracking,dimensions,alerts}[name])(g,t,duration);
  g.fillStyle=C.bg;g.fillRect(0,920,1920,160);
  return g.canvas;
}

function writeManifest() {
  const dataFiles=['evidence_isp_compression/evidence_audit.json','evidence_isp_compression/software_compression_summary.json','evidence_isp_compression/project_lz77_reference_software.json','evidence_tracking_dimensions/dimensions_exact_records.json','evidence_network_sd/manifest.json','evidence_network_sd/archive_verification.json','evidence_network_sd/source_records/0b15d69f8bb787d416ba2447e1b08d714f0f5fb36fb3d7a3e75e921ca1b2e6db/receipt.json'].map(file=>legacy+file);
  dataFiles.push('isp_assets/manifest.json','tracking_evidence/sequence_native/traceable.json');
  const sources=[...new Set([...Object.values(assets),...dataFiles])].map(file=>({file,sha256:crypto.createHash('sha256').update(fs.readFileSync(path.join(DATA_ROOT,file))).digest('hex')}));
  const manifest={width:1920,height:1080,fps:60,durations,palette:C,renderer:{file:'evidence_scenes_v24.cjs',sha256:crypto.createHash('sha256').update(fs.readFileSync(__filename)).digest('hex')},subtitle_safe_area:{x:0,y:920,w:1920,h:160},max_body_text_baseline:896,style:'B photographic evidence with 2D editorial animation',scene_text:visibleText,sources,
    facts:{
      isp_methods:{source:'isp_assets/manifest.json',provenance_label_displayed:false,input:'Archived processed RGB, not sensor RAW',method_order:ispStages.map(s=>s.id),focus_seconds:ispStages.map(s=>s.at),all_six_visible:true,layout:'Large direct input/result crop pair on left; six native results in two columns and three rows on right. All six have distinct result images. Each true direct input follows pair_inputs; the six methods are not drawn as one physical serial chain.',crop:{native_xywh:[160,58,224,126],integer_asset_scale:2},final_all_methods_hold_seconds:[9.2,10.5],method_provenance:'Offline deterministic processing, not measured FPGA before/after output; no bit-exact FPGA claim. Complete implemented FPGA method list retained at y=865/896, font 25px.'},
      compression_pcie:{source:'evidence_isp_compression/software_compression_summary.json',samples:15,dimensions:[512,288],input_rgb888_total_bytes:6635520,jpeg_q90_444_total_bytes:473287,ratio:14.020076613133257,lossy:true,lz77_reference:{source:'evidence_isp_compression/project_lz77_reference_software.json',input:'YUYV',roundtrip_equal:15,total:15},pipeline:'Native RGB inference is separate from optional preview/archive compression'},
      tracking_short:{source:'tracking_evidence/sequence_native/traceable.json',session_id:trackTrace.session_id,object_id:trackTrace.object_id,track_id:880,lifecycle:'confirmed',source_ids:trackTrace.selected_source_frames,source_date:'2026-10-26',native_size:[793,317],relative_capture_seconds:trackTrace.records.map(r=>r.capture_received_monotonic-trackTrace.records[0].capture_received_monotonic),drawn_boxes:true,boxes:trackTrace.records.map(r=>({source_frame_id:r.source_frame_id,bbox_local_xyxy:r.bbox_local_xyxy,detail_crop_local_xyxy:r.detail_crop_local_xyxy})),scope:'Historical original pixels and recorded identity association. Three samples share session/object ID, accepted classification and pixel identity checks; 16 recorded observations have one class and monotonic x. Sequence valid=false; this scene makes no accuracy, current-board, display-FPS or physical acceptance claim. Native PNGs are byte-for-byte equal to RGB pixels; no sharpening or reconstruction.'},
      dimensions:{source:'evidence_tracking_dimensions/dimensions_exact_records.json',source_frame_id:448296,source_date:'2026-10-29',boxes:dimensionRecords.dimensions.map(r=>({editorial_index:r.editorial_box_index,bbox:r.bbox_xyxy_native_px,width_px:r.width_px,height_px:r.height_px})),classification_displayed:false,confidence_displayed:false,units:'native ROI pixels'},
      alerts:{source:'evidence_network_sd/manifest.json',receipt_time_beijing:receipt.received_at,receipt_excerpt_fields:['received_at','manifest_sha256','event_id'],frame_bytes:19305,frame_native_dimensions:[99,65],source_frame_id:566648,offline_archive_checks:100,all_match:true,sd:'Optional enabled-and-mounted capability only; successful actual-card write is not evidenced'}},
    animation:{cards:{scale:[.9,1],entry_seconds:.28,trajectory:'soft curved translation to contact shadow'},isp:{comparison:'The actual direct input is revealed into its corresponding output by a .62s crop wipe; six separate native results remain visible.',starts:ispStages.map(s=>s.at),all_methods_hold_from_seconds:9.2},compression:{bar_start_seconds:.5,bar_duration_seconds:1.2,final_fraction:473287/6635520,values:'All numbers remain the actual final recorded values; no simulated online test progress'},tracking:{connections:'Editorial arcs between three same-ID record cards outside photographs; fixed ROI preserves left/middle/right positions; inset enlarges each genuine native detail crop. Boxes and ID880 are redrawn from exact log coordinates at readable final-canvas size.',card_starts:[.02,1.42,2.82],connection_starts:[1.2,2.6],position_interpolation:false},dimensions:{box_draw_starts:[.5,1.17,1.84,2.51],draw_duration_seconds:.45,coordinates:'Exact fixed historical bbox; only perimeter reveal and table row emphasis are animated'},alerts:{associations:'Original file to receipt/manifest hash to existing archive result; static recorded 100/100, no fabricated live transfer counter'},playback_progress_bar:false},
    rendering:'2D archived photographs, charts and real record typography; no 3D synthetic evidence; evaluate every output frame at frame/60. Asset pixels are never procedurally redrawn or altered.',verification_scope:'Local video asset rendering only; no model application, device connection or hardware changes'};
  fs.writeFileSync(path.join(__dirname,'visible_manifest.json'),JSON.stringify(manifest,null,2),'utf8');return manifest;
}

module.exports={init,draw,durations,visibleText,writeManifest};
if(require.main===module)(async()=>{
  await init();writeManifest();const canvas=createCanvas(1920,1080),g=canvas.getContext('2d');
  const previews={isp_methods:[1.3,4.3,8.8],compression_pcie:[.38,1,2],tracking_short:[1.0,2.4,4.1],dimensions:[.8,1.7,2.8],alerts:[.7,1.5,3.7]};
  for(const [name,times] of Object.entries(previews)){
    const mainTime=name==='isp_methods'?8.8:name==='dimensions'||name==='alerts'?3.8:name==='tracking_short'?4.1:2.5;draw(g,name,mainTime,durations[name]);fs.writeFileSync(path.join(__dirname,`${name}_evidence_preview.png`),canvas.toBuffer('image/png'));
    const strip=createCanvas(1920,360),sg=strip.getContext('2d');
    times.forEach((at,i)=>{draw(g,name,at,durations[name]);sg.drawImage(canvas,i*640,0,640,360);});fs.writeFileSync(path.join(__dirname,`${name}_motion_three_times.png`),strip.toBuffer('image/png'));
  }
})().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;});
