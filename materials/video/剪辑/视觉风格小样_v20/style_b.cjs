'use strict';

// All visible imagery is constructed here as sorted, lit Canvas polygons.
// The same three curved sheets unfold into one continuous physical track.
const C={bg:'#cbdde9',ink:'#183747',muted:'#587482',green:'#39b83f',blue:'#346e9f',silver:'#dce8e9',edge:'#aac1cb'};
const clamp=x=>Math.max(0,Math.min(1,x));
const ease=x=>{x=clamp(x);return x*x*x*(10+x*(-15+6*x));};
const mix=(a,b,p)=>a+(b-a)*p;
const rad=x=>x*Math.PI/180;
const obsAt=[5.2,7.4,9.6],obsU=[.20,.50,.80];

function text(g,s,x,y,size,color=C.ink,font='CJK',weight=500,align='left'){
  g.font=`${weight} ${size}px "${font}"`;g.textAlign=align;g.textBaseline='middle';g.fillStyle=color;g.fillText(s,x,y);
}

function project(v,c){
  const x=v[0]*Math.cos(c.yaw)+v[2]*Math.sin(c.yaw),z=-v[0]*Math.sin(c.yaw)+v[2]*Math.cos(c.yaw);
  return [c.x+x*c.scale,c.y+(-v[1]*.925+z*.38)*c.scale,v[1]*.38+z*.925];
}

function shaded(hex,light){
  const n=parseInt(hex.slice(1),16),v=[n>>16,(n>>8)&255,n&255];
  return '#'+v.map(x=>Math.round(light>1?mix(x,255,Math.min(.87,(light-1)*.9)):x*Math.max(.25,light)).toString(16).padStart(2,'0')).join('');
}

function face(faces,vs,base,alpha=1,metal=false){
  const a=vs[0],b=vs[1],d=vs[2],u=b.map((x,i)=>x-a[i]),v=d.map((x,i)=>x-a[i]);
  let n=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]],len=Math.hypot(...n)||1;n=n.map(x=>-x/len);
  // Diffuse studio light plus a narrow material highlight; no emissive/glow pass.
  const diffuse=Math.max(0,n[0]*-.34+n[1]*.83+n[2]*.44);
  const gloss=Math.pow(Math.max(0,n[0]*.80+n[1]*.10+n[2]*.59),metal?28:38);
  faces.push({vs,color:shaded(base,.58+.57*diffuse+(metal?.64:.26)*gloss),alpha});
}

function paint(g,faces,c){
  const p=faces.map(f=>({...f,ps:f.vs.map(v=>project(v,c))}));
  p.sort((a,b)=>a.ps.reduce((s,v)=>s+v[2],0)/a.ps.length-b.ps.reduce((s,v)=>s+v[2],0)/b.ps.length);
  for(const f of p){g.globalAlpha=f.alpha;g.beginPath();f.ps.forEach((p,i)=>i?g.lineTo(p[0],p[1]):g.moveTo(p[0],p[1]));g.closePath();g.fillStyle=f.color;g.fill();g.lineWidth=1.35;g.strokeStyle=f.color;g.stroke();}
  g.globalAlpha=1;
}

function sheet(faces,s){
  // A real-thickness annular strip with a cross section that rotates from wall to rail.
  const steps=58,point=(a,v,side)=>{
    const r=s.r+v*s.w*Math.cos(s.beta),n=[-Math.sin(a)*Math.sin(s.beta),Math.cos(s.beta),-Math.cos(a)*Math.sin(s.beta)];
    return [s.x+r*Math.sin(a)+side*n[0]*s.thick/2,s.y+v*s.w*Math.sin(s.beta)+side*n[1]*s.thick/2,s.z+r*Math.cos(a)+side*n[2]*s.thick/2];
  };
  for(let i=0;i<steps;i++){
    const a=mix(s.a,s.b,i/steps),b=mix(s.a,s.b,(i+1)/steps);
    face(faces,[point(a,-.5,1),point(b,-.5,1),point(b,.5,1),point(a,.5,1)],s.base,1,s.metal);
    face(faces,[point(a,.5,-1),point(b,.5,-1),point(b,-.5,-1),point(a,-.5,-1)],s.base,1,s.metal);
    for(const v of [-.5,.5])face(faces,[point(a,v,-1),point(b,v,-1),point(b,v,1),point(a,v,1)],s.edge||C.silver,1,true);
  }
  for(const a of [s.a,s.b])face(faces,[point(a,-.5,-1),point(a,.5,-1),point(a,.5,1),point(a,-.5,1)],s.edge||C.silver,1,true);
}

function cuboid(faces,center,size,angle,base,metal=false){
  const p=(x,y,z)=>[center[0]+x*Math.cos(angle)+z*Math.sin(angle),center[1]+y,center[2]-x*Math.sin(angle)+z*Math.cos(angle)];
  const [x,y,z]=size.map(a=>a/2),v=[p(-x,-y,-z),p(x,-y,-z),p(x,y,-z),p(-x,y,-z),p(-x,-y,z),p(x,-y,z),p(x,y,z),p(-x,y,z)];
  for(const q of [[0,1,2,3],[5,4,7,6],[4,0,3,7],[1,5,6,2],[3,2,6,7],[4,5,1,0]])face(faces,q.map(i=>v[i]),base,1,metal);
}

function at(u,y=42,radial=0){const a=rad(-57+114*u),r=930+radial;return [r*Math.sin(a),y,-700+r*Math.cos(a),a];}

function shadow(g,c,unfold){
  g.save();g.lineCap='round';
  if(unfold<.96){
    const p=project([mix(410,0,unfold),-115,mix(-80,40,unfold)],c);
    g.translate(p[0]+14,p[1]+42);g.scale(1,.22);g.fillStyle=`rgba(38,72,84,${.08*(1-unfold)})`;g.shadowColor='rgba(38,72,84,.20)';g.shadowBlur=65;g.beginPath();g.ellipse(0,0,350,135,0,0,Math.PI*2);g.fill();
  }
  g.restore();
  if(unfold>.02){g.save();g.globalAlpha=unfold;g.filter='blur(22px)';g.beginPath();for(let i=0;i<=100;i++){const q=at(i/100,-16);const p=project(q,c);i?g.lineTo(p[0]+8,p[1]+8):g.moveTo(p[0]+8,p[1]+8);}g.strokeStyle='rgba(40,73,87,.13)';g.lineWidth=92*c.scale;g.stroke();g.restore();}
}

function sculpture(g,t,unfold,c){
  const faces=[],introA=[-160,-35,91],introB=[-66,59,181],colors=[C.silver,C.green,C.blue];
  for(let i=0;i<3;i++){
    // Intro gaps compress smoothly; no hidden cut changes the apparatus identity.
    const introShift=ease((t-.08-i*.12)/1.4);
    sheet(faces,{
      x:mix(410+(i-1)*34*(1-introShift),0,unfold),y:mix(100+(i-1)*75+70*(1-introShift),42,unfold),
      z:mix(-100+(i-1)*36,-700,unfold),r:mix(330+(i-1)*23,930,unfold),
      a:mix(rad(introA[i]+7*(1-introShift)),rad(-57+38*i),unfold),b:mix(rad(introB[i]+7*(1-introShift)),rad(-57+38*(i+1)),unfold),
      w:mix(345,156,unfold),beta:mix(Math.PI/2,0,unfold),thick:mix(10,17,unfold),
      base:unfold<.001?colors[i]:shaded(colors[i],1+unfold*.85),edge:i===1?'#a8df89':'#e9f1f2',metal:i!==1
    });
  }
  paint(g,faces,c);
}

function tracking(g,t,c){
  const appear=ease((t-4)/.62),faces=[];
  // Three open cross sections belong to the one rail. They are sampling locations,
  // not model boxes, duplicate objects, or projected future object positions.
  for(let i=0;i<3;i++){
    const p=at(obsU[i]),h=112*appear,a=p[3]+(p[3]<-.1?-.50:.50),active=t>=obsAt[i],base=active?'#8bcf78':'#c8d9de';
    for(const r of [-110,110]){const q=[p[0]+r*Math.sin(a),42+h/2,p[2]+r*Math.cos(a)];cuboid(faces,q,[13,h,12],a,base,true);}
    const q=[p[0],42+h,p[2]];cuboid(faces,q,[13,13,232],a,base,true);
    if(active){const q=at(obsU[i],53);cuboid(faces,q,[19,7,151],p[3],'#45b83e');}
  }
  const timeline=[4,5.2,7.4,9.6],positions=[.09,.2,.5,.8];let u=.8;
  for(let i=0;i<timeline.length-1;i++){if(t<timeline[i+1]){u=mix(positions[i],positions[i+1],ease((t-timeline[i])/(timeline[i+1]-timeline[i])));break;}}
  const p=at(u,70),a=p[3];
  // One rectangular industrial component, including the two metal end caps.
  cuboid(faces,p,[118,36,68],a,'#ecebe2',false);
  for(const x of [-49,49]){const q=[p[0]+x*Math.cos(a),p[1]+1,p[2]-x*Math.sin(a)];cuboid(faces,q,[23,39,72],a,'#adbec5',true);}
  for(const x of [-20,2,24]){const q=[p[0]+x*Math.cos(a),p[1]+20,p[2]-x*Math.sin(a)];cuboid(faces,q,[6,2,58],a,x===24?'#4c7a48':'#687e85');}
  paint(g,faces,c);
  const component=project([p[0],p[1]+22,p[2]],c);
  g.save();g.globalAlpha=appear;
  const idX=component[0],idY=component[1]-118;
  g.strokeStyle='rgba(47,78,91,.50)';g.lineWidth=1.5;g.beginPath();g.moveTo(idX,idY+24);g.lineTo(idX,component[1]-30);g.stroke();
  text(g,'ID 07',idX,idY,35,C.ink,'DIN',600,'center');g.restore();
  for(let i=0;i<3;i++)if(t>=obsAt[i]){
    const reveal=ease((t-obsAt[i])/.36),q=project(at(obsU[i],0),c),x=q[0],labelY=760;
    g.save();g.globalAlpha=reveal;
    // A stamped contact mark remains at the actual acquired location.
    g.fillStyle=C.green;g.fillRect(x-4,704,8,16*reveal);g.strokeStyle='rgba(63,101,112,.30)';g.lineWidth=1;g.beginPath();g.moveTo(x,725);g.lineTo(x,labelY-31);g.stroke();
    text(g,`真实观测 ${i+1}`,x,labelY+8*(1-reveal),35,C.ink,'CJK',500,'center');g.restore();
  }
}

exports.draw=function draw(g,t){
  g.reset();g.fillStyle=C.bg;g.fillRect(0,0,1920,1080);
  // A restrained studio falloff, rather than a graphic blue/green gradient.
  const light=g.createRadialGradient(650,170,80,800,450,1450);light.addColorStop(0,'rgba(255,255,255,.20)');light.addColorStop(1,'rgba(255,255,255,0)');g.fillStyle=light;g.fillRect(0,0,1920,1080);
  const unfold=ease((t-3)/1),drift=ease((t-4)/12),c={x:mix(945,960,unfold)+12*drift,y:mix(670,584,unfold)-6*drift,scale:mix(1.06,.995,unfold),yaw:mix(-.06,.015,unfold)+.009*drift};
  shadow(g,c,unfold);sculpture(g,t,unfold,c);
  if(t<4){
    const a=ease(t/.55)*(1-ease((t-2.85)/.5));g.save();g.globalAlpha=a;
    const slide=28*(1-ease(t/.9));
    text(g,'03',136,290+slide,155,C.ink,'DIN',400);
    text(g,'识别与工业检测',137,474+slide,104,C.ink,'CJK Heavy',700);
    text(g,'识别 · 测量 · 告警',143,587+slide,38,C.muted,'CJK',400);g.restore();
  }
  if(t>=4){
    const a=ease((t-4)/.55);g.save();g.globalAlpha=a;
    text(g,'同一工件，连续确认',134,146+18*(1-a),78,C.ink,'CJK Heavy',700);g.restore();
    tracking(g,t,c);
    const reveal=ease((t-4.4)/.5),confirm=t>=11.7?1:0;
    g.save();g.globalAlpha=reveal;
    if(confirm<1){g.globalAlpha=reveal*(1-confirm);text(g,'连续观测积累',960,865-11*confirm,55,C.muted,'CJK',500,'center');}
    if(confirm>0){g.globalAlpha=reveal*confirm;text(g,'类别已确认',960,865+11*(1-confirm),61,'#27813a','CJK Heavy',700,'center');}
    g.globalAlpha=reveal;text(g,'同一身份 · 连续真实观测',960,945,31,C.muted,'CJK',400,'center');g.restore();
  }
};

exports.meta={
  title:'B｜曲面装置',
  palette:['#cbdde9','#39b83f','#346e9f','#dce8e9','#183747'],
  motion:'三块实体曲面经 quintic minimum-jerk 连续下折为单一观测轨道；单个 ID 07 工件在真实观测时刻依次停靠，之后保持最后观测位置；轻微连续正交相机移动。',
  sound_events:[
    {time:.18,type:'warm_solid_touch',note:'轻木质/陶瓷触碰，短尾，低音量'},
    {time:3.12,type:'soft_material_fold',duration:.85,note:'柔和材质擦动，随弧板下折'},
    {time:5.2,type:'glass_resonance',pitch:660,note:'第一次真实观测，温润短玻璃谐振'},
    {time:7.4,type:'glass_resonance',pitch:740,note:'第二次真实观测，与前次同类音色'},
    {time:9.6,type:'glass_resonance',pitch:830,note:'第三次真实观测，同类音色'},
    {time:11.7,type:'soft_settle',note:'低频温润落定与短谐波，类别确认'}
  ],
  visible_text:['03','识别与工业检测','识别 · 测量 · 告警','同一工件，连续确认','真实观测 1','真实观测 2','真实观测 3','ID 07','连续观测积累','类别已确认','同一身份 · 连续真实观测'],
  reference_urls:['https://www.ordinaryfolk.co/project/equilibrium'],
  rendering:'1920x1080 / 16 seconds / 60fps; Canvas projected mesh + painter sorting + diffuse/specular material shading; all geometry procedural; no image-generation assets, photographs, or path prediction.'
};
