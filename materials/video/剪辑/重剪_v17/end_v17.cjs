'use strict';

const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const {createCanvas, GlobalFonts} = require('../../dataflow_film_20261028/node_modules/@napi-rs/canvas');

GlobalFonts.registerFromPath('C:/Windows/Fonts/msyh.ttc', 'Microsoft YaHei');
const C = {bg:'#0d1d29', ink:'#edf5f3', muted:'#a5bdc7', teal:'#78dfcb', gold:'#efcf8a', line:'#355768', panel:'#142d38'};
const cards = [
  {title:'图像处理与高速互通', details:['ISP · 双模式压缩 · PCIe'], color:C.teal, icon:'control'},
  {title:'现场快速适配', details:['15张原图 · 三类姿态','小样本学习 · 快速部署'], color:C.teal, icon:'samples'},
  {title:'多目标工业分析', details:['像素尺寸 · 边缘异常提示','网口回传 · 可选SD留存'], color:C.gold, icon:'measure'},
  {title:'多目标高速分拣', details:['实测1200工件/min','各组召回率99.997%'], color:C.gold, icon:'speed'},
];
const visibleText = ['从现场适配，到自动分拣', ...cards.flatMap(card=>[card.title,...card.details])];

function icon(g, name, x, y, color) {
  g.save();g.translate(x,y);g.strokeStyle=color;g.fillStyle=color;g.lineWidth=2.5;g.lineCap='round';g.lineJoin='round';
  if(name==='control') {
    for(const dy of [-12,12]) {g.beginPath();g.moveTo(-25,dy);g.lineTo(25,dy);g.stroke();}
    g.beginPath();g.moveTo(15,-21);g.lineTo(25,-12);g.lineTo(15,-3);g.stroke();
    g.beginPath();g.moveTo(-15,3);g.lineTo(-25,12);g.lineTo(-15,21);g.stroke();
  } else if(name==='samples') {
    for(let row=0;row<2;row++)for(let col=0;col<3;col++) {
      g.beginPath();g.roundRect(-27+col*21,-18+row*23,14,14,2);g.stroke();
    }
  } else if(name==='measure') {
    g.strokeRect(-25,-24,50,35);
    g.beginPath();g.moveTo(-25,24);g.lineTo(25,24);g.moveTo(-25,18);g.lineTo(-25,30);g.moveTo(25,18);g.lineTo(25,30);g.stroke();
  } else {
    g.beginPath();g.moveTo(-31,0);g.lineTo(-15,0);g.lineTo(-7,-19);g.lineTo(4,20);g.lineTo(13,-8);g.lineTo(20,0);g.lineTo(32,0);g.stroke();
  }
  g.restore();
}

function draw(g,t) {
  const ease = x=>{x=Math.max(0,Math.min(1,x));return x*x*(3-2*x);};
  g.reset();g.fillStyle=C.bg;g.fillRect(0,0,1920,1080);
  const glow=g.createRadialGradient(960,430,60,960,450,1050);
  glow.addColorStop(0,'#1b3845');glow.addColorStop(.7,'#112833');glow.addColorStop(1,C.bg);
  g.fillStyle=glow;g.fillRect(0,0,1920,1080);
  g.textBaseline='middle';g.textAlign='center';
  g.globalAlpha=ease(t/.35);g.fillStyle=C.ink;g.font='600 56px "Microsoft YaHei",sans-serif';
  g.fillText(visibleText[0],960,172);
  g.fillStyle=C.teal;g.fillRect(914,232,92,3);
  cards.forEach((card,i)=>{
    const p=ease((t-.07-i*.025)/.32),x=160+(i%2)*820,y=298+Math.floor(i/2)*267+(1-p)*10;
    g.globalAlpha=p;g.fillStyle=C.panel;g.strokeStyle=C.line;g.lineWidth=1.5;
    g.beginPath();g.roundRect(x,y,780,226,16);g.fill();g.stroke();
    g.fillStyle=card.color;g.fillRect(x+30,y+30,4,37);
    icon(g,card.icon,x+73,y+118,card.color);
    g.textAlign='left';g.font='600 39px "Microsoft YaHei",sans-serif';g.fillStyle=C.ink;
    g.fillText(card.title,x+136,y+65);
    g.font='400 30px "Microsoft YaHei",sans-serif';g.fillStyle=C.muted;
    card.details.forEach((detail,j)=>g.fillText(detail,x+136,y+119+j*46));
  });
  g.globalAlpha=1;
}

async function main() {
  const canvas=createCanvas(1920,1080),g=canvas.getContext('2d');
  draw(g,1.3);
  fs.writeFileSync(path.join(__dirname,'end_preview.png'),canvas.toBuffer('image/png'));
  fs.writeFileSync(path.join(__dirname,'end_visible_text.json'),JSON.stringify({duration_seconds:5,visible_text:visibleText,layout:'Four equally weighted project highlights',minimum_detail_font_px:30,metric_origin:'User supplied sorting speed and recall rate for all test groups',recall_rate:0.99997,recall_definition:'1 - 3/100000'},null,2),'utf8');
  if(process.argv.includes('--preview'))return;
  const ffmpeg=path.resolve(__dirname,'../../dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe');
  const proc=spawn(ffmpeg,['-hide_banner','-loglevel','warning','-y','-f','rawvideo','-pix_fmt','rgba','-s','1920x1080','-r','60','-i','pipe:0','-an','-c:v','h264_nvenc','-preset','p5','-cq','19','-b:v','0','-pix_fmt','yuv420p','-video_track_timescale','60000','-frames:v','300',path.join(__dirname,'end.mp4')],{cwd:__dirname,stdio:['pipe','ignore','pipe'],windowsHide:true});
  proc.stderr.on('data',data=>process.stderr.write(data));
  const done=new Promise(resolve=>proc.on('close',resolve));
  for(let n=0;n<300;n++) {draw(g,n/60);if(!proc.stdin.write(canvas.data()))await once(proc.stdin,'drain');}
  proc.stdin.end();
  if(await done!==0)throw new Error('End-card render failed');
  console.log('Rendered end.mp4: 5 seconds, 300 frames, four project highlights.');
}

module.exports={draw,visibleText};
if(require.main===module)main().catch(error=>{console.error(error);process.exitCode=1;});
