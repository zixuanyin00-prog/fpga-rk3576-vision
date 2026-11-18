const fs=require('node:fs'),path=require('node:path'),{spawn}=require('node:child_process'),{once}=require('node:events');
const {createCanvas,GlobalFonts}=require('../../dataflow_film_20261028/node_modules/@napi-rs/canvas');
GlobalFonts.registerFromPath('C:/Windows/Fonts/msyh.ttc','Microsoft YaHei');
const film=require('./animations_v9.cjs');
const ffmpeg=path.resolve(__dirname,'../../dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe');
async function main(){
 for(const [part,duration] of Object.entries(film.durations)){
  if(process.argv.includes('--part')&&part!==process.argv[process.argv.indexOf('--part')+1])continue;
  const out=path.join(__dirname,part+'.mp4');if(fs.existsSync(out)&&!process.argv.includes('--force'))continue;
  const canvas=createCanvas(1920,1080),g=canvas.getContext('2d');
  const args=['-hide_banner','-loglevel','warning','-y','-f','rawvideo','-pix_fmt','rgba','-s','1920x1080','-r','60','-i','pipe:0','-vf','ass='+part+'.ass','-an','-c:v','h264_nvenc','-preset','p5','-cq','19','-b:v','0','-pix_fmt','yuv420p','-video_track_timescale','60000','-t',String(duration),out];
  const p=spawn(ffmpeg,args,{cwd:__dirname,stdio:['pipe','ignore','pipe'],windowsHide:true});
  p.stderr.on('data',d=>process.stderr.write(d));p.stdin.on('error',e=>console.error(e.message));const done=new Promise(r=>p.on('close',r));
  for(let n=0;n<duration*60;n++){
   film.draw(g,n/60,1920,1080,part);
   if(!p.stdin.write(canvas.data()))await once(p.stdin,'drain');
   if(n%600===0)console.log(part,n);
  }
  p.stdin.end();const code=await done;if(code!==0)throw new Error(part+' '+code);console.log(part+' complete');
 }
}
main().catch(e=>{console.error(e);process.exitCode=1});
