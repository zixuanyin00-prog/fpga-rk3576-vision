from pathlib import Path
import subprocess,ast,json,wave,math
import numpy as np
P=Path(__file__).resolve().parent;M=P.parent;ROOT=M.parent
FF=str(ROOT/'dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe')
tree=ast.parse((M/'全片_v2/render_full.py').read_text(encoding='utf-8-sig'))
header=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(x,ast.Name) and x.id=='header' for x in n.targets))
def run(a):
 with (P/'build.log').open('a') as f:subprocess.run([FF,'-y','-hide_banner','-nostdin',*a],cwd=P,stdout=f,stderr=f,check=True)
def shot(name,src,start,dur,caption='',vf=None):
 ass=header
 if caption:ass+=f'Dialogue: 0,0:00:00.50,0:00:{dur-.4:05.2f},Default,,0,0,0,,'+caption.replace('\n',r'\N')+'\n'
 (P/(name+'.ass')).write_text(ass,encoding='utf-8-sig')
 vf=vf or 'scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080'
 if caption:vf+=',drawbox=x=0:y=938:w=iw:h=142:color=black@0.22:t=fill,ass='+name+'.ass'
 run(['-ss',str(start),'-i',str(src),'-t',str(dur),'-vf',vf+',setsar=1,fps=60','-an','-c:v','h264_nvenc','-preset','p5','-cq','20','-b:v','0','-pix_fmt','yuv420p','-video_track_timescale','60000',name+'.mp4'])
shot('title',M/'全片_v2/00.mp4',0,6)
shot('board',M/'板卡移到振动盘.MOV',0,7,'用 FPGA 处理现场图像，\n让工件的位置与姿态变成可用的识别信息。')
shot('objects',M/'电阻姿态展示.MOV',1,9,'以电阻为例，\n系统关注工件的位置、朝向与姿态。')
console=next(M.glob('控制台全流程*.mp4'))
shot('training',console,487,12,'采集与标注完成后训练模型，\n再确认本次使用的训练版本。','crop=2560:1180:0:0,scale=1800:830,pad=1920:1080:60:25:color=0x11202a')
shot('hardware',M/'电磁阀和转接板展示.MOV',0,8,'识别结果通过外接控制接口，\n连接电磁阀等现场执行设备。','setpts=2.85*(PTS-STARTPTS),scale=1920:1080')
shot('area',M/'全片_v2/16.mp4',1,12)
shot('timing',M/'全片_v2/17.mp4',1,12)
shot('end',M/'全片_v2/19.mp4',0,6)
items=[('title',6),('board',7),('objects',9),('parameter',22),('auto',20),('training',12),('async',22),('tracking',30),('hardware',8),('area',12),('timing',12),('collage',28),('end',6)]
total=sum(d for _,d in items);offset=0;timeline=[]
for n,d in items:timeline.append({'start':offset,'duration':d,'file':n+'.mp4'});offset+=d
(P/'时间线.json').write_text(json.dumps(timeline,indent=2),encoding='utf8')
(P/'concat.txt').write_text(''.join("file '"+n+".mp4'\n" for n,d in items),encoding='utf8')
# New bright piano / mallet arrangement, major key, no dark pad or sub-bass.
sr=44100;beat=60/106;bar=beat*4;length=bar*16;N=round(length*sr);a=np.zeros((N,2),np.float32);rng=np.random.default_rng(11)
chords=[[293.665,369.994,440],[246.942,293.665,369.994],[195.998,293.665,391.995],[220,277.183,329.628]]
def note(at,f,amp,dur=1,pan=.5):
 at=round(at*sr);n=min(round(dur*sr),N-at)
 if n<=0:return
 t=np.arange(n)/sr;s=(np.sin(2*np.pi*f*t)+.34*np.sin(2*np.pi*2*f*t)*np.exp(-t*5)+.12*np.sin(2*np.pi*3*f*t))*np.exp(-t*4)*(1-np.exp(-t*200))*amp
 a[at:at+n,0]+=s*np.sqrt(1-pan);a[at:at+n,1]+=s*np.sqrt(pan)
for b in range(16):
 ch=chords[b%4]
 for bt in [0,1.5,3]:
  for j,f in enumerate(ch):note(b*bar+bt*beat+j*.018,f,.045,1.1,.25+j*.25)
 for j,k in enumerate([0,2,1,2]):note(b*bar+(.5+j*.5)*beat,ch[k]*2,.033,.7,.6)
 for bt in [1,3]:
  at=round((b*bar+bt*beat)*sr);n=min(int(sr*.12),N-at);t=np.arange(n)/sr;s=rng.normal(0,1,n)*np.exp(-t*48)*.012;a[at:at+n,:]+=s[:,None]
full=np.tile(a,(math.ceil(total/length)+1,1))[:total*sr];t=np.arange(len(full))/sr;full*=np.minimum(t/2,1)[:,None]*np.minimum((total-t)/3,1)[:,None]
with wave.open(str(P/'轻快钢琴打击乐_v4.wav'),'wb') as f:f.setnchannels(2);f.setsampwidth(2);f.setframerate(sr);f.writeframes((np.clip(full,-1,1)*32767).astype('<i2').tobytes())
run(['-f','concat','-safe','0','-i','concat.txt','-i','轻快钢琴打击乐_v4.wav','-map','0:v','-map','1:a','-c:v','h264_nvenc','-preset','p5','-cq','20','-b:v','0','-r','60','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-t',str(total),'-movflags','+faststart','FPGA视觉系统_重剪_v4.mp4'])
print(f'COMPLETE {total}s')

