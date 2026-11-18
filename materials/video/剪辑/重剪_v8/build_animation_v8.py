from pathlib import Path
import json

P=Path(__file__).resolve().parent
ROOT=P.parent.parent

sources=[
 ('原生区域取图','gpio_action_20261030/candidate/rect_pipeline.py',[272,273,274,279]),
 ('原生像素输入','VisionTrainer/full_roi_runtime.py',[59,60,61,62]),
 ('位置与类别解码','VisionTrainer/full_roi_runtime.py',[67,68,69,70,71,72,73]),
 ('结果发布到显示','gpio_action_20261030/candidate/rect_pipeline.py',[601,602,603,604]),
 ('目标几何关联','VisionTrainer/native_tracking.py',[33,34,35,36,37]),
 ('连续观测积累','VisionTrainer/pose_consensus.py',[30,31,35,36,38,39]),
 ('类别一致性','VisionTrainer/pose_consensus.py',[45,46,47,48,49,50,51,52]),
 ('真实位置过线','gpio_action_20261030/candidate/action_output.py',[179,180,187,188,189,190,193]),
 ('源帧控制事件','gpio_action_20261030/candidate/rect_pipeline.py',[611,612,613,614,615,616]),
 ('延时与脉宽','gpio_action_20261030/candidate/action_output.py',[202,210,211,212,213,214]),
 ('GPIO脉冲输出','gpio_action_20261030/candidate/action_output.py',[227,228,235,236,237,238,239]),
 ('过线身份去重','gpio_action_20261030/candidate/action_output.py',[179,180,184,188,189,190,195]),
]
cards=[]
for title,path,numbers in sources:
    lines=(ROOT/path).read_text(encoding='utf-8-sig').splitlines()
    rows=[lines[n-1] for n in numbers]
    assert not any('raise ' in line or line.strip().startswith('#') for line in rows),(title,rows)
    cards.append(dict(title=title,source_path=path,source_lines=numbers,lines=rows))
(P/'code_sources_v8.json').write_text(json.dumps(cards,ensure_ascii=False,indent=2),encoding='utf8')

s=(P.parent/'重剪_v6/animations.cjs').read_text(encoding='utf8')
s=s.replace("require('./code_sources_v6.json')","require('./code_sources_v8.json')")
s=s.replace("const cards =", "const value=require('./value_v8.cjs');\nconst cards =",1)
s=s.replace('coarsefine:14','inference:14,value:16')
s=s.replace('双向调参  ·  两阶段推理  ·  连续确认','双向调参  ·  原生区域推理  ·  连续确认')
s=s.replace("end:['从看见工件，到确认姿态','FPGA 实时视觉处理与工件姿态识别']","end:['基于 FPGA 的工件识别与分类应用']")
s=s.replace("coarsefine:['先找到工件，再判断姿态','粗定位','全图定位工件','原生 RGB 裁剪','ROI','细推理','姿态判断','正反面','数字方向','473']", "inference:['原生图像定位与分类','原生 RGB 区域','区域像素与坐标','NPU 区域推理','结果解码','识别结果','位置框','任务类别','一次区域推理','473']")
s=s.replace("tracking:['同一工件，连续确认，一次触发','观测一','观测二','观测三','ID 07','跨帧关联','姿态确认','外接控制','连续观测','生成控制事件','已处理','继续关联','473']", "tracking:['确认类别，真实过线后触发','观测一','观测二','过线观测','ID 07','连续观测确认','中心触发线','延时与脉宽','位置关联','目标类别确认','过线已记录','同一身份继续关联','473','类别已确认','延时','脉宽','同 ID 过线去重']")
s=s.replace("'粗定位','姿态识别','连续确认'", "'区域推理','位置与类别','过线控制'")
s=s.replace('T.coarsefine[9]','T.inference[9]')
s=s.replace("'定位与姿态识别'","'定位与分类'")
s=s.replace("const target=i<2?[625,500]:i===10?[960,810]:[1205,500]", "const target=[960,500]")
s=s.replace("const visibleText=[...new Set([...Object.values(T).flat(),...cards.flatMap(c=>[c.title,...c.lines])])];", "const visibleText=[...new Set([...Object.values(T).flat(),...value.visibleText,'A','B','C',...cards.flatMap(c=>[c.title,...c.lines])])];")

start=s.index('function coarsefine(g,t){')
end=s.index('function async(g,t){',start)
s=s[:start]+r'''function inference(g,t){
 const q=T.inference;text(g,q[0],960,116,48);
 box(g,90,282,550,444);text(g,q[1],365,234,33,C.teal);
 g.fillStyle=C.deep;g.fillRect(112,306,506,390);g.strokeStyle='#44616e';g.lineWidth=5;
 for(const yy of [410,592]){g.beginPath();g.moveTo(112,yy);g.lineTo(618,yy);g.stroke()}
 part(g,225,459,-.14,.68);part(g,430,529,.13,.82);part(g,555,441,Math.PI,.56);
 text(g,q[2],365,777,27,C.muted);
 appear(g,t,4.7,()=>{
  wire(g,[[640,500],[770,500]],t,C.gold);box(g,770,358,370,288,C.panel,C.gold);
  text(g,q[3],955,418,32,C.gold);text(g,q[8],955,485,26,C.muted);
  g.fillStyle=C.line;g.fillRect(812,538,286,1);text(g,q[4],955,591,31);
 });
 appear(g,t,8.7,()=>{
  wire(g,[[1140,500],[1280,500]],t,C.teal);box(g,1280,282,550,444);
  text(g,q[5],1555,332,34,C.teal);
  box(g,1325,379,460,123,C.deep,C.line,13);text(g,q[6],1430,440,31);
  g.strokeStyle=C.teal;g.lineWidth=2.5;g.strokeRect(1550,413,165,58);
  box(g,1325,534,460,123,C.deep,C.line,13);text(g,q[7],1430,596,31);
  for(let i=0;i<3;i++){box(g,1542+i*65,572,56,48,C.deep,i===1?C.teal:C.line,9);text(g,['A','B','C'][i],1570+i*65,596,24,i===1?C.teal:C.muted)}
 });
}
''' +s[end:]

start=s.index('function tracking(g,t){')
end=s.index('function collage(g,t){',start)
s=s[:start]+r'''function tracking(g,t){
 const q=T.tracking;text(g,q[0],960,116,47);
 const ats=[.6,5,10],positions=[150,220,330];
 ats.forEach((at,i)=>appear(g,t,at,()=>{
  const x=110+i*590;box(g,x,279,520,327);text(g,q[i+1],x+260,326,30,C.muted);
  g.save();g.setLineDash([8,7]);g.strokeStyle=C.gold;g.lineWidth=2;g.beginPath();g.moveTo(x+260,365);g.lineTo(x+260,505);g.stroke();g.restore();
  part(g,x+positions[i],435,.08,.8);g.strokeStyle=C.teal;g.lineWidth=2;g.strokeRect(x+positions[i]-64,390,128,89);
  box(g,x+178,521,164,51,C.deep,C.teal,25);text(g,q[4],x+260,547,27,C.teal);
  if(i<2&&t>=ats[i+1])wire(g,[[x+520,435],[x+590,435]],t,C.teal,false);
 }));
 appear(g,t,10.5,()=>{
  box(g,110,684,500,150,C.deep,C.teal);text(g,q[9],360,727,31,C.teal);tick(g,360,785,C.teal,1.1);
 });
 appear(g,t,13,()=>{
  wire(g,[[610,759],[710,759]],t,C.teal,false);box(g,710,684,500,150,C.deep,C.teal);
  text(g,q[6],960,727,31);text(g,q[3],960,786,26,C.gold);
 });
 appear(g,t,15.5,()=>{
  wire(g,[[1210,759],[1310,759]],t,C.gold,false);box(g,1310,684,500,150,C.deep,C.gold);
  text(g,q[7],1560,727,31,C.gold);g.strokeStyle=C.line;g.lineWidth=3;g.beginPath();g.moveTo(1355,794);g.lineTo(1765,794);g.stroke();
  if(t>=17){
   const p=ease((t-17)/.6);g.strokeStyle=C.gold;g.lineWidth=4;g.beginPath();g.moveTo(1355,794);g.lineTo(1460,794);g.lineTo(1460,794-35*p);g.lineTo(1535,794-35*p);g.lineTo(1535,794);g.lineTo(1765,794);g.stroke();
  }
 });
 appear(g,t,18,()=>{text(g,q[10],960,880,27,C.teal);tick(g,840,880,C.teal,.7)});
 appear(g,t,22,()=>{text(g,q[16],1555,880,26,C.muted)});
}
''' +s[end:]

start=s.index('function draw(g,t,w=1920,h=1080,partName=')
s=s[:start]+r'''function endcard(g,t){
 layer(g,Math.min(ease(t/.8),ease((6-t)/.5)),()=>{
  g.fillStyle=C.teal;g.fillRect(910,290,100,3);text(g,T.end[0],960,565,29,C.muted);
 });
}
function draw(g,t,w=1920,h=1080,partName='parameter'){
 if(partName==='value')return value.draw(g,t,w,h,'value');
 g.reset();g.save();g.scale(w/1920,h/1080);bg(g);g.save();
 if(partName==='title')title(g,t,false);
 else if(partName==='end')endcard(g,t);
 else({parameter,inference,async,tracking,collage}[partName])(g,t);
 g.restore();
 const shade=g.createLinearGradient(0,880,0,930);shade.addColorStop(0,'#0d1d2900');shade.addColorStop(1,C.bg);g.fillStyle=shade;g.fillRect(0,880,1920,50);g.fillStyle=C.bg;g.fillRect(0,930,1920,150);
 const fade=1-Math.min(ease(t/.35),ease((durations[partName]-t)/.35));if(fade>0){g.globalAlpha=fade;g.fillStyle=C.bg;g.fillRect(0,0,1920,1080)}g.restore();
}
module.exports={durations,draw,visibleText};
if(require.main===module){
 const fs=require('node:fs'),path=require('node:path');
 const {GlobalFonts}=require('../../dataflow_film_20261028/node_modules/@napi-rs/canvas');
 GlobalFonts.registerFromPath('C:/Windows/Fonts/msyh.ttc','Microsoft YaHei');
 const c=createCanvas(1920,1080),g=c.getContext('2d');
 for(const [part,t] of [['title',2],['inference',3],['inference',11],['tracking',12],['tracking',17.5],['tracking',24],['collage',6],['collage',20],['end',3]]){
  draw(g,t,1920,1080,part);fs.writeFileSync(path.join(__dirname,`animation_v8_${part}_${t}.png`),c.toBuffer('image/png'));
 }
 fs.writeFileSync(path.join(__dirname,'animation_visible_text_v8.json'),JSON.stringify(visibleText,null,2));
}
'''
assert 'coarsefine' not in s and 'refiner' not in s and '两阶段' not in s
(P/'animations_v8.cjs').write_text(s,encoding='utf8')
print('animation sources written')
