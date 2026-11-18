'use strict';

const {createCanvas, GlobalFonts} = require('../../dataflow_film_20261028/node_modules/@napi-rs/canvas');

const durations = {chapter_board:1.5, chapter_console:1.5, chapter_logic:1.5, application:3};
const C = {bg:'#0d1d29', ink:'#edf5f3', muted:'#a5bdc7', line:'#355768', teal:'#78dfcb', gold:'#efcf8a'};
const cards = {
  chapter_board:{number:'01', title:'板卡介绍', subtitle:'RK · FPGA · 控制板'},
  chapter_console:{number:'02', title:'控制台操作', subtitle:'画面 · 标注 · 训练 · 运行'},
  chapter_logic:{number:'03', title:'识别与控制', subtitle:'关联 · 确认 · 触发'},
  application:{number:'04', title:'实战与应用', subtitle:'姿态分拣 · 类别分拣'}
};
const visibleText = Object.values(cards).flatMap(card=>[`${card.number} ${card.title}`, card.subtitle]);

function draw(g,t,w=1920,h=1080,name='chapter_board') {
  const card=cards[name];
  if(!card)throw new Error(`Unknown chapter card: ${name}`);
  const smooth=value=>{const p=Math.max(0,Math.min(1,value)); return p*p*(3-2*p);};
  const opacity=Math.min(smooth(t/.19),smooth((durations[name]-t)/.19));
  g.reset(); g.save(); g.scale(w/1920,h/1080);
  g.fillStyle=C.bg; g.fillRect(0,0,1920,1080);
  g.globalAlpha=opacity;
  const light=g.createRadialGradient(1120,440,100,1050,470,1150);
  light.addColorStop(0,'#17333f'); light.addColorStop(.62,'#112733'); light.addColorStop(1,C.bg);
  g.fillStyle=light; g.fillRect(0,0,1920,930);

  // Quiet chapter marker; the typography carries the transition.
  g.fillStyle=C.gold; g.fillRect(320,340,52,3);
  g.strokeStyle=C.line; g.lineWidth=1.5;
  g.beginPath(); g.moveTo(320,631); g.lineTo(1600,631); g.stroke();
  g.fillStyle=C.teal; g.fillRect(320,631,112,3);

  g.textAlign='left'; g.textBaseline='middle';
  g.font='600 46px "Microsoft YaHei",sans-serif'; g.fillStyle=C.teal;
  g.fillText(card.number,320,456);
  g.font='600 88px "Microsoft YaHei",sans-serif'; g.fillStyle=C.ink;
  g.fillText(card.title,432,456);
  g.font='400 38px "Microsoft YaHei",sans-serif'; g.fillStyle=C.muted;
  let subtitleX=436;
  const parts=card.subtitle.split(' · ');
  parts.forEach((part,i)=>{
    g.fillText(part,subtitleX,565);
    subtitleX+=g.measureText(part).width;
    if(i<parts.length-1) {
      g.beginPath(); g.arc(subtitleX+21,565,2.2,0,Math.PI*2); g.fill();
      subtitleX+=42;
    }
  });

  const index=Number(card.number)-1;
  for(let i=0;i<4;i++) {
    g.fillStyle=i===index?C.teal:C.line;
    g.fillRect(320+i*42,702,i===index?24:12,3);
  }
  g.restore();
}

module.exports={durations,draw,visibleText};

if(require.main===module) {
  const fs=require('node:fs'), path=require('node:path');
  GlobalFonts.registerFromPath('C:/Windows/Fonts/msyh.ttc','Microsoft YaHei');
  const canvas=createCanvas(1920,1080), g=canvas.getContext('2d');
  for(const [name,duration] of Object.entries(durations)) {
    draw(g,duration/2,1920,1080,name);
    fs.writeFileSync(path.join(__dirname,`preview_v12_${name}.png`),canvas.toBuffer('image/png'));
  }
}
