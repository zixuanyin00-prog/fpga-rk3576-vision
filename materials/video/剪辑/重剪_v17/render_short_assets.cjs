'use strict';

const fs=require('node:fs'),path=require('node:path');
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const {createCanvas,GlobalFonts}=require('../../dataflow_film_20261028/node_modules/@napi-rs/canvas');
const original=require('../重剪_v10/animations_v10.cjs');

GlobalFonts.registerFromPath('C:/Windows/Fonts/msyh.ttc','Microsoft YaHei');
const durations={tracking_short:4,trigger_short:4,chapter_logic:1.5};
const C={bg:'#0d1d29',ink:'#edf5f3',muted:'#a5bdc7',line:'#355768',teal:'#78dfcb',gold:'#efcf8a'};
const chapter={number:'03',title:'识别与工业检测',subtitle:'识别 · 测量 · 告警'};
const visibleText={
  tracking_short:['同一工件，连续确认','真实观测 1','真实观测 2','真实观测 3','ID 07','连续观测积累','类别已确认','同一身份 · 连续真实观测'],
  trigger_short:['确认类别，真实过线后触发','真实位置越过触发线','触发线','过线前','已真实过线','触发条件','所选类别已确认','控制输出已启用','真实位置已过线','设定延时','控制脉冲','脉宽','过线已记录','ID 07'],
  chapter_logic:[chapter.number+' '+chapter.title,chapter.subtitle]
};

function draw(g,t,name) {
  if(name==='tracking_short') {
    // Confirmation is reached at t=4.1/1.75=2.343s; the final state remains legible.
    original.draw(g,Math.min(t*1.75,5.8),1920,1080,name);
    return;
  }
  if(name==='trigger_short') {
    // The original recorded-state fade completes at 4.85s. Complete it by 2.425s,
    // leaving more than 1.5s for reading without cutting the sequence midway.
    original.draw(g,Math.min(t*2,5.2),1920,1080,name);
    return;
  }
  const smooth=value=>{const p=Math.max(0,Math.min(1,value));return p*p*(3-2*p);};
  const opacity=Math.min(smooth(t/.19),smooth((1.5-t)/.19));
  g.reset();g.save();g.fillStyle=C.bg;g.fillRect(0,0,1920,1080);g.globalAlpha=opacity;
  const light=g.createRadialGradient(1120,440,100,1050,470,1150);
  light.addColorStop(0,'#17333f');light.addColorStop(.62,'#112733');light.addColorStop(1,C.bg);
  g.fillStyle=light;g.fillRect(0,0,1920,930);
  g.fillStyle=C.gold;g.fillRect(320,340,52,3);
  g.strokeStyle=C.line;g.lineWidth=1.5;g.beginPath();g.moveTo(320,631);g.lineTo(1600,631);g.stroke();
  g.fillStyle=C.teal;g.fillRect(320,631,112,3);
  g.textAlign='left';g.textBaseline='middle';
  g.font='600 46px "Microsoft YaHei",sans-serif';g.fillStyle=C.teal;g.fillText(chapter.number,320,456);
  g.font='600 88px "Microsoft YaHei",sans-serif';g.fillStyle=C.ink;g.fillText(chapter.title,432,456);
  g.font='400 38px "Microsoft YaHei",sans-serif';g.fillStyle=C.muted;
  let x=436;const words=chapter.subtitle.split(' · ');
  words.forEach((word,i)=>{g.fillText(word,x,565);x+=g.measureText(word).width;if(i<words.length-1){g.beginPath();g.arc(x+21,565,2.2,0,Math.PI*2);g.fill();x+=42;}});
  for(let i=0;i<4;i++){g.fillStyle=i===2?C.teal:C.line;g.fillRect(320+i*42,702,i===2?24:12,3);}
  g.restore();
}

async function main() {
  const canvas=createCanvas(1920,1080),g=canvas.getContext('2d');
  const previews=[['tracking_short',1.4],['tracking_short',3.3],['trigger_short',1.7],['trigger_short',3.3],['chapter_logic',.75]];
  for(const [name,t]of previews){draw(g,t,name);fs.writeFileSync(path.join(__dirname,`preview_${name}_${t}.png`),canvas.toBuffer('image/png'));}
  fs.writeFileSync(path.join(__dirname,'short_animation_sources.json'),JSON.stringify({
    source:'../重剪_v10/animations_v10.cjs',durations,visible_text:visibleText,
    tracking_time_mapping:'min(t * 1.75, 5.8)',trigger_time_mapping:'min(t * 2.0, 5.2)',
    tracking_final_state_hold_seconds:4-4.1/1.75,trigger_final_state_hold_seconds:4-4.85/2,
    rendering:'Procedural 60fps redraw; no existing video speed change or cloned video frames'
  },null,2),'utf8');
  if(process.argv.includes('--preview'))return;
  const ff=path.resolve(__dirname,'../../dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe');
  for(const [name,seconds]of Object.entries(durations)) {
    const count=Math.round(seconds*60),out=path.join(__dirname,name+'.mp4');
    const proc=spawn(ff,['-hide_banner','-loglevel','warning','-y','-f','rawvideo','-pix_fmt','rgba','-s','1920x1080','-r','60','-i','pipe:0','-an','-c:v','h264_nvenc','-preset','p5','-cq','19','-b:v','0','-pix_fmt','yuv420p','-video_track_timescale','60000','-frames:v',String(count),out],{cwd:__dirname,windowsHide:true,stdio:['pipe','ignore','pipe']});
    proc.stderr.on('data',data=>process.stderr.write(data));
    const done=new Promise(resolve=>proc.on('close',resolve));
    for(let frame=0;frame<count;frame++){draw(g,frame/60,name);if(!proc.stdin.write(canvas.data()))await once(proc.stdin,'drain');}
    proc.stdin.end();if(await done!==0)throw new Error('Failed render '+name);
    console.log(`${name}: ${seconds}s / ${count} frames`,out);
  }
}

module.exports={draw,durations,visibleText};
if(require.main===module)main().catch(error=>{console.error(error);process.exitCode=1;});
