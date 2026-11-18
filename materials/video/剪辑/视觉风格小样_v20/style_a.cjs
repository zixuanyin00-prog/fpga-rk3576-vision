'use strict';

// Procedural typography and line-work only. 1920 x 1080, 16 s / 60 fps.
const P={paper:'#f3f0e7',ink:'#111210',quiet:'#898b82',accent:'#e34a2c'};
const sat=x=>Math.max(0,Math.min(1,x));
const ease=x=>{x=sat(x);return 1-Math.pow(1-x,4);};
const smooth=x=>{x=sat(x);return x*x*(3-2*x);};

function draw(g,t){
  g.reset();g.fillStyle=P.ink;g.fillRect(0,0,1920,1080);
  g.textBaseline='alphabetic';g.textAlign='left';

  // In the second scene the typography establishes a fixed architecture;
  // only the measured object and identity attachment travel through it.
  if(t>=3){
    const enter=ease((t-3.15)/.75);
    g.save();g.globalAlpha=enter;
    g.fillStyle=P.paper;g.font='92px "CJK Heavy"';
    g.fillText('同一工件，连续确认',100,168);
    g.font='136px "DIN"';g.textAlign='right';g.fillText('03',1818,168);
    g.textAlign='left';
    g.fillRect(100,229,1720,3);
    const rail=ease((t-3.1)/.9);
    g.fillRect(100,493,1720*rail,3);g.fillRect(100,625,1720*rail,3);
    // The precise ruler is geometry, with no implied new measurements.
    for(let k=0;k<42;k++){
      const x=100+k*42;
      if(x>100+1720*rail)break;
      g.fillStyle=k%5===0?P.paper:P.quiet;
      g.fillRect(x,648,2,k%5===0?19:9);
    }
    const ats=[5.2,7.4,9.6],xs=ats.map(at=>240+170*(at-4));
    for(let i=0;i<3;i++){
      const p=ease((t-ats[i])/.4);if(p<=0)continue;
      const x=xs[i],pulse=1-sat((t-ats[i])/.55);
      g.fillStyle=P.paper;g.font='46px "CJK Heavy"';
      g.save();g.beginPath();g.rect(x-180,323,360,67*p);g.clip();
      g.textAlign='center';g.fillText('真实观测 '+(i+1),x,379+20*(1-p));g.restore();
      g.textAlign='left';g.fillStyle=pulse>0?P.accent:P.paper;
      g.fillRect(x-1,402,2,70*p);
      g.fillRect(x-8,476,16,7);
      g.fillRect(x-1,632,2,35*p);
      if(pulse>0){
        g.save();g.globalAlpha=pulse*.5;g.fillStyle=P.accent;
        g.fillRect(x-30,498,60,127);g.restore();
      }
    }

    const x=240+170*Math.max(0,Math.min(8.1,t-4));
    g.save();g.translate(x,560);
    // A single rectangular resistor travels; no duplicated proxy frames.
    g.fillStyle=P.paper;g.fillRect(-114,-3,228,6);
    g.fillRect(-64,-37,128,74);
    g.fillStyle=P.ink;g.fillRect(-39,-37,11,74);g.fillRect(-10,-37,11,74);g.fillRect(29,-37,6,74);
    g.strokeStyle=P.paper;g.lineWidth=3;
    const brackets=[[-91,-58,1,1],[91,-58,-1,1],[-91,58,1,-1],[91,58,-1,-1]];
    for(const [bx,by,sx,sy]of brackets){g.beginPath();g.moveTo(bx+sx*23,by);g.lineTo(bx,by);g.lineTo(bx,by+sy*16);g.stroke();}
    g.fillStyle=P.paper;g.fillRect(-1,60,2,41);g.fillRect(-83,101,166,56);
    g.fillStyle=P.ink;g.font='38px "DIN"';g.textAlign='center';g.fillText('ID 07',0,141);
    g.restore();g.textAlign='left';

    // The old sentence physically exits in slices before the confirmation lands.
    const confirm=sat((t-11.7)/.48);
    g.font='86px "CJK Heavy"';
    for(let strip=0;strip<5;strip++){
      const y=756+strip*24;
      g.save();g.beginPath();g.rect(100,y,1450,24);g.clip();
      if(confirm<1){g.fillStyle=P.paper;g.fillText('连续观测积累',100-1000*ease((confirm-strip*.045)/.8),847);}
      if(confirm>0){g.fillStyle=P.paper;g.fillText('类别已确认',100+1150*(1-ease((confirm-strip*.045)/.8)),847);}
      g.restore();
    }
    if(t>=12.18){g.fillStyle=P.accent;g.fillRect(100,867,430,9);}
    g.fillStyle=P.paper;g.font='37px "CJK"';g.fillText('同一身份 · 连续真实观测',100,946);
    g.restore();
  }

  // The chapter is one typographic composition. Its horizontal slices then
  // sweep aside to expose the already drawn tracking scene, without a black gap.
  if(t<4){
    const q=sat(t-3);
    for(let section=0;section<6;section++){
      const top=section*164,height=section===5?260:164;
      const push=t<3?0:1920*ease((q-section*.055)/.73);
      if(push>=1920)continue;
      g.save();g.beginPath();g.rect(-push,top,1920,height);g.clip();
      g.translate(-push,0);
      g.fillStyle=P.paper;g.fillRect(0,0,1920,1080);
      // A small number of hard rules controls the entire poster.
      g.fillStyle=P.ink;g.fillRect(100,103,1720,5);g.fillRect(1218,108,5,696);
      const lock=ease((t-.12)/.72);
      // Six fine masks within each line give a controlled, finite sliced entry.
      const lines=[['识别与',132,405,224,0],['工业检测',132,675,224,.09]];
      for(const [str,x,y,size,delay]of lines){
        for(let row=0;row<6;row++){
          const p=ease((t-delay-row*.035)/.66);
          g.save();g.beginPath();g.rect(100,y-size+row*40,1090,40);g.clip();
          g.translate(x+(1-p)*(row%2?370:-370),y);
          g.scale(.62+.38*p,1);g.fillStyle=P.ink;g.font=size+'px "CJK Heavy"';g.fillText(str,0,0);g.restore();
        }
      }
      g.save();g.beginPath();g.rect(1245,112,575,686);g.clip();
      g.translate(1260,672);g.scale(.52+.26*lock,1);
      g.fillStyle=P.ink;g.font='650px "Impact"';g.fillText('03',0,150*(1-lock));g.restore();
      g.fillStyle=P.ink;g.fillRect(100,803,1720,6);
      g.font='53px "CJK"';g.fillText('识别 · 测量 · 告警',132,922);
      // Marks live inside the typographic grid and have no UI/card meaning.
      g.fillRect(1658,867,162,55);g.fillStyle=P.paper;g.fillRect(1674,883,130*lock,23);
      g.restore();
    }
  }
}

exports.draw=draw;
exports.meta={
  title:'动势字场',
  palette:[P.paper,P.ink,P.accent],
  motion:'大字压缩展开；横向字形切片入场；六条画幅切片从章节擦入轨道；同一电阻连续运动，ID 随体保持；确认文字分层置换后稳定停留。',
  sound_events:[
    {time:0.12,type:'low_typographic_hit',gain:0.26},
    {time:0.66,type:'short_dry_lock',gain:0.2},
    {time:3.08,type:'paper_slice_whoosh',duration:0.72,gain:0.22},
    {time:5.2,type:'dry_observation_tick',gain:0.2},
    {time:7.4,type:'dry_observation_tick',gain:0.2},
    {time:9.6,type:'dry_observation_tick',gain:0.2},
    {time:11.7,type:'soft_low_confirm',duration:0.36,gain:0.25}
  ],
  visible_text:['03','识别与工业检测','识别 · 测量 · 告警','同一工件，连续确认','真实观测 1','真实观测 2','真实观测 3','ID 07','连续观测积累','类别已确认','同一身份 · 连续真实观测'],
  reference_urls:['https://www.brandnewschool.com/work/johnnie-walker-keep-walking','https://studiodumbar.com/work/demo-2022']
};
