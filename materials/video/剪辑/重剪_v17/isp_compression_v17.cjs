'use strict';
const fs=require('node:fs');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const {createCanvas,GlobalFonts}=require('../../dataflow_film_20261028/node_modules/@napi-rs/canvas');
GlobalFonts.registerFromPath('C:/Windows/Fonts/msyh.ttc','Microsoft YaHei');
const C={bg:'#0d1d29',panel:'#142d38',ink:'#edf5f3',muted:'#a5bdc7',teal:'#78dfcb',gold:'#efcf8a',line:'#355768'};
const definitions={
  isp_methods:{seconds:6,title:'FPGA ISP 图像预处理',visible_text:['FPGA ISP 图像预处理','四像素并行流水线','去马赛克','白平衡','高斯平滑','直方图映射','前景分割','灰度 / 二值形态学','Sobel 边缘提取','彩色轮廓叠加','数字增益 · 亮度 · 对比度']},
  compression_pcie:{seconds:6.5,title:'PCIe 高速互通与双模式压缩',visible_text:['PCIe 高速互通与双模式压缩','原生 RGB 推理支路','FPGA','采集与处理','PCIe','图像 DMA 上行','参数下发','状态回读','RK','识别与显示','可选压缩链路 · 预览与归档','预览 / 归档','JPEG 有损','LZ77 无损','PCIe → RK','接收与解码']}
};
const ease=x=>{x=Math.max(0,Math.min(1,x));return x*x*(3-2*x);};
function text(g,value,x,y,size=32,color=C.ink,weight=500,align='center'){
  g.font=`${weight} ${size}px "Microsoft YaHei",sans-serif`;g.fillStyle=color;g.textAlign=align;g.textBaseline='middle';g.fillText(value,x,y);
}
function box(g,x,y,w,h,stroke=C.line,fill=C.panel,r=16){
  g.beginPath();g.roundRect(x,y,w,h,r);g.fillStyle=fill;g.fill();g.strokeStyle=stroke;g.lineWidth=1.5;g.stroke();
}
function arrow(g,x1,y1,x2,y2,color=C.teal){
  const a=Math.atan2(y2-y1,x2-x1);g.strokeStyle=color;g.fillStyle=color;g.lineWidth=2.3;
  g.beginPath();g.moveTo(x1,y1);g.lineTo(x2,y2);g.stroke();g.beginPath();g.moveTo(x2,y2);g.lineTo(x2-11*Math.cos(a-.43),y2-11*Math.sin(a-.43));g.lineTo(x2-11*Math.cos(a+.43),y2-11*Math.sin(a+.43));g.closePath();g.fill();
}
function background(g,title,t){
  g.reset();g.fillStyle=C.bg;g.fillRect(0,0,1920,1080);const glow=g.createRadialGradient(960,420,40,960,450,1100);glow.addColorStop(0,'#1b3845');glow.addColorStop(.7,'#112833');glow.addColorStop(1,C.bg);g.fillStyle=glow;g.fillRect(0,0,1920,1080);
  g.globalAlpha=ease(t/.32);text(g,title,960,136,55,C.ink,600);g.fillStyle=C.teal;g.fillRect(914,203,92,3);
}
function icon(g,i,x,y,color){
  g.save();g.translate(x,y);g.strokeStyle=color;g.fillStyle=color;g.lineWidth=2.4;g.lineCap='round';g.lineJoin='round';
  if(i===0){for(let r=0;r<3;r++)for(let c=0;c<4;c++){g.globalAlpha=.45+((r+c)%3)*.22;g.fillStyle=[C.teal,C.gold,C.muted][(r+c)%3];g.fillRect(-29+c*17,-21+r*17,10,10);}}
  else if(i===1){for(let j=-1;j<=1;j++){g.beginPath();g.moveTo(j*24,-24);g.lineTo(j*24,24);g.stroke();g.beginPath();g.arc(j*24,j===0?-8:8,6,0,Math.PI*2);g.fill();}}
  else if(i===2){for(let r=-1;r<=1;r++)for(let c=-1;c<=1;c++){g.globalAlpha=r===0&&c===0?1:(r===0||c===0?.65:.35);g.beginPath();g.arc(c*22,r*22,r===0&&c===0?7:5,0,Math.PI*2);g.fill();}}
  else if(i===3){g.beginPath();g.moveTo(-33,24);g.lineTo(-33,-24);g.moveTo(-33,24);g.lineTo(34,24);g.stroke();for(let j=0;j<6;j++){g.globalAlpha=.45+j*.09;g.fillRect(-23+j*10,17-(j<3?j*9:(6-j)*9),6,8+(j<3?j*9:(6-j)*9));}}
  else if(i===4){for(let r=-1;r<=1;r++)for(let c=-1;c<=1;c++){g.globalAlpha=r===0||c===0?1:.2;g.fillRect(c*20-6,r*20-6,12,12);}}
  else if(i===5){for(let j=0;j<2;j++){g.beginPath();g.roundRect(-35+j*40,-21,29,42,5);g.stroke();}g.beginPath();g.moveTo(-3,0);g.lineTo(5,0);g.stroke();}
  else if(i===6){g.beginPath();g.moveTo(-35,20);g.lineTo(-13,20);g.lineTo(-13,-19);g.lineTo(13,-19);g.lineTo(13,20);g.lineTo(35,20);g.stroke();}
  else {g.beginPath();g.moveTo(-36,22);g.lineTo(-22,-14);g.lineTo(7,-25);g.lineTo(35,2);g.lineTo(22,25);g.closePath();g.stroke();g.globalAlpha=.35;g.beginPath();g.moveTo(-30,22);g.lineTo(-16,-14);g.lineTo(13,-25);g.lineTo(41,2);g.stroke();}
  g.restore();
}
function isp(g,t){
  const q=definitions.isp_methods.visible_text;background(g,q[0],t);g.globalAlpha=ease((t-.05)/.35);text(g,q[1],960,258,34,C.teal);
  for(let i=0;i<8;i++){
    const a=ease((t-.08-i*.016)/.36),x=100+(i%4)*440,y=338+Math.floor(i/4)*208+(1-a)*8;g.globalAlpha=a;
    box(g,x,y,400,176);icon(g,i,x+200,y+56,i<4?C.teal:C.gold);text(g,q[i+2],x+200,y+123,i===5?31:34,C.ink,500);
  }
  g.globalAlpha=ease((t-.18)/.38);g.fillStyle=C.line;g.fillRect(460,784,1000,1);text(g,q[10],960,837,34,C.muted);
  g.globalAlpha=1;
}
function compression(g,t){
  const q=definitions.compression_pcie.visible_text;background(g,q[0],t);g.globalAlpha=ease((t-.05)/.35);
  text(g,q[1],960,251,32,C.teal);
  box(g,100,294,360,250,C.teal);text(g,q[2],280,389,48,C.teal,600);text(g,q[3],280,461,31,C.muted);
  box(g,710,294,500,250);text(g,q[4],960,331,42,C.ink,600);
  box(g,1460,294,360,250,C.gold);text(g,q[8],1640,389,48,C.gold,600);text(g,q[9],1640,461,31,C.muted);
  const rows=[{y:383,value:q[5],color:C.teal,right:true},{y:447,value:q[6],color:C.gold,right:false},{y:507,value:q[7],color:C.teal,right:true}];
  for(const row of rows){
    text(g,row.value,960,row.y,27,row.color);
    if(row.right){arrow(g,477,row.y,694,row.y,row.color);arrow(g,1226,row.y,1444,row.y,row.color);}
    else {arrow(g,694,row.y,477,row.y,row.color);arrow(g,1444,row.y,1226,row.y,row.color);}
  }
  g.globalAlpha=ease((t-.12)/.38);box(g,100,605,1720,260,C.line,'#102632');text(g,q[10],960,642,33,C.muted);
  box(g,145,712,270,98);text(g,q[11],280,761,30,C.muted);
  box(g,495,712,735,98,C.gold);g.strokeStyle=C.line;g.lineWidth=1.5;g.beginPath();g.moveTo(862,729);g.lineTo(862,793);g.stroke();text(g,q[12],679,761,35,C.gold);text(g,q[13],1047,761,35,C.teal);
  box(g,1440,700,335,122);text(g,q[14],1607,738,32,C.ink);text(g,q[15],1607,786,25,C.muted);
  arrow(g,431,761,479,761,C.gold);arrow(g,1247,761,1424,761,C.gold);
  arrow(g,280,561,280,697,C.line);arrow(g,1640,683,1640,561,C.line);
  g.globalAlpha=1;
}
const draw={isp_methods:isp,compression_pcie:compression};
async function render(name,previewOnly){
  const definition=definitions[name],canvas=createCanvas(1920,1080),g=canvas.getContext('2d');
  draw[name](g,1.2);fs.writeFileSync(path.join(__dirname,`${name}_preview.png`),canvas.toBuffer('image/png'));
  fs.writeFileSync(path.join(__dirname,`${name}_visible_text.json`),JSON.stringify({duration_seconds:definition.seconds,visible_text:definition.visible_text,source_audit:'audit/isp_compression.md',subtitle_area_y:900},null,2),'utf8');
  if(previewOnly)return;
  const ff=path.resolve(__dirname,'../../dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe'),frames=definition.seconds*60;
  const proc=spawn(ff,['-hide_banner','-loglevel','warning','-y','-f','rawvideo','-pix_fmt','rgba','-s','1920x1080','-r','60','-i','pipe:0','-an','-c:v','h264_nvenc','-preset','p5','-cq','19','-b:v','0','-pix_fmt','yuv420p','-video_track_timescale','60000','-frames:v',String(frames),path.join(__dirname,`${name}.mp4`)],{cwd:__dirname,stdio:['pipe','ignore','pipe'],windowsHide:true});
  proc.stderr.on('data',data=>process.stderr.write(data));const done=new Promise(resolve=>proc.on('close',resolve));
  for(let n=0;n<frames;n++){draw[name](g,n/60);if(!proc.stdin.write(canvas.data()))await once(proc.stdin,'drain');}
  proc.stdin.end();if(await done!==0)throw new Error(name+' render failed');console.log(`${name}: ${frames} frames / ${definition.seconds}s`);
}
if(require.main===module){const name=process.argv.find(a=>Object.hasOwn(definitions,a));const names=name?[name]:Object.keys(definitions);(async()=>{for(const n of names)await render(n,process.argv.includes('--preview'));})().catch(error=>{console.error(error);process.exitCode=1;});}
module.exports={definitions,draw};
