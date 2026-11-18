'use strict';
const durations={value:16};
const C={bg:'#0d1d29',panel:'#17313f',deep:'#102632',line:'#355768',ink:'#edf5f3',muted:'#a5bdc7',teal:'#78dfcb',gold:'#efcf8a'};
const words={title:'从识别类别，到自动分拣',input:'识别类别',dimensions:'型号  ·  正反面  ·  数字方向',
 models:['型号 A','型号 B','型号 C'],codes:['103','473','102'],select:'选定类别与触发',selected:'类别 A',
 conditions:'类别确认  ·  真实过线',enable:'输出启用',pulse:'单路控制脉冲',controller:'后续控制器',receive:'接收控制脉冲',valve:'电磁阀',execute:'分拣执行'};
const visibleText=[...new Set(Object.values(words).flat())];
const ease=x=>{x=Math.max(0,Math.min(1,x));return x*x*(3-2*x)};
function text(g,s,x,y,size=30,col=C.ink){g.font=`${size}px "Microsoft YaHei",sans-serif`;g.textAlign='center';g.textBaseline='middle';g.fillStyle=col;g.fillText(s,x,y)}
function box(g,x,y,w,h,col=C.panel,line=C.line,r=18){g.beginPath();g.roundRect(x,y,w,h,r);g.fillStyle=col;g.fill();if(line){g.strokeStyle=line;g.lineWidth=1.5;g.stroke()}}
function layer(g,a,fn){g.save();g.globalAlpha*=Math.max(0,Math.min(1,a));fn();g.restore()}
function arrow(g,pts,col=C.line){g.strokeStyle=col;g.lineWidth=2.5;g.beginPath();pts.forEach((p,i)=>i?g.lineTo(...p):g.moveTo(...p));g.stroke();const p=pts.at(-1),q=pts.at(-2),a=Math.atan2(p[1]-q[1],p[0]-q[0]);g.beginPath();g.moveTo(...p);g.lineTo(p[0]-12*Math.cos(a-.4),p[1]-12*Math.sin(a-.4));g.lineTo(p[0]-12*Math.cos(a+.4),p[1]-12*Math.sin(a+.4));g.closePath();g.fillStyle=col;g.fill()}
function resistor(g,x,y,code){g.save();g.translate(x,y);box(g,-65,-31,130,62,'#a4b6b7','#d5e5df',6);g.fillStyle='#e2ebe7';g.fillRect(-67,-25,17,50);g.fillRect(50,-25,17,50);text(g,code,0,1,26,'#243c43');g.restore()}
function draw(g,t,w=1920,h=1080,part='value'){
 g.reset();g.save();g.scale(w/1920,h/1080);g.fillStyle=C.bg;g.fillRect(0,0,1920,1080);const bg=g.createRadialGradient(950,430,40,950,500,1100);bg.addColorStop(0,'#203d4b');bg.addColorStop(1,C.bg);g.fillStyle=bg;g.fillRect(0,0,1920,1080);
 text(g,words.title,960,116,48);const flow=ease((t-6.8)/1.0),active=t>=7.3;
 box(g,100,255,570,560);text(g,words.input,385,297,34,C.teal);
 for(let i=0;i<3;i++)layer(g,ease((t-.8-i*.4)/.8)*(active&&i>0?.48:1),()=>{box(g,139,317+i*150,492,106,C.deep,active&&i===0?C.teal:C.line,13);resistor(g,255,370+i*150,words.codes[i]);text(g,words.models[i],470,370+i*150,30,active&&i===0?C.teal:C.ink)});
 layer(g,ease((t-2.8)/.8),()=>text(g,words.dimensions,385,775,25,C.muted));
 layer(g,.15+.85*flow,()=>{
  box(g,830,310,360,450,C.panel,active?C.gold:C.line);text(g,words.select,1010,355,30,C.gold);
  box(g,885,410,250,78,C.deep,active?C.gold:C.line,13);if(active)text(g,words.selected,1010,449,31,C.gold);
  text(g,words.conditions,1010,548,23,C.muted);text(g,words.enable,1010,589,23,C.muted);
  g.strokeStyle=C.line;g.lineWidth=3;g.beginPath();g.moveTo(875,690);g.lineTo(1145,690);g.stroke();
  if(t>=10.4){const p=ease((t-10.4)/.6);g.strokeStyle=C.gold;g.lineWidth=4;g.beginPath();g.moveTo(875,690);g.lineTo(945,690);g.lineTo(945,690-33*p);g.lineTo(1005,690-33*p);g.lineTo(1005,690);g.lineTo(1145,690);g.stroke()}
  text(g,words.pulse,1010,724,24,C.gold);
 });
 layer(g,.15+.85*flow,()=>{
  box(g,1350,335,470,180,C.panel,t>=11.3?C.teal:C.line);text(g,words.controller,1585,402,34,C.teal);text(g,words.receive,1585,463,25,C.muted);
  box(g,1350,615,470,180,C.panel,t>=12.5?C.teal:C.line);text(g,words.valve,1585,674,34,C.teal);text(g,words.execute,1585,737,29);
  arrow(g,[[1585,515],[1585,615]],t>=12.5?C.teal:C.line);
 });
 layer(g,flow,()=>{
  arrow(g,[[670,520],[830,520]],C.gold);arrow(g,[[1190,520],[1270,520],[1270,425],[1350,425]],C.teal);
  if(t>=10.9&&t<11.7){const p=ease((t-10.9)/.8);const distance=p*255;let x,y;if(distance<80){x=1195+distance;y=520}else if(distance<175){x=1275;y=520-(distance-80)}else{x=1275+(distance-175);y=425}g.fillStyle=C.teal;g.beginPath();g.arc(x,y,5,0,Math.PI*2);g.fill()}
 });
 const fade=1-Math.min(ease(t/.35),ease((16-t)/.35));if(fade>0){g.globalAlpha=fade;g.fillStyle=C.bg;g.fillRect(0,0,1920,1080)}g.restore();
}
module.exports={durations,draw,visibleText};
if(require.main===module){const fs=require('node:fs'),path=require('node:path');const {createCanvas,GlobalFonts}=require('../../dataflow_film_20261028/node_modules/@napi-rs/canvas');GlobalFonts.registerFromPath('C:/Windows/Fonts/msyh.ttc','Microsoft YaHei');const c=createCanvas(1920,1080),g=c.getContext('2d');for(const t of[4,13]){draw(g,t);fs.writeFileSync(path.join(__dirname,`value_v8_${t}.png`),c.toBuffer('image/png'))}}
