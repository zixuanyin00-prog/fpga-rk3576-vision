'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {spawn}=require('node:child_process'),{once}=require('node:events');
const {createCanvas,GlobalFonts}=require('../../dataflow_film_20261028/node_modules/@napi-rs/canvas');
for(const [f,n] of [['msyh.ttc','CJK'],['msyhbd.ttc','CJK Heavy'],['bahnschrift.ttf','DIN'],['consola.ttf','Mono'],['simhei.ttf','Heiti'],['simsun.ttc','Songti'],['impact.ttf','Impact']])GlobalFonts.registerFromPath('C:/Windows/Fonts/'+f,n);
const P=__dirname,ff=path.resolve(P,'../../dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe');
const scenes=require('./scenes_b_v26.cjs'),plan=JSON.parse(fs.readFileSync(path.join(P,'render_plan.json'),'utf8'));
const selection=process.argv[2]||'all',stills=process.argv.includes('--stills');
const requested=selection==='all'?plan:plan.filter(s=>s.name===selection);
if(!requested.length)throw Error('Unknown scene '+selection);
fs.mkdirSync(path.join(P,'scenes'),{recursive:true});fs.mkdirSync(path.join(P,'previews'),{recursive:true});
const sourceHash=crypto.createHash('sha256').update(fs.readFileSync(path.join(P,'../重剪_v21/geometry_b.cjs'))).update(fs.readFileSync(path.join(P,'../重剪_v21/scenes_b.cjs'))).update(fs.readFileSync(path.join(P,'scenes_b_v26.cjs'))).digest('hex');
const canvas=createCanvas(1920,1080),g=canvas.getContext('2d');
(async()=>{
 await scenes.init();
 if(scenes.writeManifest)scenes.writeManifest();
 for(const s of requested){
  const out=path.join(P,'scenes',s.name+'.mp4'),marker=out+'.render.json',frames=Math.round(s.duration*60);
  const signature={sourceHash,name:s.name,frames,width:1920,height:1080,fps:60};
  if(!stills&&fs.existsSync(marker)&&fs.existsSync(out)&&JSON.stringify(JSON.parse(fs.readFileSync(marker)).signature)===JSON.stringify(signature)){console.log('reuse '+s.name);continue;}
  for(const [label,f] of [['early',.18],['middle',.5],['late',.87]]){
   scenes.draw(g,s.name,s.duration*f,s.duration);
   fs.writeFileSync(path.join(P,'previews',s.name+'_'+label+'.png'),canvas.toBuffer('image/png'));
  }
  if(stills){console.log('stills '+s.name);continue;}
  const fd=fs.openSync(out+'.log','w'),proc=spawn(ff,['-hide_banner','-loglevel','warning','-y','-f','rawvideo','-pix_fmt','rgba','-s','1920x1080','-r','60','-i','pipe:0','-an','-c:v','h264_nvenc','-preset','p5','-cq','17','-b:v','0','-pix_fmt','yuv420p','-video_track_timescale','60000','-frames:v',String(frames),'-movflags','+faststart',out],{windowsHide:true,stdio:['pipe','ignore',fd]});
  const done=new Promise(resolve=>proc.on('close',resolve));
  const began=Date.now();let previous='',equal=0,longest=0,run=0;
  for(let frame=0;frame<frames;frame++){
   scenes.draw(g,s.name,frame/60,s.duration);
   const data=canvas.data(),hash=crypto.createHash('sha1').update(data).digest('hex');
   if(hash===previous){equal++;run++;longest=Math.max(run,longest);}else run=0;
   previous=hash;
   if(!proc.stdin.write(data))await once(proc.stdin,'drain');
   if(frame&&frame%120===0)console.log(s.name+' '+frame+'/'+frames+' '+((Date.now()-began)/1000).toFixed(1)+'s');
  }
  proc.stdin.end();if(await done!==0)throw Error('FFmpeg failure '+s.name);fs.closeSync(fd);
  const report={signature,duration:frames/60,render_seconds:(Date.now()-began)/1000,adjacent_identical_frames:equal,longest_identical_run_frames:longest,animation_time_step_seconds:1/60,frame_generation:'independent draw at exact frame/60; no time quantization or intermediate video fps conversion'};
  fs.writeFileSync(marker,JSON.stringify(report,null,2));console.log('COMPLETE '+s.name+' '+report.render_seconds+'s');
 }
})().catch(e=>{process.stderr.write(e.stack+'\n');process.exitCode=1;});
