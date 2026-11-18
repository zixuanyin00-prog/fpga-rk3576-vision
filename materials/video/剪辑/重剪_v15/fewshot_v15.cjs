'use strict';

const fs=require('node:fs');
const path=require('node:path');
const {createCanvas,loadImage,GlobalFonts}=require('../../dataflow_film_20261028/node_modules/@napi-rs/canvas');

const durations={fewshot_value:7};
const visibleText=['小样本学习，快速适配','15张现场原图','训练','同一模型 · 3类姿态','A','B','C','现场采样 → 小样本训练 → 快速部署'];
const C={bg:'#0d1d29',panel:'#112b37',line:'#355768',ink:'#edf5f3',muted:'#a5bdc7',teal:'#78dfcb',gold:'#efcf8a'};
let samples=[];

async function init() {
  GlobalFonts.registerFromPath('C:/Windows/Fonts/msyh.ttc','Microsoft YaHei');
  samples=await Promise.all(Array.from({length:15},(_,i)=>loadImage(path.join(__dirname,'..','重剪_v13','sample_assets',`native_${i}.png`))));
}

function draw(g,t,w=1920,h=1080,name='fewshot_value') {
  if(name!=='fewshot_value')throw new Error(`Unknown animation: ${name}`);
  if(samples.length!==15)throw new Error('Call and await init() before drawing the sample animation.');
  const ease=value=>{const p=Math.max(0,Math.min(1,value));return p*p*(3-2*p);};
  g.reset();g.save();g.scale(w/1920,h/1080);
  g.fillStyle=C.bg;g.fillRect(0,0,1920,1080);
  const light=g.createRadialGradient(1040,450,80,1000,480,1100);
  light.addColorStop(0,'#1b3845');light.addColorStop(.65,'#112833');light.addColorStop(1,C.bg);
  g.fillStyle=light;g.fillRect(0,0,1920,930);
  g.textBaseline='middle';g.textAlign='center';

  g.globalAlpha=ease((t-.08)/.35);
  g.font='600 56px "Microsoft YaHei",sans-serif';g.fillStyle=C.ink;
  g.fillText(visibleText[0],960,139);
  g.fillStyle=C.teal;g.fillRect(914,208,92,3);

  g.globalAlpha=ease((t-.55)/.35);
  g.textAlign='left';g.font='500 34px "Microsoft YaHei",sans-serif';g.fillStyle=C.muted;
  g.fillText(visibleText[1],120,278);
  for(let i=0;i<15;i++) {
    const p=ease((t-.7-i*.075)/.34),x=120+(i%5)*190,y=344+Math.floor(i/5)*122+(1-p)*10;
    g.globalAlpha=p;
    g.fillStyle=C.panel;g.strokeStyle=C.line;g.lineWidth=1.5;
    g.beginPath();g.roundRect(x-4,y-4,184,107,7);g.fill();g.stroke();
    // The PNG retains all 512 x 288 source pixels; only this display tile is fitted.
    g.drawImage(samples[i],x,y,176,99);
  }

  const flow=ease((t-2.15)/.45);
  g.globalAlpha=flow;g.strokeStyle=C.gold;g.fillStyle=C.gold;g.lineWidth=2.5;
  for(const [left,right] of [[1081,1124],[1277,1320]]) {
    g.beginPath();g.moveTo(left,514);g.lineTo(right,514);g.stroke();
    g.beginPath();g.moveTo(right,514);g.lineTo(right-10,508);g.lineTo(right-10,520);g.closePath();g.fill();
  }
  g.fillStyle=C.panel;g.strokeStyle=C.gold;g.lineWidth=1.6;
  g.beginPath();g.roundRect(1140,450,120,128,18);g.fill();g.stroke();
  for(let i=0;i<3;i++)for(let j=0;j<2;j++) {
    g.fillStyle=i===1&&j===0?C.gold:C.line;
    g.fillRect(1173+i*20,475+j*18,12,10);
  }
  g.textAlign='center';g.font='500 26px "Microsoft YaHei",sans-serif';g.fillStyle=C.gold;
  g.fillText(visibleText[2],1200,548);

  g.globalAlpha=ease((t-2.7)/.35);
  g.textAlign='left';g.font='500 34px "Microsoft YaHei",sans-serif';g.fillStyle=C.muted;
  g.fillText(visibleText[3],1352,278);
  for(let i=0;i<3;i++) {
    const p=ease((t-2.95-i*.48)/.4),x=1352,y=344+i*122+(1-p)*9;
    g.globalAlpha=p;g.fillStyle=C.panel;g.strokeStyle=C.line;g.lineWidth=1.5;
    g.beginPath();g.roundRect(x,y,448,99,13);g.fill();g.stroke();
    g.font='600 40px "Microsoft YaHei",sans-serif';g.fillStyle=C.teal;
    g.fillText(visibleText[4+i],x+34,y+50);
    g.strokeStyle=C.line;g.lineWidth=1.5;
    g.beginPath();g.moveTo(x+104,y+50);g.lineTo(x+376,y+50);g.stroke();
    g.fillStyle=C.teal;g.beginPath();g.arc(x+383,y+50,3,0,Math.PI*2);g.fill();
  }

  g.globalAlpha=ease((t-4.25)/.45);
  g.fillStyle=C.teal;g.fillRect(763,787,394,2);
  g.textAlign='center';g.font='500 37px "Microsoft YaHei",sans-serif';g.fillStyle=C.ink;
  g.fillText(visibleText[7],960,847);
  g.restore();
}

module.exports={durations,draw,visibleText,init};

if(require.main===module) {
  (async()=>{
    await init();
    const canvas=createCanvas(1920,1080),g=canvas.getContext('2d');
    for(const t of [2.1,4.5,6]) {
      draw(g,t);fs.writeFileSync(path.join(__dirname,`preview_v15_fewshot_${t}.png`),canvas.toBuffer('image/png'));
    }
    fs.writeFileSync(path.join(__dirname,'fewshot_v15_visible_text.json'),JSON.stringify(visibleText,null,2),'utf8');
  })();
}
