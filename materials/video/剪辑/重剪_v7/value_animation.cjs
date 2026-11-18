'use strict';

const durations={value:16};
const C={bg:'#0d1d29',panel:'#17313f',deep:'#102632',line:'#355768',ink:'#edf5f3',muted:'#a5bdc7',teal:'#78dfcb',gold:'#efcf8a'};
const words={
 title:'从识别类别，到自动分拣',
 input:'识别类别',controller:'外部控制器',output:'分类出口',
 dimensions:'型号  ·  正反面  ·  数字方向',mapping:'类别与动作映射',
 models:['型号 A','型号 B','型号 C'],classes:['类别 A','类别 B','类别 C'],
 outlets:['出口 A','出口 B','出口 C'],codes:['103','473','102']
};
const visibleText=[...new Set(Object.values(words).flat())];
const ease=x=>{x=Math.max(0,Math.min(1,x));return x*x*(3-2*x)};
const mix=(a,b,x)=>a+(b-a)*x;

function text(g,s,x,y,size=30,color=C.ink,align='center'){
 g.font=`${size}px "Microsoft YaHei",sans-serif`;g.textAlign=align;g.textBaseline='middle';g.fillStyle=color;g.fillText(s,x,y);
}
function box(g,x,y,w,h,color=C.panel,line=C.line,r=18){
 g.beginPath();g.roundRect(x,y,w,h,r);g.fillStyle=color;g.fill();
 if(line){g.strokeStyle=line;g.lineWidth=1.5;g.stroke()}
}
function layer(g,a,draw){g.save();g.globalAlpha*=Math.max(0,Math.min(1,a));draw();g.restore()}
function arrow(g,points,color=C.line){
 g.strokeStyle=color;g.lineWidth=2.5;g.beginPath();points.forEach((p,i)=>i?g.lineTo(...p):g.moveTo(...p));g.stroke();
 const p=points.at(-1),q=points.at(-2),a=Math.atan2(p[1]-q[1],p[0]-q[0]);
 g.beginPath();g.moveTo(...p);g.lineTo(p[0]-12*Math.cos(a-.4),p[1]-12*Math.sin(a-.4));g.lineTo(p[0]-12*Math.cos(a+.4),p[1]-12*Math.sin(a+.4));g.closePath();g.fillStyle=color;g.fill();
}
function resistor(g,x,y,code){
 g.save();g.translate(x,y);box(g,-65,-31,130,62,'#a4b6b7','#d5e5df',6);
 g.fillStyle='#e2ebe7';g.fillRect(-67,-25,17,50);g.fillRect(50,-25,17,50);
 text(g,code,0,1,26,'#243c43');g.restore();
}

function draw(g,t,w=1920,h=1080,part='value'){
 g.reset();g.save();g.scale(w/1920,h/1080);
 g.fillStyle=C.bg;g.fillRect(0,0,1920,1080);
 const bg=g.createRadialGradient(950,430,40,950,500,1100);bg.addColorStop(0,'#203d4b');bg.addColorStop(1,C.bg);g.fillStyle=bg;g.fillRect(0,0,1920,1080);
 text(g,words.title,960,116,48);

 const reveal=ease((t-.6)/1.1),flow=ease((t-6.8)/1.0);
 const phase=t<10.1?0:t<12.65?1:2;
 const phaseStart=[7.3,10.1,12.65][phase];
 const progress=ease((t-phaseStart)/1.7);
 const active=t>=7.3;
 const rowY=[370,520,670];

 box(g,100,255,570,560);
 text(g,words.input,385,297,34,C.teal);
 for(let i=0;i<3;i++)layer(g,reveal*ease((t-.8-i*.4)/.8),()=>{
  const selected=active&&phase===i;
  box(g,139,rowY[i]-53,492,106,C.deep,selected?C.teal:C.line,13);
  resistor(g,255,rowY[i],words.codes[i]);
  text(g,words.models[i],470,rowY[i],30,selected?C.teal:C.ink);
 });
 layer(g,ease((t-2.8)/.8),()=>text(g,words.dimensions,385,775,25,C.muted));

 layer(g,.25+.75*flow,()=>{
  box(g,830,370,360,300,C.panel,flow>.8?C.gold:C.line);
  text(g,words.controller,1010,418,34,C.gold);
  text(g,words.mapping,1010,473,25,C.muted);
  box(g,885,529,250,83,C.deep,flow>.8?C.gold:C.line,12);
  if(active&&progress>.42)text(g,words.classes[phase],1010,570,31,C.gold);
 });

 layer(g,.25+.75*flow,()=>{
  box(g,1350,255,470,560);
  text(g,words.output,1585,297,34,C.teal);
  for(let i=0;i<3;i++){
   const selected=active&&phase===i&&progress>.83;
   box(g,1390,rowY[i]-53,390,106,C.deep,selected?C.teal:C.line,13);
   text(g,words.outlets[i],1520,rowY[i],30,selected?C.teal:C.ink);
   if(selected)resistor(g,1705,rowY[i],words.codes[i]);
  }
 });

 layer(g,flow,()=>{
  arrow(g,[[670,520],[830,520]],C.gold);
  arrow(g,[[1190,520],[1270,520],[1270,rowY[phase]],[1350,rowY[phase]]],C.teal);
  if(active&&progress<.96){
   let x,y;
   if(progress<.42){x=mix(683,817,progress/.42);y=520}
   else if(progress<.7){x=mix(1202,1270,(progress-.42)/.28);y=520}
   else if(progress<.84){x=1270;y=mix(520,rowY[phase],(progress-.7)/.14)}
   else{x=mix(1270,1337,(progress-.84)/.12);y=rowY[phase]}
   g.fillStyle=progress<.42?C.gold:C.teal;g.beginPath();g.arc(x,y,5,0,Math.PI*2);g.fill();
  }
 });

 const fade=1-Math.min(ease(t/.35),ease((durations.value-t)/.35));
 if(fade>0){g.globalAlpha=fade;g.fillStyle=C.bg;g.fillRect(0,0,1920,1080)}
 g.restore();
}

module.exports={durations,draw,visibleText};

if(require.main===module){
 const fs=require('node:fs'),path=require('node:path');
 const {createCanvas,GlobalFonts}=require('../../dataflow_film_20261028/node_modules/@napi-rs/canvas');
 GlobalFonts.registerFromPath('C:/Windows/Fonts/msyh.ttc','Microsoft YaHei');
 const c=createCanvas(1920,1080),g=c.getContext('2d');
 for(const t of [4,9.4,14.7]){draw(g,t);fs.writeFileSync(path.join(__dirname,`value_${t}.png`),c.toBuffer('image/png'))}
 fs.writeFileSync(path.join(__dirname,'value_visible_text.json'),JSON.stringify(visibleText,null,2));
}
