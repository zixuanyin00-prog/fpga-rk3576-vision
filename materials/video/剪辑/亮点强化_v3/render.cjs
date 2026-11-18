const fs=require('node:fs'),path=require('node:path'),{spawn}=require('node:child_process'),{once}=require('node:events');
const {createCanvas,GlobalFonts}=require('../../dataflow_film_20261028/node_modules/@napi-rs/canvas');
GlobalFonts.registerFromPath('C:/Windows/Fonts/msyh.ttc','Microsoft YaHei');GlobalFonts.registerFromPath('C:/Windows/Fonts/msyhbd.ttc','Microsoft YaHei');
const film=require('./highlights.js'),canvas=createCanvas(1920,1080),g=canvas.getContext('2d');
async function main(){for(const part of ['parameter','async','tracking','small'].filter(x=>!process.argv.includes('--part')||x===process.argv[process.argv.indexOf('--part')+1])){
 film.draw(g,part==='small'?3:18,1920,1080,part);fs.writeFileSync(path.join(__dirname,part+'.png'),canvas.toBuffer('image/png'));
 if(process.argv.includes('--stills'))continue;
 const ffmpeg=path.resolve(__dirname,'../../dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe');
 const args=['-hide_banner','-loglevel','warning','-y','-f','rawvideo','-pix_fmt','rgba','-s','1920x1080','-r','60','-i','pipe:0','-an','-c:v','h264_nvenc','-preset','p5','-rc','vbr','-cq','20','-b:v','0','-pix_fmt','yuv420p','-video_track_timescale','60000','-t',String(film.durations[part]),path.join(__dirname,part+'.mp4')];
 const p=spawn(ffmpeg,args,{stdio:['pipe','ignore','pipe'],windowsHide:true});p.stderr.on('data',d=>process.stderr.write(d));p.stdin.on('error',e=>console.error(e.message));const done=new Promise(r=>p.on('close',r));
 for(let n=0;n<film.durations[part]*60;n++){film.draw(g,n/60,1920,1080,part);if(!p.stdin.write(canvas.data()))await once(p.stdin,'drain');if(n%600===0)console.log(part,n)}
 p.stdin.end();const code=await done;if(code!==0)throw new Error(part+' failed '+code);console.log(part+' complete');
}}
main().catch(e=>{console.error(e);process.exitCode=1});

