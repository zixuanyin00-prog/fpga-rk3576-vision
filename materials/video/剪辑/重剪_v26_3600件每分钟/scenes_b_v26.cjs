'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const original=require('../重剪_v21/scenes_b.cjs');
const index=original.visibleText.end.indexOf('实测1200工件/min');
if(index<0)throw Error('Expected original speed label not found');
original.visibleText.end[index]='实测3600工件/min';
module.exports={...original,writeManifest(){
  const sourceFiles=['scenes_b_v26.cjs','../重剪_v21/scenes_b.cjs','../重剪_v21/geometry_b.cjs'];
  const data={scene:'end',start:174.8,duration:5,fps:60,frames:300,visible_text:original.visibleText.end,change:'Speed updated to 3600 workpieces/min as requested by user; no new hardware measurement.',sources:sourceFiles.map(p=>({file:path.resolve(__dirname,p),sha256:crypto.createHash('sha256').update(fs.readFileSync(path.resolve(__dirname,p))).digest('hex')}))};
  fs.writeFileSync(path.join(__dirname,'visible_manifest.json'),JSON.stringify(data,null,2));return data;
}};
