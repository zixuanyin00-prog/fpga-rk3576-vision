'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {spawn}=require('node:child_process'),{once}=require('node:events');
const {createCanvas,GlobalFonts}=require('../../dataflow_film_20261028/node_modules/@napi-rs/canvas');
const fonts=[['msyh.ttc','CJK'],['msyhbd.ttc','CJK Heavy'],['simhei.ttf','Heiti'],['simsun.ttc','Songti'],['bahnschrift.ttf','DIN'],['consola.ttf','Mono'],['impact.ttf','Impact']];
for(const [file,alias] of fonts)GlobalFonts.registerFromPath('C:/Windows/Fonts/'+file,alias);
const ff=path.resolve(__dirname,'../../dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe');
const name=process.argv[2];if(!['a','b','c'].includes(name))throw Error('Use a, b or c');
const file=path.join(__dirname,'style_'+name+'.cjs'),style=require(file),canvas=createCanvas(1920,1080),g=canvas.getContext('2d');
const moments=[['chapter',1.7],['transition',3.5],['observation',8.6],['confirmed',13.5]];
for(const [label,t] of moments){style.draw(g,t);fs.writeFileSync(path.join(__dirname,`style_${name}_preview_${label}.png`),canvas.toBuffer('image/png'));}
const fingerprint=crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
fs.writeFileSync(path.join(__dirname,`style_${name}_manifest.json`),JSON.stringify({source:file,sha256:fingerprint,width:1920,height:1080,fps:60,frames:960,duration:16,reference_assets_in_render:false,...style.meta},null,2));
if(process.argv.includes('--stills'))process.exit(0);
(async()=>{
 const output=path.join(__dirname,`style_${name}_silent.mp4`),log=fs.openSync(path.join(__dirname,`style_${name}_render.log`),'w');
 const proc=spawn(ff,['-hide_banner','-loglevel','warning','-y','-f','rawvideo','-pix_fmt','rgba','-s','1920x1080','-r','60','-i','pipe:0','-an','-c:v','h264_nvenc','-preset','p5','-cq','18','-b:v','0','-pix_fmt','yuv420p','-video_track_timescale','60000','-frames:v','960','-movflags','+faststart',output],{windowsHide:true,stdio:['pipe','ignore',log]});
 const done=new Promise(resolve=>proc.on('close',resolve));
 for(let frame=0;frame<960;frame++){style.draw(g,frame/60);if(!proc.stdin.write(canvas.data()))await once(proc.stdin,'drain');}
 proc.stdin.end();if(await done!==0)throw Error('Failed style '+name);fs.closeSync(log);console.log('Rendered '+output);
})().catch(err=>{process.stderr.write(err.stack+'\n');process.exitCode=1;});
