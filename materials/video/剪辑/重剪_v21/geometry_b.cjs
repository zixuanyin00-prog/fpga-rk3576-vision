'use strict';

// B / physical study. Deterministic, perspective-correct, depth-tested software 3D.
// No painter sorting, motion quantization, generated images, WebGL or network assets.
const {createCanvas}=require('../../dataflow_film_20261028/node_modules/@napi-rs/canvas');
const C={bg:'#cbdde9',ink:'#183747',muted:'#587482',green:'#39b83f',blue:'#346e9f',silver:'#dce8e9',edge:'#aac1cb',black:'#263b42'};
const clamp=(x,a=0,b=1)=>Math.max(a,Math.min(b,x));
const smooth=x=>{x=clamp(x);return x*x*x*(10+x*(-15+6*x));};
const mix=(a,b,t)=>a+(b-a)*t;
const add=(a,b)=>[a[0]+b[0],a[1]+b[1],a[2]+b[2]];
const sub=(a,b)=>[a[0]-b[0],a[1]-b[1],a[2]-b[2]];
const dot=(a,b)=>a[0]*b[0]+a[1]*b[1]+a[2]*b[2];
const mul=(a,s)=>a.map(v=>v*s);
const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
const unit=a=>mul(a,1/(Math.hypot(...a)||1));
const hex=s=>{const n=parseInt(s.slice(1),16);return [n>>16,(n>>8)&255,n&255];};
const meshCache=new Map(),surfaceCache=new Map();
const MAT={silver:{color:C.silver,metal:.72,roughness:.36},blue:{color:C.blue,metal:.28,roughness:.43},green:{color:C.green,metal:.12,roughness:.5},black:{color:C.black,metal:.08,roughness:.6}};

function createScene(options={}){
  const camera={eye:[1050,800,1500],target:[0,70,0],up:[0,1,0],fov:32,width:1920,height:1080,...options};
  return {camera,objects:[],shadows:[],landmarks:{},bounds:[]};
}
function cameraBasis(c){
  const f=unit(sub(c.target,c.eye)),r=unit(cross(f,c.up||[0,1,0])),u=cross(r,f);
  return {f,r,u,focal:c.height/(2*Math.tan(c.fov*Math.PI/360)),cx:c.width/2,cy:c.height/2};
}
function project(p,c){
  const b=cameraBasis(c),v=sub(p,c.eye),z=dot(v,b.f);
  return {x:b.cx+dot(v,b.r)*b.focal/z,y:b.cy-dot(v,b.u)*b.focal/z,z};
}
function rotation(r){
  const [x,y,z]=r,sx=Math.sin(x),cx=Math.cos(x),sy=Math.sin(y),cy=Math.cos(y),sz=Math.sin(z),cz=Math.cos(z);
  return [cz*cy,cz*sy*sx-sz*cx,cz*sy*cx+sz*sx,sz*cy,sz*sy*sx+cz*cx,sz*sy*cx-cz*sx,-sy,cy*sx,cy*cx];
}
function rotate(p,m){return [m[0]*p[0]+m[1]*p[1]+m[2]*p[2],m[3]*p[0]+m[4]*p[1]+m[5]*p[2],m[6]*p[0]+m[7]*p[1]+m[8]*p[2]];}
function transformPoint(p,o){return add(rotate(mul(p,o.scale||1),rotation(o.rotation||[0,0,0])),o.position||[0,0,0]);}
function material(options,base=MAT.silver){return {...base,...(typeof options==='string'?{color:options}:options)};}

// Every patch uses shared analytic positions/normals; winding is normalized once.
function meshBuilder(){
  const v=[],indices=[];
  return {
    patch(nu,nv,fn){
      const start=v.length/7;
      for(let i=0;i<=nu;i++)for(let j=0;j<=nv;j++){
        const p=fn(i/nu,j/nv);v.push(...p.slice(0,6),p[6]===undefined?1:p[6]);
      }
      for(let i=0;i<nu;i++)for(let j=0;j<nv;j++){
        const a=start+i*(nv+1)+j,b=a+nv+1;
        indices.push(a,b,b+1,a,b+1,a+1);
      }
    },
    finish(){
      for(let k=0;k<indices.length;k+=3){
        const a=indices[k]*7,b=indices[k+1]*7,c=indices[k+2]*7;
        const ab=[v[b]-v[a],v[b+1]-v[a+1],v[b+2]-v[a+2]],ac=[v[c]-v[a],v[c+1]-v[a+1],v[c+2]-v[a+2]];
        if(dot(cross(ab,ac),[v[a+3]+v[b+3]+v[c+3],v[a+4]+v[b+4]+v[c+4],v[a+5]+v[b+5]+v[c+5]])<0){const q=indices[k+1];indices[k+1]=indices[k+2];indices[k+2]=q;}
      }
      return {v:new Float32Array(v),indices:new Uint32Array(indices)};
    }
  };
}
function cached(key,build){if(!meshCache.has(key))meshCache.set(key,build());return meshCache.get(key);}
function instance(scene,mesh,options={},mat=MAT.silver){
  const o={mesh,position:options.position||[0,0,0],rotation:options.rotation||[0,options.angle||0,0],scale:options.scale||1,material:material(options.material||options.color,mat)};
  scene.objects.push(o);return o;
}

function addPlate(scene,options={}){
  const size=options.size||[420,16,260],radius=Math.min(options.radius===undefined?6:options.radius,...size.map(x=>x/2)),segments=options.segments||4;
  const mesh=cached('box'+JSON.stringify([size,radius,segments]),()=>{
    const b=meshBuilder(),h=size.map(x=>x/2),inner=h.map(x=>Math.max(0,x-radius));
    // Rounded cube surface: shared fillet profile, with planar spans remaining flat.
    const coords=h.map((q,k)=>{
      if(radius===0)return [-q,q];
      const a=[];
      for(let i=0;i<=segments;i++)a.push(-inner[k]-radius*Math.cos(i*Math.PI/(2*segments)));
      for(let i=0;i<=segments;i++)a.push(inner[k]+radius*Math.sin(i*Math.PI/(2*segments)));
      return [...new Set(a.map(x=>Math.round(x*1e8)/1e8))];
    });
    for(let axis=0;axis<3;axis++)for(const sign of [-1,1]){
      const axes=[0,1,2].filter(q=>q!==axis),a=coords[axes[0]],c=coords[axes[1]];
      b.patch(a.length-1,c.length-1,(u,w)=>{
        const p=[0,0,0];p[axis]=h[axis]*sign;p[axes[0]]=a[Math.round(u*(a.length-1))];p[axes[1]]=c[Math.round(w*(c.length-1))];
        const core=p.map((q,k)=>clamp(q,-inner[k],inner[k])),delta=sub(p,core),n=unit(delta);
        return [...add(core,mul(n,radius)),...n];
      });
    }
    return b.finish();
  });
  return instance(scene,mesh,options);
}

function addRibbon(scene,options={}){
  const radius=options.radius||255,width=options.width||250,thickness=options.thickness||12;
  const arc=options.arc||[-2.3,.4],tilt=options.tilt===undefined?Math.PI/2:options.tilt,segments=options.segments||64,bevel=Math.min(thickness*.43,width*.02);
  const mesh=cached('ribbon'+JSON.stringify([radius,width,thickness,arc,tilt,segments]),()=>{
    const b=meshBuilder(),section=[];
    for(let k=0;k<4;k++)for(let j=0;j<=4;j++){
      const a=k*Math.PI/2+j*Math.PI/8,nx=Math.cos(a),ny=Math.sin(a);
      const cx=(k===0||k===3?1:-1)*(width/2-bevel),cy=(k<2?1:-1)*(thickness/2-bevel);
      section.push([cx+bevel*nx,cy+bevel*ny,nx,ny]);
    }
    const pt=(u,q)=>{
      const a=mix(arc[0],arc[1],u),sa=Math.sin(a),ca=Math.cos(a),ct=Math.cos(tilt),st=Math.sin(tilt);
      const [v,d,nv,nd]=q,r=radius+v*ct-d*st;
      return [r*sa,v*st+d*ct,r*ca,sa*(nv*ct-nd*st),nv*st+nd*ct,ca*(nv*ct-nd*st)];
    };
    b.patch(segments,section.length,(u,v)=>pt(u,section[Math.round(v*section.length)%section.length]));
    for(const end of [0,1]){
      const a=mix(arc[0],arc[1],end),n=[Math.cos(a)*(end?1:-1),0,-Math.sin(a)*(end?1:-1)];
      b.patch(1,section.length,(r,v)=>{const q=section[Math.round(v*section.length)%section.length],p=pt(end,[q[0]*r,q[1]*r,q[2],q[3]]);return [...p.slice(0,3),...n];});
    }
    return b.finish();
  });
  return instance(scene,mesh,options);
}

// A satin ribbon swept along a spatial spline. Width and roll vary continuously.
// The centre line can be curved in all three axes; the edge follows the surface,
// rather than being drawn afterwards as an unrelated screen-space stroke.
function addSweep(scene,options={}){
  const points=options.points,width=options.width||140,thickness=options.thickness||9,twist=options.twist||[0,0,0],segments=options.segments||100;
  const mesh=cached('sweep'+JSON.stringify([points,width,thickness,twist,segments]),()=>{
    const builder=meshBuilder(),corners=[],section=[],bevel=thickness*.43;
    for(let k=0;k<4;k++)for(let j=0;j<=4;j++){
      const a=k*Math.PI/2+j*Math.PI/8,nv=Math.cos(a),nd=Math.sin(a);
      corners.push([(k===0||k===3?1:-1)*(width/2-bevel)+bevel*nv,(k<2?1:-1)*(thickness/2-bevel)+bevel*nd,nv,nd]);
    }
    for(let k=0;k<corners.length;k++){
      const a=corners[k],b=corners[(k+1)%corners.length];section.push(a);
      if(k===4||k===14)for(let j=1;j<9;j++)section.push([mix(a[0],b[0],j/9),mix(a[1],b[1],j/9),0,k===4?1:-1]);
    }
    const curve=u=>{
      const at=Math.min(points.length-2,Math.floor(u*(points.length-1))),t=u*(points.length-1)-at;
      const p1=points[at],p2=points[at+1],p0=points[at-1]||sub(mul(p1,2),p2),p3=points[at+2]||sub(mul(p2,2),p1);
      const p=[0,1,2].map(k=>.5*((2*p1[k])+(-p0[k]+p2[k])*t+(2*p0[k]-5*p1[k]+4*p2[k]-p3[k])*t*t+(-p0[k]+3*p1[k]-3*p2[k]+p3[k])*t*t*t));
      const tangent=unit([0,1,2].map(k=>.5*((-p0[k]+p2[k])+2*(2*p0[k]-5*p1[k]+4*p2[k]-p3[k])*t+3*(-p0[k]+3*p1[k]-3*p2[k]+p3[k])*t*t)));
      const base=unit(sub([0,0,1],mul(tangent,tangent[2]))),side=cross(tangent,base),a=mix(twist[0],twist[1],u)+twist[2]*Math.sin(u*Math.PI);
      const across=add(mul(base,Math.cos(a)),mul(side,Math.sin(a))),normal=cross(across,tangent),taper=.86+.14*Math.sin(u*Math.PI);
      return {p,tangent,across,normal,taper};
    };
    const point=(u,q)=>{const f=curve(u);return add(f.p,add(mul(f.across,q[0]*f.taper),mul(f.normal,q[1])));};
    builder.patch(segments,section.length,(u,v)=>{
      const q=section[Math.round(v*section.length)%section.length],f=curve(u),p=point(u,q),before=Math.max(0,u-.0001),after=Math.min(1,u+.0001),tangent=unit(sub(point(after,q),point(before,q)));
      const acrossTangent=add(mul(f.across,-q[3]*f.taper),mul(f.normal,q[2])),normal=unit(cross(tangent,acrossTangent));
      return [...p,...normal];
    });
    for(const end of [0,1])builder.patch(1,section.length,(r,v)=>{
      const q=section[Math.round(v*section.length)%section.length],f=curve(end),p=point(end,[q[0]*r,q[1]*r,q[2],q[3]]);return [...p,...mul(f.tangent,end?1:-1)];
    });
    return builder.finish();
  });
  return instance(scene,mesh,options);
}

function addCylinder(scene,options={}){
  const radius=options.radius||30,height=options.height||30,segments=options.segments||48;
  const mesh=cached('cylinder'+JSON.stringify([radius,height,segments]),()=>{
    const b=meshBuilder();
    b.patch(segments,1,(u,v)=>{const a=u*Math.PI*2;return [radius*Math.cos(a),(v-.5)*height,radius*Math.sin(a),Math.cos(a),0,Math.sin(a)];});
    for(const sign of [-1,1])b.patch(segments,1,(u,v)=>{const a=u*Math.PI*2;return [v*radius*Math.cos(a),sign*height/2,v*radius*Math.sin(a),0,sign,0];});
    return b.finish();
  });
  return instance(scene,mesh,options,MAT.black);
}

function addResistor(scene,options={}){
  const parent={position:options.position||[0,22,0],rotation:options.rotation||[0,options.angle||0,0],scale:options.scale||1};
  const local=(p,size,color,radius=3)=>addPlate(scene,{position:transformPoint(p,parent),size:size.map(x=>x*parent.scale),rotation:parent.rotation,color,radius:radius*parent.scale,segments:3});
  local([0,0,0],[146,30,82],options.color||C.black,5);
  for(const x of [-60,60])local([x,0,0],[30,32,85],'#c4d3d9',3.5);
  // Ceramic marking, kept on the top surface of the real flat SMD package.
  const digits=[[1,2,5,6],[0,1,2],[0,1,2,3,6]],seg=[[-5,-12,5,-12],[6,-11,6,-1],[6,1,6,11],[-5,12,5,12],[-6,1,-6,11],[-6,-11,-6,-1],[-5,0,5,0]];
  if(options.marking!==false)for(let d=0;d<3;d++)for(const k of digits[d]){
    const [x1,z1,x2,z2]=seg[k],x=(d-1)*21+(x1+x2)/2,z=(z1+z2)/2;
    local([x,15.12,z],[Math.abs(x2-x1)+2.2,.26,Math.abs(z2-z1)+2.2],'#c3ccc7',.1);
  }
  const corners=[];for(const x of [-76,76])for(const y of [-16,16])for(const z of [-43,43])corners.push(transformPoint([x,y,z],parent));
  return {center:parent.position,top:transformPoint([0,17,0],parent),corners};
}

function addCamera(scene,options={}){
  const p=options.position||[0,270,0],s=options.scale||1,r=options.rotation||[0,options.angle||0,0],parent={position:p,rotation:r,scale:s};
  const local=(q,size,color,rad=5)=>addPlate(scene,{position:transformPoint(q,parent),size:size.map(v=>v*s),rotation:r,color,radius:rad*s,segments:3});
  local([0,0,0],[136,60,94],'#c6d7df',7);
  local([0,31,0],[98,3,68],'#e8eff1',1.4);
  for(const [y,rad,h,color] of [[-39,33,18,'#3d515a'],[-51,28,8,'#788f9b'],[-58,24,8,'#213746'],[-62.4,20,1.2,'#325b70']]){
    addCylinder(scene,{position:transformPoint([0,y,0],parent),rotation:r,radius:rad*s,height:h*s,color,segments:48});
  }
  local([47,3,47.5],[12,9,1.6],C.green,1);
  for(const x of [-44,-22,0,22,44])local([x,31.8,0],[3,1.4,45],'#93aab5',.5);
  return {center:p,lens:transformPoint([0,-63,0],parent)};
}

function addChip(scene,options={}){
  const p=options.position||[0,0,0],s=options.scale||1,r=options.rotation||[0,options.angle||0,0],parent={position:p,rotation:r,scale:s};
  const local=(q,size,color,rad=4)=>addPlate(scene,{position:transformPoint(q,parent),size:size.map(x=>x*s),rotation:r,color,radius:rad*s,segments:2});
  local([0,0,0],[250,14,206],options.color||C.blue,9);
  local([0,18,0],[118,22,106],'#354d59',5);
  local([0,30,0],[101,2,88],'#d0e0e5',1.2);
  for(let i=-3;i<=3;i++)for(const sign of [-1,1]){
    local([i*14,13,sign*65],[6,5,19],'#bbcdd3',1);
    local([sign*70,13,i*13],[18,5,6],'#bbcdd3',1);
  }
  for(const x of [-100,100])for(const z of [-78,78])addCylinder(scene,{position:transformPoint([x,8,z],parent),rotation:r,radius:4*s,height:1*s,color:'#d4e3e7',segments:16});
  return {center:transformPoint([0,32,0],parent)};
}

function addTube(scene,options={}){
  const points=options.points||[[-200,0,0],[-90,60,0],[90,-60,0],[200,0,0]],radius=options.radius||4,segments=options.segments||48,sides=options.sides||10;
  const mesh=cached('tube'+JSON.stringify([points,radius,segments,sides]),()=>{
    const b=meshBuilder(),rings=[];let previous=[0,1,0];
    for(let i=0;i<=segments;i++){
      const t=i/segments,u=1-t,p=[0,1,2].map(k=>u*u*u*points[0][k]+3*u*u*t*points[1][k]+3*u*t*t*points[2][k]+t*t*t*points[3][k]);
      const tangent=unit([0,1,2].map(k=>3*u*u*(points[1][k]-points[0][k])+6*u*t*(points[2][k]-points[1][k])+3*t*t*(points[3][k]-points[2][k])));
      if(Math.abs(dot(previous,tangent))>.98)previous=[1,0,0];
      const n=unit(sub(previous,mul(tangent,dot(previous,tangent)))),v=cross(tangent,n);rings.push({p,n,v});previous=n;
    }
    b.patch(segments,sides,(u,v)=>{const q=rings[Math.round(u*segments)],a=v*Math.PI*2,n=add(mul(q.n,Math.cos(a)),mul(q.v,Math.sin(a)));return [...add(q.p,mul(n,radius)),...n];});
    return b.finish();
  });
  return instance(scene,mesh,options,MAT.green);
}

function addContactShadow(scene,options={}){
  const radius=options.radius||[160,90],alpha=options.opacity===undefined?.17:options.opacity;
  const mesh=cached('shadow'+JSON.stringify([radius,alpha]),()=>{
    const b=meshBuilder();b.patch(8,48,(u,v)=>{const a=v*Math.PI*2;return [Math.cos(a)*radius[0]*u,0,Math.sin(a)*radius[1]*u,0,1,0,alpha*(Math.exp(-u*u*4.6)-Math.exp(-4.6))/(1-Math.exp(-4.6))];});return b.finish();
  });
  const o={mesh,position:options.position||[0,0,0],rotation:options.rotation||[0,options.angle||0,0],scale:options.scale||1,material:{color:'#315261',shadow:true}};
  scene.shadows.push(o);return o;
}

function background(g,options={}){
  const w=options.width||1920,h=options.height||1080;
  g.fillStyle=options.color||C.bg;g.fillRect(0,0,w,h);
  const light=g.createRadialGradient(w*.34,h*.12,w*.02,w*.43,h*.3,w*.79);
  light.addColorStop(0,'rgba(255,255,255,.31)');light.addColorStop(.65,'rgba(255,255,255,.06)');light.addColorStop(1,'rgba(71,116,143,.035)');
  g.fillStyle=light;g.fillRect(0,0,w,h);
}

// Opaque geometry first, then depth-tested translucent receiver shadows.
function render(g,scene,options={}){
  const x=options.x||0,y=options.y||0,w=options.w||scene.camera.width,h=options.h||scene.camera.height;
  const aa=options.quality===undefined?1.5:options.quality,W=Math.ceil(w*aa),H=Math.ceil(h*aa),key=W+'x'+H;
  let surface=surfaceCache.get(key);
  if(!surface){const canvas=createCanvas(W,H),ctx=canvas.getContext('2d'),image=ctx.createImageData(W,H);surface={canvas,ctx,image,pixels:new Uint32Array(image.data.buffer),depth:new Float32Array(W*H)};surfaceCache.set(key,surface);}
  surface.pixels.fill(0);surface.depth.fill(0);
  const pixels=surface.pixels,depth=surface.depth,bytes=surface.image.data,c={...scene.camera,width:W,height:H},b=cameraBasis(c),light=unit([-.48,.82,.48]),fill=unit([.65,.25,-.72]);
  let triangles=0,visibleTriangles=0;
  for(const o of [...scene.objects,...scene.shadows]){
    const v=o.mesh.v,ind=o.mesh.indices,n=v.length/7,out=new Float32Array(n*7),m=rotation(o.rotation),s=o.scale,p=o.position,mat=o.material,col=hex(mat.color||C.silver),shadow=mat.shadow;
    for(let i=0;i<n;i++){
      const a=i*7,px=v[a]*s,py=v[a+1]*s,pz=v[a+2]*s;
      const X=m[0]*px+m[1]*py+m[2]*pz+p[0],Y=m[3]*px+m[4]*py+m[5]*pz+p[1],Z=m[6]*px+m[7]*py+m[8]*pz+p[2];
      const dx=X-c.eye[0],dy=Y-c.eye[1],dz=Z-c.eye[2],z=dx*b.f[0]+dy*b.f[1]+dz*b.f[2],iz=1/z;
      out[a]=b.cx+(dx*b.r[0]+dy*b.r[1]+dz*b.r[2])*b.focal*iz;out[a+1]=b.cy-(dx*b.u[0]+dy*b.u[1]+dz*b.u[2])*b.focal*iz;out[a+2]=iz;
      let shade=1,spec=0;
      if(!shadow){
        const nx=m[0]*v[a+3]+m[1]*v[a+4]+m[2]*v[a+5],ny=m[3]*v[a+3]+m[4]*v[a+4]+m[5]*v[a+5],nz=m[6]*v[a+3]+m[7]*v[a+4]+m[8]*v[a+5];
        const dl=Math.max(0,nx*light[0]+ny*light[1]+nz*light[2]),df=Math.max(0,nx*fill[0]+ny*fill[1]+nz*fill[2]);
        const vd=1/Math.hypot(dx,dy,dz),hx=light[0]-dx*vd,hy=light[1]-dy*vd,hz=light[2]-dz*vd,hl=1/Math.hypot(hx,hy,hz),nh=Math.max(0,(nx*hx+ny*hy+nz*hz)*hl),metal=mat.metal||0;
        const nv=Math.max(0,-(nx*dx+ny*dy+nz*dz)*vd);
        // Large key + frontal fill + white studio reflections. Silver stays silver
        // on turning surfaces; a broad highlight moves continuously across a bend.
        shade=.68+.28*dl+.13*df+.06*Math.max(0,ny)+.035*nv;
        spec=Math.pow(nh,14+(1-(mat.roughness||.4))*27)*(.11+.26*metal)+Math.pow(nh,4)*(.025+.045*metal)+Math.pow(1-nv,3)*(.018+.035*metal);
      }
      out[a+3]=Math.min(255,col[0]*shade+255*spec)*iz;out[a+4]=Math.min(255,col[1]*shade+255*spec)*iz;out[a+5]=Math.min(255,col[2]*shade+255*spec)*iz;out[a+6]=v[a+6]*iz;
    }
    for(let q=0;q<ind.length;q+=3){
      triangles++;const i0=ind[q]*7,i1=ind[q+1]*7,i2=ind[q+2]*7,x0=out[i0],y0=out[i0+1],x1=out[i1],y1=out[i1+1],x2=out[i2],y2=out[i2+1];
      const area=(x1-x0)*(y2-y0)-(y1-y0)*(x2-x0);if(area>=-.00001||out[i0+2]<=0||out[i1+2]<=0||out[i2+2]<=0)continue;
      const minX=Math.max(0,Math.ceil(Math.min(x0,x1,x2)-.5)),maxX=Math.min(W-1,Math.floor(Math.max(x0,x1,x2)-.5)),minY=Math.max(0,Math.ceil(Math.min(y0,y1,y2)-.5)),maxY=Math.min(H-1,Math.floor(Math.max(y0,y1,y2)-.5));if(minX>maxX||minY>maxY)continue;
      visibleTriangles++;
      const a0=(y1-y2)/area,b0=(x2-x1)/area,c0=(x1*y2-x2*y1)/area,a1=(y2-y0)/area,b1=(x0-x2)/area,c1=(x2*y0-x0*y2)/area;
      const iz0=out[i0+2],iz1=out[i1+2],iz2=out[i2+2],dz0=iz0-iz2,dz1=iz1-iz2;
      const rr0=out[i0+3]-out[i2+3],rr1=out[i1+3]-out[i2+3],gg0=out[i0+4]-out[i2+4],gg1=out[i1+4]-out[i2+4],bb0=out[i0+5]-out[i2+5],bb1=out[i1+5]-out[i2+5];
      const aa0=out[i0+6]-out[i2+6],aa1=out[i1+6]-out[i2+6];
      for(let py=minY;py<=maxY;py++){
        let w0=a0*(minX+.5)+b0*(py+.5)+c0,w1=a1*(minX+.5)+b1*(py+.5)+c1,index=py*W+minX;
        for(let px=minX;px<=maxX;px++,index++,w0+=a0,w1+=a1){
          if(w0<-.000001||w1<-.000001||w0+w1>1.000001)continue;
          const iz=iz2+w0*dz0+w1*dz1;if(iz<depth[index]-1e-9)continue;
          const z=1/iz,R=(out[i2+3]+w0*rr0+w1*rr1)*z,G=(out[i2+4]+w0*gg0+w1*gg1)*z,B=(out[i2+5]+w0*bb0+w1*bb1)*z;
          if(!shadow){depth[index]=iz;pixels[index]=(255<<24)|((B+.5)<<16)|((G+.5)<<8)|(R+.5);}
          else{
            const alpha=(out[i2+6]+w0*aa0+w1*aa1)*z;if(alpha<.002)continue;
            const j=index*4,oldA=bytes[j+3]/255,nextA=alpha+oldA*(1-alpha),k=oldA*(1-alpha);
            bytes[j]=(R*alpha+bytes[j]*k)/nextA;bytes[j+1]=(G*alpha+bytes[j+1]*k)/nextA;bytes[j+2]=(B*alpha+bytes[j+2]*k)/nextA;bytes[j+3]=nextA*255;
          }
        }
      }
    }
  }
  surface.ctx.putImageData(surface.image,0,0);
  g.save();g.beginPath();g.rect(x,y,w,h);g.clip();g.imageSmoothingEnabled=true;g.imageSmoothingQuality='high';g.drawImage(surface.canvas,x,y,w,h);g.restore();
  return {triangles,visibleTriangles,width:W,height:H};
}

function hero(scene,t=0,variant=0,options={}){
  const v=variant%4,turn=.055*Math.sin(t*.9),energy=options.energy===undefined?1:options.energy;
  // Each chapter is an independently arranged set of open, non-concentric ribbons.
  // Different z lanes keep the sculptures physically separated, including during
  // their short staggered settling motion. They are a material study, not hardware.
  const designs=[
    [
      {points:[[-330,-70,0],[-235,120,-8],[-90,165,12],[75,-90,3],[235,-55,-10],[335,90,0]],width:170,twist:[-.38,.52,.55]},
      {points:[[-310,-90,178],[-180,25,183],[-30,45,175],[120,-95,180],[290,45,175]],width:88,twist:[.58,-.35,-.48]},
      {points:[[-300,-150,-185],[-185,-20,-180],[-45,45,-178],[95,-65,-180],[275,75,-185]],width:116,twist:[-.50,.40,.40]}
    ],
    [
      {points:[[-310,-115,0],[-245,60,-8],[-90,175,0],[85,175,5],[255,95,0],[330,-35,0]],width:178,twist:[-.60,.68,.15]},
      {points:[[-280,-145,190],[-155,-60,185],[15,115,190],[190,120,185],[300,30,185]],width:82,twist:[.65,-.60,.10]},
      {points:[[-300,-105,-190],[-160,35,-185],[20,65,-180],[180,-50,-185],[315,-105,-190]],width:128,twist:[-.6,.2,.50]}
    ],
    [
      {points:[[-320,-125,0],[-190,25,-5],[-85,185,0],[55,180,5],[205,-5,0],[320,-55,-8]],width:164,twist:[.50,-.70,.50]},
      {points:[[-285,55,178],[-160,-90,185],[-10,-115,178],[145,20,183],[300,110,178]],width:94,twist:[-.55,.65,-.3]},
      {points:[[-310,-100,-190],[-150,-145,-185],[25,-35,-188],[170,90,-182],[300,105,-185]],width:120,twist:[.35,-.60,.15]}
    ],
    [
      {points:[[-320,85,0],[-205,155,0],[-60,95,-8],[90,-120,0],[245,-90,6],[330,40,0]],width:182,twist:[-.65,.65,.40]},
      {points:[[-300,0,190],[-145,55,180],[20,0,190],[155,-65,185],[300,105,185]],width:86,twist:[.60,-.45,-.15]},
      {points:[[-285,-135,-190],[-140,-55,-185],[25,95,-190],[180,35,-185],[310,-60,-185]],width:112,twist:[-.40,.50,.50]}
    ]
  ];
  for(let i=0;i<3;i++){
    const entry=smooth((t-i*.035)/(.34+energy*.045)),d=designs[v][i],colors=['#edf2f1','#52c446','#6096bc'];
    addSweep(scene,{...d,thickness:i===0?10:8,position:[(i-1)*12*(1-entry),35+42*(1-entry)+3*Math.sin(t*.8+i),0],rotation:[0,turn,-.045+(1-entry)*.075],material:{color:colors[i],metal:i===0?.72:.12,roughness:i===0?.34:.48}});
  }
  addContactShadow(scene,{position:[0,-182,0],radius:[465,335],opacity:.15});
  return {center:[0,35,0]};
}

function fit(scene,bounds,padding=.88){
  const c=scene.camera;let fov=c.fov;
  for(let i=0;i<2;i++){
    c.fov=fov;const ps=bounds.map(p=>project(p,c));let xmin=Infinity,xmax=-Infinity,ymin=Infinity,ymax=-Infinity;
    for(const p of ps){xmin=Math.min(xmin,p.x);xmax=Math.max(xmax,p.x);ymin=Math.min(ymin,p.y);ymax=Math.max(ymax,p.y);}
    const ratio=Math.max((xmax-xmin)/(c.width*padding),(ymax-ymin)/(c.height*padding));fov=2*Math.atan(Math.tan(fov*Math.PI/360)*ratio)*180/Math.PI;
  }
  c.fov=fov;
}
function boundsBox(size,center=[0,0,0]){const a=[];for(const x of [-.5,.5])for(const y of [-.5,.5])for(const z of [-.5,.5])a.push([center[0]+x*size[0],center[1]+y*size[1],center[2]+z*size[2]]);return a;}

function drawModel(g,kind,options={}){
  const x=options.x||0,y=options.y||0,w=options.w||960,h=options.h||640,t=options.t||0,progress=clamp(options.progress===undefined?.5:options.progress),variant=options.variant||0;
  const scene=createScene({width:w,height:h}),drift=.045*Math.sin(t*.65),turn=.12*smooth(t/.65);
  scene.camera.eye=[1050+110*Math.sin(drift),800,1500];
  let bounds,marks={};
  if(kind==='hero'||kind==='ribbon'){
    hero(scene,t,variant,options);scene.camera.eye=[650,760,1670];scene.camera.target=[0,25,0];bounds=boundsBox([770,460,580],[0,25,0]);
  }else if(kind==='resistor'){
    const top=options.view==='top',p=[top?mix(-150,150,progress):0,22,0];
    marks.objectData=addResistor(scene,{position:p,rotation:[0,top?0:-.12+turn,0],scale:1,marking:options.marking});marks.object=marks.objectData.top;
    addContactShadow(scene,{position:[p[0],.05,0],radius:[130,76],opacity:.22});
    bounds=boundsBox(top?[540,40,210]:[230,80,160],[0,20,0]);scene.camera.target=[0,20,0];
    if(top){scene.camera.eye=[0,1200,0];scene.camera.up=[0,0,-1];}
  }else if(kind==='camera'){
    marks.camera=addCamera(scene,{position:[0,40,0],rotation:[0,.18+turn,0],scale:2.2}).center;
    scene.camera.eye=[1000,620,1400];scene.camera.target=[0,0,0];bounds=boundsBox([370,300,300],[0,0,0]);
    addContactShadow(scene,{position:[0,-145,0],radius:[270,200],opacity:.13});
  }else if(kind==='chip'){
    marks.center=addChip(scene,{position:[0,0,0],rotation:[0,-.12+turn,0],color:options.color||[C.blue,C.green,C.blue,C.silver][variant%4]}).center;
    bounds=boundsBox([330,90,280],[0,25,0]);scene.camera.target=[0,15,0];
    addContactShadow(scene,{position:[0,-7.1,0],radius:[220,180],opacity:.18});
  }else if(kind==='plates'){
    const gap=options.gap===undefined?58:options.gap;
    for(let i=0;i<3;i++)addPlate(scene,{position:[(i-1)*30,(i-1)*gap,0],rotation:[0,-.15+turn,0],size:[360,10,240],radius:4,color:[C.blue,C.green,C.silver][(i+variant)%3]});
    addContactShadow(scene,{position:[0,-gap-10,0],radius:[300,240],opacity:.15});bounds=boundsBox([490,260,370]);scene.camera.target=[0,0,0];
  }else if(kind==='tracking'||kind==='trigger'){
    // A single camera observes one continuous transport surface and ONE object.
    addPlate(scene,{position:[0,-9,0],size:[1040,18,252],radius:8,color:'#dde9ed'});
    const ox=mix(-385,385,progress);marks.objectData=addResistor(scene,{position:[ox,13.5,10],scale:.84,rotation:[0,-.035,0]});marks.object=marks.objectData.top;
    addContactShadow(scene,{position:[ox,.15,10],radius:[100,58],opacity:.24});
    addContactShadow(scene,{position:[0,-18.2,0],radius:[670,245],opacity:.13});
    marks.planeCorners=[[-520,0,-126],[520,0,-126],[520,0,126],[-520,0,126]];
    marks.triggerLine=[[0,1,-117],[0,1,117]];
    if(kind==='tracking')marks.camera=addCamera(scene,{position:[0,320,-25],scale:1.13}).center;
    scene.camera.eye=[650,900,1600];scene.camera.target=[0,95,0];bounds=boundsBox([1190,430,430],[0,145,0]);
    if(kind==='trigger'){scene.camera.target=[0,5,0];bounds=boundsBox([1160,100,380],[0,10,0]);}
  }else if(kind==='dimensions'){
    marks.objectData=addResistor(scene,{position:[0,20,0],rotation:[0,options.angle||0,0],scale:options.scale||1});marks.object=marks.objectData.top;
    addContactShadow(scene,{position:[0,0,0],radius:[125,73],opacity:.19});scene.camera.eye=[0,1200,0];scene.camera.up=[0,0,-1];scene.camera.target=[0,0,0];bounds=boundsBox([230,40,145]);
  }else if(kind==='alerts'){
    marks.center=addChip(scene,{position:[0,0,0],color:C.blue}).center;
    for(let i=0;i<3;i++)addPlate(scene,{position:[170+i*18,65+i*30,-25-i*8],size:[110,7,80],radius:3,color:i===2?C.green:C.silver,rotation:[0,-.15,0]});
    bounds=boundsBox([560,250,340],[70,50,0]);scene.camera.target=[60,40,0];addContactShadow(scene,{position:[40,-10,0],radius:[330,215],opacity:.16});
  }else throw new Error('Unknown B model kind: '+kind);
  if(options.eye)scene.camera.eye=options.eye;if(options.target)scene.camera.target=options.target;
  fit(scene,bounds,options.padding||.88);
  const stats=render(g,scene,{x,y,w,h,quality:options.quality});
  const toScreen=p=>{const q=project(p,scene.camera);return {x:q.x+x,y:q.y+y,z:q.z};},landmarks={};
  for(const [key,value] of Object.entries(marks))if(key!=='objectData')landmarks[key]=Array.isArray(value[0])?value.map(toScreen):toScreen(value);
  if(marks.objectData){const ps=marks.objectData.corners.map(toScreen),xs=ps.map(p=>p.x),ys=ps.map(p=>p.y);landmarks.bounds={x:Math.min(...xs),y:Math.min(...ys),w:Math.max(...xs)-Math.min(...xs),h:Math.max(...ys)-Math.min(...ys)};}
  return {landmarks,project:toScreen,scene,stats};
}

module.exports={C,MAT,smooth,mix,createScene,background,render,project,addRibbon,addSweep,addPlate,addResistor,addCamera,addChip,addTube,addContactShadow,hero,drawModel};

async function qa(){
  const fs=require('node:fs'),path=require('node:path'),out=path.join(__dirname,'geometry_qa');fs.mkdirSync(out,{recursive:true});
  const c=createCanvas(1920,1080),g=c.getContext('2d');
  const cases=[['hero',.2,0],['hero',1.2,1],['hero',3.6,2],['tracking',.2,.12],['tracking',1.5,.5],['tracking',2.8,.88],['resistor',1,.5],['camera',1,.5],['chip',1,.5],['plates',1,.5],['hero',.6,3],['trigger',1,.5]];
  const report=[];
  for(let i=0;i<cases.length;i++){
    const [kind,t,p]=cases[i];background(g);const start=performance.now();const r=drawModel(g,kind,{x:120,y:90,w:1680,h:900,t,progress:p,variant:kind==='hero'?p:0,quality:1.5});
    const ms=performance.now()-start,file=`${String(i).padStart(2,'0')}_${kind}_${t}.png`;fs.writeFileSync(path.join(out,file),c.toBuffer('image/png'));report.push({kind,t,file,ms,...r.stats,landmarks:r.landmarks});
  }
  fs.writeFileSync(path.join(out,'qa_report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report.map(r=>({kind:r.kind,ms:Math.round(r.ms),triangles:r.triangles})),null,2));
  // Order reversal is a direct regression check for the old painter-sorting bug.
  const probe=createCanvas(960,640),pg=probe.getContext('2d'),scene=createScene({width:960,height:640});
  hero(scene,.7,2);render(pg,scene,{w:960,h:640,quality:1.5});const reference=Buffer.from(probe.data());
  pg.clearRect(0,0,960,640);scene.objects.reverse();render(pg,scene,{w:960,h:640,quality:1.5});const reversed=Buffer.from(probe.data());
  let orderPixels=0;for(let i=0;i<reference.length;i+=4)if(reference[i]!==reversed[i]||reference[i+1]!==reversed[i+1]||reference[i+2]!==reversed[i+2]||reference[i+3]!==reversed[i+3])orderPixels++;
  const checks={depth_order_reversal_changed_pixels:orderPixels,depth_order_independent:orderPixels===0,stills:report.length};
  if(process.argv.includes('--motion')){
    const {spawn}=require('node:child_process'),{once}=require('node:events'),crypto=require('node:crypto');
    const ff=path.resolve(__dirname,'../../dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe'),file=path.join(out,'geometry_motion_60fps.mp4'),log=fs.openSync(path.join(out,'geometry_motion_render.log'),'w');
    const proc=spawn(ff,['-hide_banner','-loglevel','warning','-y','-f','rawvideo','-pix_fmt','rgba','-s','1920x1080','-r','60','-i','pipe:0','-an','-c:v','h264_nvenc','-preset','p5','-cq','17','-b:v','0','-pix_fmt','yuv420p','-video_track_timescale','60000','-frames:v','360','-movflags','+faststart',file],{windowsHide:true,stdio:['pipe','ignore',log]});
    const done=new Promise(resolve=>proc.on('close',resolve)),hashes=new Set(),ms=[];let previous,duplicates=0;
    for(let frame=0;frame<360;frame++){
      const t=frame/60,start=performance.now();background(g);
      drawModel(g,'hero',{x:20,y:120,w:1060,h:850,t,variant:0,quality:1.5});
      drawModel(g,'tracking',{x:1040,y:200,w:850,h:650,t,progress:smooth(t/5.6),quality:1.5});
      ms.push(performance.now()-start);const rgba=c.data(),hash=crypto.createHash('sha256').update(rgba).digest('hex');hashes.add(hash);if(hash===previous)duplicates++;previous=hash;
      if(!proc.stdin.write(rgba))await once(proc.stdin,'drain');
    }
    proc.stdin.end();if(await done!==0)throw new Error('Geometry motion encode failed');fs.closeSync(log);
    checks.motion={file,width:1920,height:1080,fps:60,frames:360,unique_frame_hashes:hashes.size,adjacent_repeated_frames:duplicates,mean_draw_ms:ms.reduce((a,b)=>a+b,0)/ms.length,p95_draw_ms:ms.sort((a,b)=>a-b)[Math.floor(ms.length*.95)]};
  }
  fs.writeFileSync(path.join(out,'geometry_checks.json'),JSON.stringify(checks,null,2));console.log(JSON.stringify(checks,null,2));
}
if(require.main===module)qa().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;});
