const fs=require('node:fs'),path=require('node:path'),{spawn}=require('node:child_process'),{once}=require('node:events');
const canvasModule=require('../../dataflow_film_20261028/node_modules/@napi-rs/canvas');
const {createCanvas,GlobalFonts}=canvasModule;
GlobalFonts.registerFromPath('C:/Windows/Fonts/msyh.ttc','Microsoft YaHei');
GlobalFonts.registerFromPath('C:/Windows/Fonts/msyhbd.ttc','Microsoft YaHei');
const film=require('./scene.js'),width=1920,height=1080,fps=60;
const canvas=createCanvas(width,height),g=canvas.getContext('2d');
async function main(){
 for(const t of [6,19,29,38]){film.draw(g,t,width,height);fs.writeFileSync(path.join(__dirname,`frame-${t}.png`),canvas.toBuffer('image/png'));}
 if(process.argv.includes('--stills'))return;
 const ffmpeg=path.resolve(__dirname,'../../dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe');
 const args=['-hide_banner','-loglevel','warning','-y','-f','rawvideo','-pix_fmt','rgba','-s',`${width}x${height}`,'-r',String(fps),'-i','pipe:0','-ss','9','-i',path.resolve(__dirname,'../全片_v2/明亮轻快配乐.wav'),'-map','0:v','-map','1:a','-c:v','h264_nvenc','-preset','p5','-rc','vbr','-cq','20','-b:v','0','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-af','afade=t=in:d=1.5,afade=t=out:st=39:d=3','-t',String(film.DURATION),'-movflags','+faststart',path.join(__dirname,'FPGA技术动画_数据流样片_v1.mp4')];
 const p=spawn(ffmpeg,args,{stdio:['pipe','ignore','pipe'],windowsHide:true});
 p.stderr.on('data',d=>process.stderr.write(d));
 const complete=new Promise(resolve=>p.on('close',resolve));
 p.stdin.on('error',e=>console.error(e.message));
 for(let n=0;n<film.DURATION*fps;n++){
  film.draw(g,n/fps,width,height);
  if(!p.stdin.write(canvas.data()))await once(p.stdin,'drain');
  if(n%300===0)console.log(`${n}/${film.DURATION*fps} frames`);
 }
 p.stdin.end();const code=await complete;if(code!==0)throw new Error(`ffmpeg ${code}`);
 console.log('COMPLETE');
}
main().catch(e=>{console.error(e);process.exitCode=1});
