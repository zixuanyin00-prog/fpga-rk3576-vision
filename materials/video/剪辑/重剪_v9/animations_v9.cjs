'use strict';
const old=require('../重剪_v8/animations_v8.cjs');
const durations={title:4,lighttech:10,application:3,end:5};
const visibleText=['FPGA 实时视觉识别','与工件自动分拣','现场成像  ·  小样本学习  ·  识别联动','实战与应用','识别  →  选择  →  收集','基于 FPGA 的工件识别与分类应用'];
function text(g,s,x,y,size,col='#edf5f3'){g.font=`${size}px "Microsoft YaHei"`;g.fillStyle=col;g.textAlign='center';g.textBaseline='middle';g.fillText(s,x,y)}
function draw(g,t,w=1920,h=1080,name='title'){
 if(name==='lighttech')return old.draw(g,19+t*.25,w,h,'collage');
 g.reset();g.save();g.scale(w/1920,h/1080);
 g.fillStyle='#0d1d29';g.fillRect(0,0,1920,1080);
 const gradient=g.createRadialGradient(960,400,50,960,470,1050);gradient.addColorStop(0,'#203d4b');gradient.addColorStop(1,'#0d1d29');g.fillStyle=gradient;g.fillRect(0,0,1920,1080);
 g.globalAlpha=Math.min(1,t/.5,(durations[name]-t)/.5);g.fillStyle='#78dfcb';g.fillRect(910,300,100,3);
 if(name==='title'){text(g,visibleText[0],960,419,66);text(g,visibleText[1],960,514,66);text(g,visibleText[2],960,648,29,'#a5bdc7')}
 else if(name==='application'){text(g,visibleText[3],960,465,76);text(g,visibleText[4],960,603,32,'#a5bdc7')}
 else text(g,visibleText[5],960,565,29,'#a5bdc7');
 g.restore();
}
module.exports={durations,draw,visibleText};
