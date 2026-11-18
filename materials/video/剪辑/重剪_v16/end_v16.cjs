'use strict';

const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const {createCanvas, GlobalFonts} = require('../../dataflow_film_20261028/node_modules/@napi-rs/canvas');

GlobalFonts.registerFromPath('C:/Windows/Fonts/msyh.ttc', 'Microsoft YaHei');
const C = {bg:'#0d1d29', ink:'#edf5f3', muted:'#a5bdc7', teal:'#78dfcb', gold:'#efcf8a', line:'#355768', panel:'#142d38'};
const cards = [
  {title:'FPGA 双向调参', detail:'参数下发 · 状态回读', color:C.teal, icon:'control'},
  {title:'小样本学习', detail:'15张原图 · 3类姿态', color:C.teal, icon:'samples'},
  {title:'快速部署', detail:'采集 · 训练 · 部署', color:C.gold, icon:'deploy'},
  {title:'高速识别与分拣', detail:'实测1200工件/min', color:C.gold, icon:'speed'},
];
const visibleText = ['从现场适配，到自动分拣', ...cards.flatMap(card=>[card.title,card.detail])];

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
  } else if(name==='deploy') {
    g.beginPath();g.moveTo(0,-26);g.lineTo(0,8);g.moveTo(-12,-2);g.lineTo(0,10);g.lineTo(12,-2);g.stroke();
    g.beginPath();g.moveTo(-25,5);g.lineTo(-25,23);g.lineTo(25,23);g.lineTo(25,5);g.stroke();
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
    g.textAlign='left';g.font='600 43px "Microsoft YaHei",sans-serif';g.fillStyle=C.ink;
    g.fillText(card.title,x+136,y+85);
    g.font='400 33px "Microsoft YaHei",sans-serif';g.fillStyle=C.muted;
    g.fillText(card.detail,x+136,y+149);
  });
  g.globalAlpha=1;
}

async function main() {
  const canvas=createCanvas(1920,1080),g=canvas.getContext('2d');
  draw(g,1.3);
  fs.writeFileSync(path.join(__dirname,'end_preview.png'),canvas.toBuffer('image/png'));
  fs.writeFileSync(path.join(__dirname,'end_visible_text.json'),JSON.stringify({duration_seconds:5,visible_text:visibleText,layout:'Four equally weighted project highlights',metric_origin:'User supplied measured sorting speed'},null,2),'utf8');
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
