'use strict';

const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const {createCanvas, GlobalFonts} = require('../../dataflow_film_20261028/node_modules/@napi-rs/canvas');

GlobalFonts.registerFromPath('C:/Windows/Fonts/msyh.ttc', 'Microsoft YaHei');
const C = {bg:'#0d1d29', ink:'#edf5f3', muted:'#a5bdc7', teal:'#78dfcb', gold:'#efcf8a', line:'#355768'};
const visibleText = ['实测分拣速度', '1200', '工件/min', '小样本学习 · 高速识别 · 快速部署'];

function draw(g, t) {
  const ease = x => { x = Math.max(0, Math.min(1, x)); return x*x*(3-2*x); };
  const alpha = ease(t/.5);
  const rise = (1-alpha)*12;
  g.reset();
  g.fillStyle = C.bg; g.fillRect(0,0,1920,1080);
  const glow = g.createRadialGradient(960,410,25,960,440,1050);
  glow.addColorStop(0, '#1b3845'); glow.addColorStop(.65,'#112833'); glow.addColorStop(1,C.bg);
  g.fillStyle = glow; g.fillRect(0,0,1920,1080);
  g.save(); g.translate(0,rise); g.globalAlpha=alpha;
  g.textAlign='center';g.textBaseline='middle';
  g.font='500 34px "Microsoft YaHei",sans-serif';g.fillStyle=C.muted;
  g.fillText(visibleText[0],960,267);
  g.fillStyle=C.teal;g.fillRect(920,322,80,3);

  g.font='600 188px "Microsoft YaHei",sans-serif';
  const numberWidth=g.measureText(visibleText[1]).width;
  g.font='400 44px "Microsoft YaHei",sans-serif';
  const unitWidth=g.measureText(visibleText[2]).width;
  const left=(1920-numberWidth-unitWidth-26)/2;
  g.textAlign='left';g.font='600 188px "Microsoft YaHei",sans-serif';g.fillStyle=C.gold;
  g.fillText(visibleText[1],left,476);
  g.font='400 44px "Microsoft YaHei",sans-serif';g.fillStyle=C.ink;
  g.fillText(visibleText[2],left+numberWidth+26,530);

  g.strokeStyle=C.line;g.lineWidth=1.5;
  g.beginPath();g.moveTo(540,654);g.lineTo(1380,654);g.stroke();
  g.textAlign='center';g.font='500 39px "Microsoft YaHei",sans-serif';g.fillStyle=C.ink;
  g.fillText(visibleText[3],960,755);
  g.restore();
}

async function main() {
  const canvas=createCanvas(1920,1080),g=canvas.getContext('2d');
  draw(g,1.4);
  fs.writeFileSync(path.join(__dirname,'end_v15_preview.png'),canvas.toBuffer('image/png'));
  fs.writeFileSync(path.join(__dirname,'end_v15_visible_text.json'),JSON.stringify({duration_seconds:5,visible_text:visibleText,metric_origin:'User supplied measured sorting speed in current request'},null,2),'utf8');
  if(process.argv.includes('--preview'))return;
  const ffmpeg=path.resolve(__dirname,'../../dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe');
  const output=path.join(__dirname,'end.mp4');
  const p=spawn(ffmpeg,['-hide_banner','-loglevel','warning','-y','-f','rawvideo','-pix_fmt','rgba','-s','1920x1080','-r','60','-i','pipe:0','-an','-c:v','h264_nvenc','-preset','p5','-cq','19','-b:v','0','-pix_fmt','yuv420p','-video_track_timescale','60000','-frames:v','300',output],{cwd:__dirname,stdio:['pipe','ignore','pipe'],windowsHide:true});
  p.stderr.on('data',d=>process.stderr.write(d));
  const done=new Promise(resolve=>p.on('close',resolve));
  for(let n=0;n<300;n++){
    draw(g,n/60);
    if(!p.stdin.write(canvas.data()))await once(p.stdin,'drain');
  }
  p.stdin.end();
  if(await done!==0)throw new Error('End-card render failed');
  console.log('Rendered end.mp4: 5.0s / 300 frames / 1920x1080 / 60fps.');
}

module.exports={draw,visibleText};
if(require.main===module)main().catch(error=>{console.error(error);process.exitCode=1;});
