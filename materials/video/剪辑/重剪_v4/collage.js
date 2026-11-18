const data=require('./code_sources.json');
const old=require('./highlights.js');
const ease=x=>{x=Math.min(1,Math.max(0,x));return x*x*(3-2*x)};
function text(g,s,x,y,z=28,col='#eaf4f0'){g.font=`${z}px "Microsoft YaHei"`;g.fillStyle=col;g.textAlign='center';g.fillText(s,x,y)}
function draw(g,t,w,h,part){if(part==='auto'){g.save();g.scale(w/1920,h/1080);g.fillStyle='#11202a';g.fillRect(0,0,1920,1080);text(g,'少量采样，一键生成候选框',960,115,46);const names=['采集样本','自动生成候选框','人工确认姿态'];for(let i=0;i<3;i++){const x=110+i*590;g.fillStyle='#1a333f';g.beginPath();g.roundRect(x,295,520,390,20);g.fill();text(g,names[i],x+260,355,30);for(let k=0;k<3;k++){const px=x+100+k*150;g.fillStyle='#bbcaca';g.fillRect(px-40,440,80,45);text(g,'473',px,468,17,'#18333d');if(i>0&&t>4+i*3){g.strokeStyle='#71dbce';g.lineWidth=2;g.strokeRect(px-53,424,106,77)}}if(i===2&&t>12)text(g,'确认类别，进入训练',x+260,595,27,'#71dbce')}text(g,t<10?'自动生成目标候选框，减少重复手动画框。':'人工检查候选框和姿态类别，再开始训练。',960,960,34);g.restore();return;}if(part!=='collage')return old.draw(g,t,w,h,part);g.save();g.scale(w/1920,h/1080);g.fillStyle='#11202a';g.fillRect(0,0,1920,1080);
 const pull=ease((t-3)/7),merge=ease((t-12)/5),zoom=2.3-1.5*pull;
 g.save();g.translate(960,475);g.scale(zoom,zoom);g.globalAlpha=1-merge;
 for(let i=0;i<15;i++){
  const d=data[i%data.length],x=(i%5-2)*365-170,y=(Math.floor(i/5)-1)*270-120;
  g.fillStyle='#1a303c';g.fillRect(x,y,340,245);g.strokeStyle='#4c7d89';g.strokeRect(x,y,340,245);
  g.textAlign='left';g.font='14px Consolas';g.fillStyle='#7ee0d0';g.fillText(d.name,x+16,y+24);
  g.save();g.beginPath();g.rect(x+12,y+36,316,196);g.clip();g.font='10px Consolas';
  d.lines.slice(0,21).forEach((s,j)=>{g.fillStyle=/^(module|class|def|import|from)|always|assign/.test(s.trim())?'#eac999':'#b8ccd3';g.fillText(s,x+16,y+48+j*9)});g.restore();
 }
 g.restore();
 g.fillStyle='#11202a';g.fillRect(0,0,1920,190);g.fillRect(0,918,1920,162);if(t<15)text(g,'从底层图像处理，到现场智能应用',960,100,42);
 g.save();g.globalAlpha=merge;
 text(g,'让图像，成为可执行的判断',960,160,48);
 const titles=['FPGA','RK','现场应用'],details=['图像采集与预处理','定位 · 姿态 · 连续确认','显示 · 调参 · 外接控制'];
 for(let i=0;i<3;i++){const x=160+i*570;g.fillStyle='#1a333f';g.strokeStyle='#70d9cb';g.lineWidth=2;g.beginPath();g.roundRect(x,355,460,260,22);g.fill();g.stroke();text(g,titles[i],x+230,445,48,'#70d9cb');text(g,details[i],x+230,525,26);if(i<2){g.strokeStyle='#70d9cb';g.beginPath();g.moveTo(x+460,485);g.lineTo(x+570,485);g.stroke();g.fillStyle='#70d9cb';g.beginPath();g.moveTo(x+570,485);g.lineTo(x+553,476);g.lineTo(x+553,494);g.fill()}}
 text(g,'采集与处理   →   识别与确认   →   显示与执行',960,735,30,'#b4cdd5');g.restore();
 const sub=t<12?['从图像采集、处理到识别与控制，','各个模块共同支撑现场应用。']:['FPGA 负责图像采集与预处理，','RK 完成识别、显示和外接控制。'];
 sub.forEach((s,i)=>text(g,s,960,960+i*47,34));g.restore();}
module.exports={durations:{...old.durations,collage:28,auto:20},draw};

