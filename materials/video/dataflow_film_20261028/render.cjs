const fs=require('node:fs');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const {createCanvas,GlobalFonts}=require('@napi-rs/canvas');
const film=require('./renderer.js');
GlobalFonts.registerFromPath('C:/Windows/Fonts/msyh.ttc','Microsoft YaHei');
GlobalFonts.registerFromPath('C:/Windows/Fonts/msyhbd.ttc','Microsoft YaHei');
GlobalFonts.registerFromPath('C:/Windows/Fonts/consola.ttf','Consolas');
GlobalFonts.registerFromPath('C:/Windows/Fonts/bahnschrift.ttf','Bahnschrift');
const args=process.argv.slice(2);
const arg=(n,d)=>args.includes(n)?args[args.indexOf(n)+1]:d;
const width=Number(arg('--width',3840)),height=width*9/16,fps=Number(arg('--fps',60));
const canvas=createCanvas(width,height),g=canvas.getContext('2d');
fs.mkdirSync(path.join(__dirname,'output'),{recursive:true});
async function main(){
  if(args.includes('--stills')){
    const times=arg('--times','4,13,22,31,47,59,74,82,93').split(',').map(Number);
    const tic=performance.now();
    for(const t of times){film.draw(g,t,width,height);fs.writeFileSync(path.join(__dirname,'output',`frame-${String(t).padStart(5,'0')}.png`),canvas.toBuffer('image/png'));}
    console.log(JSON.stringify({stills:times,width,height,elapsed:((performance.now()-tic)/1000).toFixed(2)}));return;
  }
  const start=Number(arg('--start',0)),seconds=Number(arg('--seconds',film.DURATION)),frames=Math.round(seconds*fps);
  const name=arg('--output',`dataflow-v1-${height}p${fps}.mp4`);
  const out=path.join(__dirname,'output',name);
  const cmd=['-hide_banner','-loglevel','warning','-y','-f','rawvideo','-pix_fmt','rgba','-s',`${width}x${height}`,'-r',String(fps),'-i','pipe:0'];
  if(fs.existsSync(path.join(__dirname,'soundtrack.wav')))cmd.push('-ss',String(start),'-i',path.join(__dirname,'soundtrack.wav'));
  const encoder=arg('--encoder','libx264');
  if(encoder==='h264_nvenc')cmd.push('-c:v',encoder,'-preset','p5','-tune','hq','-rc','vbr','-cq','20','-b:v','0');
  else cmd.push('-c:v','libx264','-preset','fast','-crf','19');
  cmd.push('-profile:v','high','-pix_fmt','yuv420p','-g',String(fps*2));
  if(fs.existsSync(path.join(__dirname,'soundtrack.wav')))cmd.push('-c:a','aac','-b:a','192k');
  cmd.push('-t',String(seconds),'-movflags','+faststart','-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709','-metadata','title=一帧图像的数据旅程 · 第一版','-metadata','comment=Procedural architecture film. Three-stream offline candidate; not hardware acceptance evidence.',out);
  const proc=spawn(arg('--ffmpeg','ffmpeg'),cmd,{stdio:['pipe','ignore','pipe'],windowsHide:true});
  let errors='';proc.stderr.on('data',d=>{errors+=d.toString();process.stderr.write(d);});
  const exited=new Promise(resolve=>proc.on('close',resolve));
  proc.stdin.on('error',e=>console.error(e.message));
  const tic=performance.now();
  for(let n=0;n<frames;n++){
    film.draw(g,start+n/fps,width,height);
    const pixels=canvas.data();
    if(!proc.stdin.write(pixels))await once(proc.stdin,'drain');
    if(n%300===0){let elapsed=(performance.now()-tic)/1000;console.log(JSON.stringify({frame:n,total:frames,percent:(n/frames*100).toFixed(1),seconds:elapsed.toFixed(1),renderFps:n?+(n/elapsed).toFixed(1):0}));}
  }
  proc.stdin.end();const code=await exited;if(code!==0)throw new Error(`ffmpeg exited ${code}: ${errors}`);
  const report={file:out,width,height,fps,frames,duration:seconds,renderSeconds:+((performance.now()-tic)/1000).toFixed(1),source:'deterministic procedural Canvas',scope:'offline architecture illustration; no hardware or model executed'};
  fs.writeFileSync(out.replace('.mp4','.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}
main().catch(e=>{console.error(e);process.exitCode=1;});
