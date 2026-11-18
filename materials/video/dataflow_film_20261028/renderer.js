/* A deterministic, asset-free motion film. Shared by Node and the browser. */
(function (root) {
  'use strict';
  const W=1920,H=1080,DURATION=100;
  const C={bg:'#080c12',panel:'#101a24',line:'#30424f',ink:'#eff5f7',muted:'#91a6b5',dim:'#556b7c',cyan:'#62edda',purple:'#b6a0ff',amber:'#f2cf92'};
  const chapters=[
    {start:0,end:9,name:'序章',en:'THE JOURNEY OF A FRAME'},
    {start:9,end:24,name:'公共图像处理',en:'01 / FROM LIGHT TO PIXELS'},
    {start:24,end:41,name:'三路并行',en:'02 / ONE SOURCE, THREE PATHS'},
    {start:41,end:54,name:'ROI 与 NPU',en:'03 / PIXELS INTO PREDICTIONS'},
    {start:54,end:66,name:'跟踪与输出',en:'04 / OBSERVATION TO ACTION'},
    {start:66,end:87,name:'场景训练闭环',en:'05 / LEARN FOR THIS SCENE'},
    {start:87,end:100,name:'系统全景',en:'06 / THE COMPLETE FLOW'}
  ];
  const clamp=(v,a=0,b=1)=>Math.max(a,Math.min(b,v));
  const mix=(a,b,p)=>a+(b-a)*p;
  const ease=p=>{p=clamp(p);return p*p*(3-2*p);};
  const out=p=>1-Math.pow(1-clamp(p),3);
  const range=(p,a,b)=>ease((p-a)/(b-a));
  function key(p,rows) { for(let i=1;i<rows.length;i++)if(p<=rows[i][0])return mix(rows[i-1][1],rows[i][1],ease((p-rows[i-1][0])/(rows[i][0]-rows[i-1][0])));return rows.at(-1)[1]; }
  function alpha(g,a,fn){g.save();g.globalAlpha*=clamp(a);fn();g.restore();}
  function rounded(g,x,y,w,h,r=16){g.beginPath();g.moveTo(x+r,y);g.arcTo(x+w,y,x+w,y+h,r);g.arcTo(x+w,y+h,x,y+h,r);g.arcTo(x,y+h,x,y,r);g.arcTo(x,y,x+w,y,r);g.closePath();}
  function box(g,x,y,w,h,{fill=C.panel,stroke=C.line,r=16,lw=1.4}={}){rounded(g,x,y,w,h,r);if(fill){g.fillStyle=fill;g.fill();}if(stroke){g.strokeStyle=stroke;g.lineWidth=lw;g.stroke();}}
  function txt(g,s,x,y,size=28,color=C.ink,weight=400,align='left',font='Microsoft YaHei'){g.font=`${weight} ${size}px "${font}", "Segoe UI", sans-serif`;g.fillStyle=color;g.textAlign=align;g.textBaseline='alphabetic';g.fillText(s,x,y);}
  function mono(g,s,x,y,size=19,color=C.muted,align='left'){txt(g,s,x,y,size,color,400,align,/[\u3400-\u9fff]/.test(s)?'Microsoft YaHei':'Consolas');}
  function line(g,x1,y1,x2,y2,color=C.line,width=1.5,dash=[]){g.beginPath();g.setLineDash(dash);g.moveTo(x1,y1);g.lineTo(x2,y2);g.strokeStyle=color;g.lineWidth=width;g.stroke();g.setLineDash([]);}
  function dot(g,x,y,r,color=C.cyan,glow=false){g.save();if(glow){g.shadowBlur=18;g.shadowColor=color;}g.beginPath();g.arc(x,y,r,0,Math.PI*2);g.fillStyle=color;g.fill();g.restore();}
  function tag(g,s,x,y,color=C.cyan){g.font='500 18px "Consolas", monospace';const w=g.measureText(s).width+28;box(g,x,y-25,w,37,{fill:'#10242a',stroke:color,r:7,lw:.8});mono(g,s,x+14,y,18,color);return w;}
  function bez(p0,p1,p2,p3,t){let u=1-t;return {x:u*u*u*p0[0]+3*u*u*t*p1[0]+3*u*t*t*p2[0]+t*t*t*p3[0],y:u*u*u*p0[1]+3*u*u*t*p1[1]+3*u*t*t*p2[1]+t*t*t*p3[1]};}
  function route(g,pts,t,{color=C.cyan,count=3,speed=.18,dashed=false,bright=1}={}){
    const [a,b,c,d]=pts;g.beginPath();g.moveTo(...a);g.bezierCurveTo(...b,...c,...d);g.strokeStyle=C.line;g.lineWidth=2;if(dashed)g.setLineDash([6,10]);g.stroke();g.setLineDash([]);
    alpha(g,bright,()=>{for(let i=0;i<count;i++){let q=(t*speed+i/count)%1;for(let j=8;j>=0;j--){let v=q-j*.007;if(v<0)continue;let p=bez(a,b,c,d,v);alpha(g,(1-j/9)*.8,()=>dot(g,p.x,p.y,j===0?4:2.2,color,j===0));}}});
  }
  function wire(g,x1,y1,x2,y2,t,opt={}){const bend=(x2-x1)*.45;route(g,[[x1,y1],[x1+bend,y1],[x2-bend,y2],[x2,y2]],t,opt);}
  function camera(g,cx,cy,z,angle,fn){g.save();g.translate(W/2,570);g.rotate(angle);g.scale(z,z);g.translate(-cx,-cy);fn();g.restore();}
  function corner(g,x,y,w,h,color=C.cyan,len=17){g.strokeStyle=color;g.lineWidth=2;g.beginPath();for(const [px,py,sx,sy] of [[x,y,1,1],[x+w,y,-1,1],[x+w,y+h,-1,-1],[x,y+h,1,-1]]){g.moveTo(px+sx*len,py);g.lineTo(px,py);g.lineTo(px,py+sy*len);}g.stroke();}
  function part(g,x,y,r,angle=0,mode=0){
    g.save();g.translate(x,y);g.rotate(angle);
    {const gr=g.createLinearGradient(-r,-r,r,r);gr.addColorStop(0,'#b5cbd1');gr.addColorStop(.4,'#526b7b');gr.addColorStop(.6,'#1a2c3a');gr.addColorStop(1,'#8ba7b2');g.fillStyle=gr;g.beginPath();for(let i=0;i<6;i++){let a=Math.PI/3*i;let px=Math.cos(a)*r,py=Math.sin(a)*r;i?g.lineTo(px,py):g.moveTo(px,py);}g.closePath();g.fill();g.strokeStyle=mode===2?C.amber:'#94b0b9';g.lineWidth=1.5;g.stroke();g.beginPath();g.arc(0,0,r*.38,0,Math.PI*2);g.fillStyle='#0a151e';g.fill();g.strokeStyle=mode===2?C.amber:'#637f8e';g.stroke();
      for(let i=0;i<4;i++){line(g,-r*.6,-r*.5+i*5,-r*.1,-r*.5+i*5,'#b2c7cc',.6);}
    }
    g.restore();
  }
  function imagePlate(g,x,y,w,h,t,{mode=0,roi=false,detect=false,id=true,scan=true}={}){
    box(g,x,y,w,h,{fill:'#101b25',stroke:'#3a505e',r:12});g.save();rounded(g,x+2,y+2,w-4,h-4,10);g.clip();
    const bg=g.createLinearGradient(x,y,x+w,y+h);bg.addColorStop(0,mode===0?'#182c36':'#102a2d');bg.addColorStop(1,'#0b121b');g.fillStyle=bg;g.fillRect(x,y,w,h);
    for(let i=1;i<8;i++)line(g,x+i*w/8,y,x+i*w/8,y+h,'#233843',.7);
    for(let i=1;i<5;i++)line(g,x,y+i*h/5,x+w,y+i*h/5,'#233843',.7);
    const dx=Math.sin(t*.4)*w*.026;
    for(const [px,py,r,a] of [[.28,.35,.10,.2],[.64,.64,.12,-.2],[.78,.24,.07,.5]])part(g,x+px*w+dx,y+py*h,r*w,a+t*.015,mode===2?2:0);
    if(mode===1){alpha(g,.25,()=>{g.fillStyle=C.cyan;g.fillRect(x,y,w,h);});}
    if(roi){const rx=x+w*.48,ry=y+h*.39,rw=w*.32,rh=h*.4;g.fillStyle='#060c1599';g.fillRect(x,y,w,h);corner(g,rx,ry,rw,rh,C.cyan,16);mono(g,'ROI',rx+9,ry+22,16,C.cyan);}
    if(detect){for(const [px,py,r,n]of[[.28,.35,.13,'ID 01'],[.64,.64,.15,'ID 02']]){const rw=r*w*2,rh=r*w*1.7;corner(g,x+px*w+dx-rw/2,y+py*h-rh/2,rw,rh,C.cyan,13);mono(g,n,x+px*w+dx-rw/2,y+py*h-rh/2-8,15,C.cyan);}}
    if(scan){const sy=y+((t*.18)%1)*h;const gr=g.createLinearGradient(0,sy-38,0,sy+1);gr.addColorStop(0,'#62edda00');gr.addColorStop(1,'#62edda19');g.fillStyle=gr;g.fillRect(x,sy-38,w,39);line(g,x,sy,x+w,sy,'#62edda66',1);}
    g.restore();if(id)mono(g,'FRAME / '+String(248+Math.floor(t*4)).padStart(5,'0'),x+14,y+h-13,15,'#c1d9df');
  }
  function chip(g,x,y,size,t,label='NPU',sub='RK3576',color=C.purple){
    for(let i=1;i<=7;i++){const q=size*i/8;line(g,x-17,y+q,x,y+q,C.dim,3);line(g,x+size,y+q,x+size+17,y+q,C.dim,3);line(g,x+q,y-17,x+q,y,C.dim,3);line(g,x+q,y+size,x+q,y+size+17,C.dim,3);}
    box(g,x,y,size,size,{fill:'#131e2c',stroke:color,r:17,lw:1.7});box(g,x+18,y+18,size-36,size-36,{fill:'#0d1420',stroke:'#3b3b55',r:8});
    for(let a=0;a<7;a++)for(let b=0;b<7;b++){const r=(Math.sin(a*1.7+b*.9+t*2)+1)/2;alpha(g,.09+r*.24,()=>{g.fillStyle=color;g.fillRect(x+28+a*(size-58)/7,y+28+b*(size-58)/7,(size-72)/7,(size-72)/7);});}
    txt(g,label,x+size/2,y+size/2+9,size*.22,C.ink,600,'center','Bahnschrift');mono(g,sub,x+size/2,y+size/2+40,16,color,'center');
  }
  function background(g,t){
    g.fillStyle=C.bg;g.fillRect(0,0,W,H);
    let r=g.createRadialGradient(1230+Math.sin(t*.08)*160,430,0,1230,430,1050);r.addColorStop(0,'#13323b');r.addColorStop(.5,'#0d1823');r.addColorStop(1,C.bg);g.fillStyle=r;g.fillRect(0,0,W,H);
    alpha(g,.30,()=>{for(let x=56;x<W;x+=64)for(let y=64;y<H;y+=64)dot(g,x+Math.sin(t*.07)*5,y,1,'#465d6b');});
    alpha(g,.18,()=>{for(let i=0;i<8;i++)line(g,-200+i*330,1080,770+i*55,290,'#315661',1);for(let i=0;i<9;i++){let y=450+i*i*8;line(g,0,y,W,y,'#315661',1);}});
    const vign=g.createRadialGradient(960,520,350,960,520,1100);vign.addColorStop(0,'#080c1200');vign.addColorStop(1,'#080c12bb');g.fillStyle=vign;g.fillRect(0,0,W,H);
  }
  function heading(g,kicker,title,sub,p){alpha(g,out(p*9),()=>{const dy=(1-out(p*9))*22;mono(g,kicker,116,139+dy,20,C.cyan);txt(g,title,112,213+dy,52,C.ink,600);if(sub)txt(g,sub,116,256+dy,24,C.muted);});}
  function caption(g,main,sub){txt(g,main,960,925,27,C.ink,400,'center');if(sub)txt(g,sub,960,962,20,C.muted,400,'center');}
  function intro(g,p,t){
    const z=mix(1.2,.92,ease(p));
    camera(g,1300,540,z,mix(-.08,.015,p),()=>{
      for(let i=3;i>=1;i--)alpha(g,.16,()=>box(g,1035+i*24,355+i*24,570,360,{fill:'#152a34',stroke:'#80a8b1',r:16}));
      imagePlate(g,1035,355,570,360,t,{detect:p>.55,id:false});
      g.save();g.translate(1320,535);g.scale(1,.55);for(let i=0;i<3;i++){g.beginPath();g.ellipse(0,0,390+i*38,390+i*38,0,0,Math.PI*2);g.strokeStyle=i===0?'#5be0cc42':'#698b9a22';g.lineWidth=1.5;g.stroke();}for(let i=0;i<5;i++){let a=t*.15+i*1.256;dot(g,Math.cos(a)*425,Math.sin(a)*425,5,i%2?C.purple:C.cyan,true);}g.restore();
      tag(g,'NATIVE RGB',1440,332);mono(g,'IMX415  →  FPGA  →  RK3576',1110,795,20,C.muted);
    });
    const gr=g.createLinearGradient(0,0,1100,0);gr.addColorStop(0,'#080c12');gr.addColorStop(.65,'#080c12f0');gr.addColorStop(1,'#080c1200');g.fillStyle=gr;g.fillRect(0,0,1160,H);
    alpha(g,out(p*5),()=>{mono(g,'PROJECT 03 / INDUSTRIAL VISION',120,271,22,C.cyan);txt(g,'让图像',112,397,94,C.ink,600);txt(g,'成为判断',112,515,94,C.ink,600);line(g,120,565,280,565,C.cyan,3);txt(g,'FPGA × RK3576',120,637,32,C.ink,400,'left','Bahnschrift');txt(g,'一次贯穿采集、识别与训练的数据旅程',120,687,26,C.muted);});
    alpha(g,range(p,.4,.7),()=>{mono(g,'A FRAME. A DECISION. A COMPLETE SYSTEM.',120,832,18,C.dim);});
  }
  function processing(g,p,t){
    heading(g,chapters[1].en,'从光，到可计算的像素','公共处理先发生，三条数据通路共享同一处理定义。',p);
    const cx=key(p,[[0,470],[.25,800],[.52,1460],[.73,1980],[1,1240]]),z=key(p,[[0,1.22],[.23,1.04],[.68,1.04],[1,.67]]);
    camera(g,cx,560,z,.013*Math.sin(p*Math.PI*2),()=>{
      const nodes=[{x:310,title:'IMX415',sub:'1920 × 1080',mode:0},{x:930,title:'ISP · RGB 增强',sub:'曝光 / 白平衡 / 直方图',mode:1},{x:1550,title:'工业前景',sub:'全分辨率空间处理',mode:1},{x:2170,title:'边缘 · 形态学',sub:'Sobel / opening 等',mode:2}];
      for(let i=0;i<3;i++)wire(g,nodes[i].x+235,545,nodes[i+1].x-235,545,t+i,{count:2,speed:.4});
      for(let i=0;i<nodes.length;i++){const n=nodes[i];mono(g,'0'+(i+1),n.x-220,331,24,C.cyan);imagePlate(g,n.x-230,370,460,285,t-i*.4,{mode:n.mode,id:i===0});txt(g,n.title,n.x,714,32,C.ink,500,'center');txt(g,n.sub,n.x,755,22,C.muted,400,'center');}
      alpha(g,range(p,.72,.88),()=>{line(g,80,809,2400,809,'#38515e',1);mono(g,'COMMON IMAGE PROCESSING',1240,854,21,C.cyan,'center');});
    });
    const c=p<.3?'一张图像，进入硬件流水线':p<.69?'先形成公共处理输出，再按用途分流':'处理后的原生坐标与像素尺寸，继续传递';
    caption(g,c,'此处为程序绘制的流程示意，不是相机实拍或算法效果测评。');
  }
  function streams(g,p,t){
    heading(g,chapters[2].en,'同一主干，三路并行','显示、推理、采图：各自调度，共享 DDR 与 PCIe 传输资源。',p);
    const z=key(p,[[0,1.24],[.25,1],[.8,1],[1,.97]]),cx=key(p,[[0,660],[.3,960],[1,990]]);
    camera(g,cx,570,z,mix(-.025,0,ease(p)),()=>{
      imagePlate(g,110,451,300,190,t,{mode:2,id:false});txt(g,'公共 RGB',260,687,28,C.ink,500,'center');
      const ys=[360,565,770];
      for(let i=0;i<3;i++){
        route(g,[[410,546],[590,546],[520,ys[i]],[730,ys[i]]],t+i,{count:i===0?5:i===1?3:1,speed:i===0?.22:i===1?.15:.08});
        wire(g,1010,ys[i],1450,ys[i],t+i,{count:i===0?4:i===1?2:1,speed:i===0?.25:i===1?.17:.1});
      }
      box(g,1138,300,180,535,{fill:'#13212b',stroke:'#405966',r:20});
      for(let i=0;i<3;i++){box(g,1158,ys[i]-35,140,70,{fill:'#0d1721',stroke:'#3b555e',r:8});for(let j=0;j<5;j++)alpha(g,.2+.6*((j+Math.floor(t*2+i))%5===0),()=>box(g,1170+j*24,ys[i]-17,16,34,{fill:C.cyan,stroke:null,r:2}));}
      mono(g,'DDR',1228,269,24,C.ink,'center');mono(g,'PCIe / DMA',1228,873,20,C.muted,'center');
      const desc=[['01','完整预览','1920 × 1080','HDMI 显示'],['02','推理 ROI','连续原生 RGB','RK3576 NPU'],['03','按需采图','独立单帧请求','PC · VisionTrainer']];
      for(let i=0;i<3;i++){let y=ys[i];box(g,730,y-67,280,134,{fill:'#101e28',stroke:i===0?'#5bada4':'#355460',r:12});mono(g,desc[i][0],751,y-28,17,C.cyan);txt(g,desc[i][1],786,y-12,30,C.ink,500);txt(g,desc[i][2],786,y+29,19,C.muted);dot(g,1450,y,5,C.cyan,true);txt(g,desc[i][3],1480,y+8,26,C.ink,500);}
    });
    caption(g,'主画面持续运行，推理与采图按各自节奏取数','动画节奏仅解释并行关系，不表示实测吞吐或帧率。');
  }
  function inference(g,p,t){
    heading(g,chapters[3].en,'把关注区域，送入 NPU','图像保持原生尺寸；检测结果与源帧身份一起返回。',p);
    camera(g,key(p,[[0,720],[.35,900],[.75,1150],[1,1150]]),550,key(p,[[0,1.1],[.45,1],[1,.98]]),-.014*Math.sin(p*Math.PI),()=>{
      imagePlate(g,190,350,620,385,t,{roi:true,id:true});txt(g,'公共处理后的 RGB',500,792,27,C.muted,400,'center');
      wire(g,810,545,1230,545,t,{color:C.cyan,count:3,speed:.24});
      const q=range(p,.12,.53),rx=mix(487.6,894,q),ry=mix(500.15,415,q),rw=mix(198.4,270,q),rh=rw/198.4*154;
      alpha(g,range(p,.06,.15),()=>{box(g,rx-5,ry-5,rw+10,rh+10,{fill:'#081820',stroke:C.cyan,r:9,lw:2});g.save();rounded(g,rx,ry,rw,rh,6);g.clip();g.translate(rx,ry);g.scale(rw/198.4,rh/154);g.translate(-487.6,-500.15);imagePlate(g,190,350,620,385,t,{id:false,scan:false});g.restore();tag(g,'NATIVE CROP',rx,ry-22);});
      chip(g,1260,413,255,t,'NPU','RKNN / INFERENCE');
      alpha(g,range(p,.42,.61),()=>{wire(g,1536,545,1700,545,t,{color:C.purple,count:2,speed:.3});box(g,1650,390,290,305,{fill:'#151a2d',stroke:'#625985',r:16});mono(g,'OUTPUT',1680,434,20,C.purple);txt(g,'位置',1680,496,28);txt(g,'姿态',1680,551,28);txt(g,'置信度',1680,606,28);line(g,1680,633,1905,633,'#554c75');mono(g,'+ SOURCE FRAME ID',1680,671,17,C.purple);});
      alpha(g,range(p,.55,.8),()=>{mono(g,'原生裁剪 → 张量 → 模型输出',1160,797,24,C.purple,'center');});
    });
    caption(g,'ROI 输入尺寸匹配所选模型，输出位置映射回原坐标','模型包沿独立通路进入运行时，保留现场控制配置。');
  }
  function outcomes(g,p,t){
    heading(g,chapters[4].en,'让一次观察，成为稳定判断','跨帧关联 → ID 与姿态确认 → 计数、显示和外部输出。',p);
    camera(g,key(p,[[0,780],[.42,960],[1,1110]]),560,key(p,[[0,1.08],[.45,.97],[1,.92]]),.012*Math.sin(p*Math.PI),()=>{
      const xs=[220,680,1140];
      for(let i=0;i<3;i++){imagePlate(g,xs[i],340,355,220,t+i*.65,{detect:true,id:false,scan:false});mono(g,['t₀','t₁','t₂'][i],xs[i]+18,596,23,C.muted);tag(g,'ID 02',xs[i]+247,596,C.cyan);if(i<2)wire(g,xs[i]+355,451,xs[i+1],451,t,{count:2,speed:.33});}
      const sy=690;wire(g,395,sy,1575,sy,t,{count:6,speed:.1});
      for(let i=0;i<3;i++){line(g,xs[i]+178,621,xs[i]+178,sy,'#466a73',1.5);dot(g,xs[i]+178,sy,6,C.cyan,true);}
      txt(g,'同一对象，累积独立源帧观测',870,772,31,C.ink,500,'center');
      alpha(g,range(p,.16,.45),()=>{box(g,1575,330,320,470,{fill:'#121e27',stroke:'#41606d',r:18});mono(g,'EVENT OUTPUT',1605,378,20,C.cyan);txt(g,'姿态确认',1605,444,30);txt(g,'计数 +1',1605,518,39,C.cyan,600);txt(g,'显示 / 图表',1605,583,25,C.muted);txt(g,'GPIO 输出',1605,645,27,C.ink);g.beginPath();for(let x=0;x<250;x++){let v=(x/250+t*.25)%1;let y=v>.4&&v<.62?-25:0;x?g.lineTo(1605+x,706+y):g.moveTo(1605+x,706+y);}g.strokeStyle=C.cyan;g.lineWidth=2;g.stroke();txt(g,'记录与采证',1605,758,25,C.muted);});
    });
    caption(g,'结果汇入显示缓存，预览继续独立运行','ID、姿态、计数与 GPIO 为流程示意；实际效果需现场验收。');
  }
  function smallNode(g,x,y,title,sub,index,color=C.cyan,active=0){box(g,x-145,y-68,290,136,{fill:color===C.purple?'#171b2c':'#111f29',stroke:active>.4?color:'#3d505f',r:13,lw:active>.4?2:1.2});mono(g,index,x-123,y-37,16,color);txt(g,title,x,y+2,29,C.ink,500,'center');txt(g,sub,x,y+39,18,C.muted,400,'center');if(active>.2)alpha(g,active,()=>{dot(g,x+121,y-41,4,color,true);});}
  function training(g,p,t){
    heading(g,chapters[5].en,'为当前场景，训练当前模型','VisionTrainer · 采集 / 标注 / GPU 训练 / 新图测试 / 明确选型',p);
    const cx=key(p,[[0,500],[.25,950],[.46,1410],[.68,1280],[1,950]]),z=key(p,[[0,1.18],[.28,1],[.48,1.08],[.76,.96],[1,.93]]);
    camera(g,cx,580,z,.009*Math.sin(p*6),()=>{
      const xs=[270,700,1130,1560],y=430,y2=740;
      for(let i=0;i<3;i++)wire(g,xs[i]+145,y,xs[i+1]-145,y,t+i,{count:3,speed:.2});
      route(g,[[1705,y],[1820,y],[1820,y2],[1705,y2]],t,{color:C.purple,count:3,speed:.14});
      for(let i=3;i>1;i--)route(g,[[xs[i]-145,y2],[xs[i]-225,y2],[xs[i-1]+225,y2],[xs[i-1]+145,y2]],t,{color:C.purple,count:3,speed:.2});
      route(g,[[985,y2],[900,y2],[900,y2],[845,y2]],t,{color:C.purple,count:2,speed:.3});
      smallNode(g,270,y,'原生采图','15 张 · 当前场景','01',C.cyan,1);smallNode(g,700,y,'人工确认标注','候选框 / 姿态标签','02',C.cyan,range(p,.08,.2));smallNode(g,1130,y,'GPU 训练','随机初始化 · 原生裁剪','03',C.cyan,range(p,.23,.35));smallNode(g,1560,y,'独立新图测试','10 张 · PC GPU 推理','04',C.cyan,range(p,.35,.47));
      smallNode(g,1560,y2,'模型转换','ONNX → RKNN','05',C.purple,range(p,.45,.55));smallNode(g,1130,y2,'明确选择模型','训练北京时间 / 场景 / 哈希','06',C.purple,range(p,.57,.68));smallNode(g,700,y2,'模型专用部署','RK3576 · NPU','07',C.purple,range(p,.7,.82));
      alpha(g,.65*range(p,.77,.94),()=>{route(g,[[1560,362],[1560,307],[700,307],[700,362]],t,{color:C.amber,dashed:true,count:1,speed:.08});mono(g,'纠错后加入下一轮；该组退出独立验收',1130,304,17,C.amber,'center');});
      alpha(g,range(p,.58,.7),()=>{box(g,950,626,360,33,{fill:'#1b1931',stroke:null,r:6});mono(g,'SELECTED BY THE USER',1130,650,18,C.purple,'center');});
      alpha(g,range(p,.72,.87),()=>{wire(g,555,740,420,740,t,{color:C.purple,count:2,speed:.3});chip(g,230,652,175,t,'NPU','MODEL ONLY');});
    });
    const c=p<.47?'采集本场景，人工确认，再训练与测试':p<.73?'模型用真实训练时间、场景和哈希标识':'明确选型后，仅将模型包交给设备';
    caption(g,c,'场景改变后重新采集训练；历史数据与模型保留。');
  }
  function overview(g,p,t){
    heading(g,chapters[6].en,p>.66?'感知 · 计算 · 判断 · 迭代':'一帧图像，连接整个系统','图像流向前，模型与控制回到现场。',p);
    camera(g,960,580,mix(1.08,.94,ease(p)),mix(-.018,0,ease(p)),()=>{
      const y=505;
      wire(g,330,y,500,y,t,{count:3,speed:.23});wire(g,760,y,965,y,t,{count:4,speed:.2});
      route(g,[[1260,y],[1360,y],[1340,365],[1460,365]],t,{count:3,speed:.18});route(g,[[1260,y],[1360,y],[1360,610],[1460,610]],t,{count:2,speed:.16});
      route(g,[[1010,611],[775,657],[775,805],[900,805]],t,{count:2,speed:.12});
      route(g,[[1180,805],[1330,805],[1370,685],[1180,615]],t,{color:C.purple,count:3,speed:.15});
      route(g,[[900,850],[620,960],[580,700],[980,590]],t,{color:C.muted,count:1,speed:.08,dashed:true});
      wire(g,965,565,760,565,t,{color:C.muted,count:1,speed:.12,dashed:true});
      imagePlate(g,90,429,240,152,t,{id:false,scan:false});txt(g,'IMX415',210,628,26,C.ink,500,'center','Bahnschrift');
      box(g,500,386,260,225,{fill:'#12212b',stroke:'#4f8d89',r:16});mono(g,'FPGA',630,441,35,C.ink,'center');txt(g,'公共图像处理',630,491,26,C.ink,500,'center');txt(g,'三路分流',630,533,24,C.cyan,400,'center');txt(g,'DDR 缓冲',630,573,21,C.muted,400,'center');
      mono(g,'PCIe / DMA',860,462,19,C.muted,'center');
      box(g,965,386,295,225,{fill:'#181b2d',stroke:'#736a9c',r:16});mono(g,'RK3576',1112,440,35,C.ink,'center');txt(g,'NPU 推理',1112,492,26,C.purple,500,'center');txt(g,'跟踪 · 姿态 · 计数',1112,537,22,C.ink,400,'center');txt(g,'缓存与输出',1112,575,21,C.muted,400,'center');
      imagePlate(g,1460,294,300,173,t,{detect:true,id:false,scan:false});txt(g,'HDMI / 显示',1610,510,25,C.ink,500,'center');
      box(g,1460,562,300,100,{fill:'#10202a',stroke:'#3c5664',r:13});txt(g,'GPIO · 图表 · 记录',1610,621,25,C.ink,500,'center');
      box(g,900,725,280,171,{fill:'#181b2d',stroke:'#736a9c',r:16});mono(g,'PC / VISIONTRAINER',1040,763,19,C.purple,'center');txt(g,'采集与标注',1040,806,25,C.ink,500,'center');txt(g,'GPU 训练 · 测试 · 选型',1040,851,20,C.muted,400,'center');
      mono(g,'原生采图',737,742,18,C.cyan);mono(g,'模型包',1332,759,18,C.purple);mono(g,'参数 / ROI / 阈值 / 确认次数',480,906,17,C.muted);
      alpha(g,.8,()=>{line(g,1470,825,1510,825,C.cyan,2);txt(g,'图像数据',1526,832,18,C.muted);line(g,1470,865,1510,865,C.purple,2);txt(g,'模型数据',1526,872,18,C.muted);});
    });
    caption(g,'FPGA × RK3576 × VisionTrainer','三路方案：离线候选，尚未完成当前组合的实板验收。');
  }
  const scenes=[intro,processing,streams,inference,outcomes,training,overview];
  function chrome(g,t,index){
    mono(g,'VISION / DATAFLOW',116,65,18,C.muted);mono(g,'2026.10  /  DESIGN FILM 01',1805,65,17,C.dim,'right');
    line(g,116,90,1804,90,'#2a3b47',1);
    line(g,116,1011,1804,1011,'#30424f',1.5);line(g,116,1011,116+1688*clamp(t/DURATION),1011,C.cyan,2.8);
    for(const ch of chapters){const x=116+ch.start/DURATION*1688;dot(g,x,1011,3.3,ch.start<=t?C.cyan:C.dim);}
    mono(g,String(index).padStart(2,'0')+' / '+chapters[index].name,116,1051,18,C.muted);mono(g,'架构示意 · 非实测画面 / OFFLINE CANDIDATE',960,1051,16,C.dim,'center');mono(g,String(Math.floor(t)).padStart(3,'0')+' / 100',1805,1051,18,C.muted,'right');
  }
  function draw(g,time,width=W,height=H){
    const t=clamp(time,0,DURATION-.0001),index=chapters.findIndex(ch=>t<ch.end),ch=chapters[index],p=(t-ch.start)/(ch.end-ch.start);
    g.save();g.setTransform(width/W,0,0,height/H,0,0);background(g,t);
    const enter=index===0?1:ease((t-ch.start)/.7);
    if(enter<1){const prev=chapters[index-1];alpha(g,1-enter,()=>scenes[index-1](g,1,prev.end));}
    alpha(g,enter,()=>scenes[index](g,p,t));chrome(g,t,index);
    if(t>98.7)alpha(g,range(t,98.7,100)*.4,()=>{g.fillStyle=C.bg;g.fillRect(0,0,W,H);});g.restore();
  }
  const api={draw,chapters,DURATION,W,H};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.DataflowFilm=api;
})(typeof globalThis!=='undefined'?globalThis:this);
