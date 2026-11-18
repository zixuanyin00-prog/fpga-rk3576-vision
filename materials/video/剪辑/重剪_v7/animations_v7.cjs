const base=require('../重剪_v6/animations.cjs');
const value=require('./value_animation.cjs');
const endText=['让视觉判断，服务自动分拣','基于 FPGA 的工件识别与分类应用'];
const ease=x=>{x=Math.max(0,Math.min(1,x));return x*x*(3-2*x)};
function draw(g,t,w,h,part){
 if(part==='value')return value.draw(g,t,w,h,part);
 if(part!=='end')return base.draw(g,t,w,h,part);
 g.reset();g.save();g.scale(w/1920,h/1080);g.fillStyle='#0d1d29';g.fillRect(0,0,1920,1080);
 g.globalAlpha=Math.min(ease(t/.8),ease((6-t)/.5));g.fillStyle='#78dfcb';g.fillRect(910,290,100,3);
 g.textAlign='center';g.textBaseline='middle';g.fillStyle='#edf5f3';g.font='57px "Microsoft YaHei"';g.fillText(endText[0],960,455);
 g.fillStyle='#a5bdc7';g.font='29px "Microsoft YaHei"';g.fillText(endText[1],960,565);g.restore();
}
module.exports={durations:{value:16,collage:24,end:6},draw,visibleText:[...value.visibleText,...endText]};
