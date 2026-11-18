'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const original=require('../重剪_v21/scenes_b.cjs');
const labels=['作品名称','企业赛道：安路赛道','选题类型：自由选题','板卡使用：平台三'];
function draw(g,name,t,duration){
  original.draw(g,name,t,duration);
  if(name!=='opening_title')throw Error('This local revision only renders the opening title');
  const progress=Math.min(1,Math.max(0,t/.18));
  g.save();g.globalAlpha=progress;g.textAlign='left';g.textBaseline='middle';
  g.font='500 29px "CJK",sans-serif';g.fillStyle='#287b38';g.fillText(labels[0],124,278);
  g.fillStyle='#346e9f';g.fillRect(124,766,45,3);
  g.font='500 31px "CJK",sans-serif';g.fillStyle='#183747';
  labels.slice(1).forEach((line,i)=>g.fillText(line,124,806+i*45));
  g.restore();return g.canvas;
}
module.exports={init:original.init,draw,durations:{opening_title:2},visibleText:{opening_title:[...original.visibleText.opening_title,...labels]},writeManifest(){
  const sources=['scenes_b_v27.cjs','../重剪_v21/scenes_b.cjs','../重剪_v21/geometry_b.cjs'];
  const data={scene:'opening_title',duration:2,fps:60,frames:120,work_name:'FPGA 实时视觉识别与工件自动分拣',competition_track:'企业赛道：安路赛道',topic:'自由选题',board_platform:'平台三',visible_text:[...original.visibleText.opening_title,...labels],sources:sources.map(p=>({file:path.resolve(__dirname,p),sha256:crypto.createHash('sha256').update(fs.readFileSync(path.resolve(__dirname,p))).digest('hex')}))};
  fs.writeFileSync(path.join(__dirname,'visible_manifest.json'),JSON.stringify(data,null,2));return data;
}};
