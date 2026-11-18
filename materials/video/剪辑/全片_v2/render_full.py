from pathlib import Path
import subprocess, json, wave, math
import numpy as np

ROOT=Path(__file__).resolve().parent
MEDIA=ROOT.parent
CONSOLE=next(MEDIA.glob('控制台全流程*.mp4'))
W,H,FPS=1920,1080,60
shots=[]
def add(kind,name,start,length,duration,sub,**extra):
    shots.append(dict(kind=kind,name=name,start=start,length=length,duration=duration,sub=sub,**extra))

add('card','',0,8,8,[],title='基于 FPGA 的实时视觉处理\n与工件姿态识别系统',tag='小样本快速学习  ·  实时姿态识别  ·  外接控制')
add('video','板卡移到振动盘.MOV',0,14.36,17,[(1,15,'这是一套面向工业工件的\n实时视觉处理与姿态识别系统。')])
add('video','振动盘到HDMI.MOV',0,11.2,20,[(1,18,'这里以电阻为例，\n从现场运行画面，看到 HDMI 上的识别结果。')])
add('video','高速整体演示（HDMI）.MOV',9,18,18,[(1,16.5,'先定位工件，再识别姿态，\n识别结果与运行状态在 HDMI 屏幕上实时呈现。')])
add('parallel','',12,16,16,[(1,14.5,'系统根据识别结果自动触发电磁阀，\n将视觉判断用于现场工件筛选。')])
add('card','',0,5,5,[],title='从现场调参，到快速部署',tag='成像调整  /  采图标注  /  训练测试  /  部署运行')
add('sync','',20,32,32,[(1,15,'先通过控制台调整成像参数，\n同时观察现场 HDMI 画面的变化。'),(17,30.5,'控制台与实拍画面按同一时间轴展示，\n让调节过程和显示效果对应起来。')])
add('sync','',88,40,40,[(1,13,'FPGA 与 RK 之间建立双向控制通路，\n把图像处理参数接入现场操作界面。'),(14.5,26.5,'预览与推理异步运行，\n调试时可以持续观察画面与处理效果。'),(28,38.5,'完成成像调整后，\n固定本次训练采图使用的现场参数。')])
add('console','',140,35,24,[(1,11,'接下来采集当前场景的工件图像，\n准备本次训练使用的小样本数据。'),(12.5,23,'控制台操作按流程展示，\n重复操作适度加速，关键结果保留停留时间。')],speed=1.5)
add('console','',175,30,30,[(1,14,'在图像中标出目标位置，\n并为工件设置对应的姿态类别。'),(16,28.5,'同一界面完成样本查看与标注，\n把现场经验转化为可用于训练的数据。')])
add('console','',254,56,14,[(1,12.5,'逐张检查目标框与类别，\n重复标注过程已加速展示。')],speed=4)
add('console','',324,56,14,[(1,12.5,'补充不同位置和姿态的样本，\n让本次训练覆盖需要识别的工件状态。')],speed=4)
add('console','',487,23,28,[(1,13,'完成标注后，进行模型训练与转换。\n这里保留训练完成后的结果界面。'),(15,26.5,'模型按当前任务随用随训，\n在控制台明确选择本次使用的训练版本。')])
add('console','',510,32,25,[(1,12,'部署前先查看现场测试结果，\n检查工件位置与姿态判断是否符合预期。'),(13.5,23.5,'测试结果保留在控制台中，\n便于对照图像，逐项检查识别表现。')],speed=1.5)
add('video','部署后的运行结果.mp4',0,23.89,28,[(1,13,'部署后，直接在控制台查看 HDMI 监控画面，\n同时保留运行参数与操作入口。'),(15,26.5,'从采图、标注、训练到部署，\n形成面向现场人员的完整操作流程。')],console=True)
add('video','电磁阀和转接板展示.MOV',0,21.55,25,[(1,12,'系统结合跨帧关联与连续观测确认姿态，\n并记录工件处理状态，减少重复触发。'),(13.5,23.5,'识别结果还可以连接外部控制设备，\n本次演示通过转接板驱动电磁阀。')])
add('image','性能工具截图/01_TD_资源利用率.png',0,18,18,[(1,16.5,'安路 TD 最终布局布线报告显示：\nLUT6 占用 73.57%，ERAM 31.62%，DSP 30.00%。')])
add('image','性能工具截图/02_TD_时序与覆盖率.png',0,18,18,[(1,16.5,'布局布线后完成快慢双角静态时序分析，\n建立与保持裕量均为正，STA 覆盖率为 99.67%。')])
add('video','板卡移到振动盘.MOV',0,14.36,16,[(1,14.5,'FPGA 承担图像采集与预处理，\n与 RK 侧的识别、显示及控制功能协同运行。')])
add('card','',0,8,8,[],title='让现场图像，转化为可执行的判断',tag='基于 FPGA 的工业工件视觉应用')

header='''[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,44,&H00FFFFFF,&H00FFFFFF,&H00202020,&H70000000,0,0,0,0,100,100,0.5,0,1,1.7,0.7,2,90,90,38,1
Style: Title,Microsoft YaHei,66,&H00F3F3ED,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,1,0,1,0,0,5,90,90,0,1
Style: Small,Microsoft YaHei,30,&H00BFDAC0,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,2,0,1,0,0,5,90,90,0,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
def tc(t): return f'{int(t//3600)}:{int(t//60)%60:02}:{t%60:05.2f}'
def event(a,b,text,style='Default',pos=''):
    text=text.replace('\n',r'\N')
    return f'Dialogue: 0,{tc(a)},{tc(b)},{style},,0,0,0,,{{\\fad(250,250){pos}}}{text}\n'
def run(args,log):
    with (ROOT/log).open('a',encoding='utf-8') as f:
        subprocess.run(['ffmpeg','-hide_banner','-y','-nostdin',*args],cwd=ROOT,stdout=f,stderr=f,check=True)

global_ass=header; global_sub=[];offset=0
for i,s in enumerate(shots):
    s['timeline_start']=offset
    ass=header
    for a,b,txt in s['sub']:
        ass+=event(a,b,txt);global_ass+=event(a+offset,b+offset,txt);global_sub.append((a+offset,b+offset,txt))
    if s['kind']=='card':
        for style,txt,pos,a in [('Title',s['title'],r'\pos(960,480)',.5),('Small',s['tag'],r'\pos(960,650)',1.1)]:
            ass+=event(a,s['duration']-.4,txt,style,pos)
            global_ass+=event(a+offset,offset+s['duration']-.4,txt,style,pos)
    (ROOT/f'{i:02}.ass').write_text(ass,encoding='utf-8-sig')
    offset+=s['duration']
TOTAL=offset
(ROOT/'完整字幕.ass').write_text(global_ass,encoding='utf-8-sig')
def srt(t):
    ms=round(t*1000);return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'
(ROOT/'完整字幕.srt').write_text('\n\n'.join(f'{i+1}\n{srt(a)} --> {srt(b)}\n{txt}' for i,(a,b,txt) in enumerate(global_sub)),encoding='utf-8-sig')
(ROOT/'剪辑时间线.json').write_text(json.dumps(shots,ensure_ascii=False,indent=2),encoding='utf-8')

# Hardware encoding keeps this review cut quick to revise. All clips have the same output clock.
enc=['-c:v','libx264','-preset','veryfast','-crf','20','-threads','8','-pix_fmt','yuv420p','-an','-r','60','-video_track_timescale','60000']
for i,s in enumerate(shots):
    out=ROOT/f'{i:02}.mp4'
    if out.exists() and (ROOT/f'{i:02}.done').exists(): continue
    k=s['kind'];dur=s['duration'];speed=s.get('speed',1)
    inputs=[]
    if k=='card':
        inputs=['-f','lavfi','-i',f'color=c=0x11202a:s=1920x1080:r=60:d={dur}']
        filt=f'[0:v]drawbox=x=890:y=330:w=140:h=3:color=0xbad8bf:t=fill,ass={i:02}.ass[v]'
    elif k in ['sync','parallel']:
        if k=='sync':
            files=[CONSOLE,MEDIA/'实拍ISP使用.MOV'];starts=[s['start']]*2
            panels='[0:v]setpts=PTS-STARTPTS,scale=1200:750,setsar=1,fps=60[l];[1:v]setpts=PTS-STARTPTS,scale=640:360,setsar=1,fps=60[r];'
            placement='[b][l]overlay=24:116[tmp];[tmp][r]overlay=1256:310'
        else:
            files=[MEDIA/'高速整体演示（振动盘） (0-59s可用).MOV',MEDIA/'高速整体演示（HDMI）.MOV'];starts=[12,21]
            panels='[0:v]setpts=PTS-STARTPTS,scale=510:906:force_original_aspect_ratio=increase,crop=510:906,setsar=1,fps=60[l];[1:v]setpts=PTS-STARTPTS,scale=1280:720,setsar=1,fps=60[r];'
            placement='[b][l]overlay=40:26[tmp];[tmp][r]overlay=600:120'
        for file,start in zip(files,starts): inputs+=['-ss',str(start),'-t',str(s['length']),'-i',str(file)]
        filt=panels+f'color=c=0x11202a:s=1920x1080:r=60:d={dur}[b];'+placement+f',ass={i:02}.ass[v]'
    else:
        file=CONSOLE if k=='console' else MEDIA/s['name']
        if k=='image': inputs=['-loop','1','-framerate','60','-i',str(file)]
        else: inputs=['-ss',str(s['start']),'-t',str(s['length']),'-i',str(file)]
        prefix=f'setpts=(PTS-STARTPTS)/{speed},'
        if k=='image':
            layout='crop=2560:1310:0:0,scale=1758:900,pad=1920:1080:81:20:color=0x11202a'
        elif k=='console' or s.get('console'):
            layout='scale=1504:940,pad=1920:1080:208:0:color=0x11202a'
        else:
            layout='scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080'
        tail=f',setsar=1,fps=60,tpad=stop_mode=clone:stop_duration={dur},trim=duration={dur}'
        # Console and split-screen subtitles use the reserved lower area; photographic shots use a subtle translucent strip.
        if k=='video' and not s.get('console'): tail+=',drawbox=x=0:y=938:w=iw:h=142:color=black@0.22:t=fill'
        filt=f'[0:v]{prefix}{layout}{tail},ass={i:02}.ass[v]'
    if i==0: filt=filt.replace('[v]',',fade=t=in:d=0.6[v]')
    if i==len(shots)-1: filt=filt.replace('[v]',f',fade=t=out:st={dur-1.5}:d=1.5[v]')
    run([*inputs,'-filter_complex',filt,'-map','[v]','-t',str(dur),*enc,str(out)],f'{i:02}.log')
    (ROOT/f'{i:02}.done').write_text('complete')
    print(f'{i+1}/{len(shots)} done: {k}, {dur}s',flush=True)

# Bright, original instrumental bed: major-key electric-piano chords, light plucks and restrained percussion.
sr=44100;beat=60/100;bar=beat*4;loopsecs=bar*16
N=round(loopsecs*sr);music=np.zeros((N,2),np.float32)
rng=np.random.default_rng(20261101)
chords=[[261.626,329.628,391.995,493.883],[246.942,293.665,391.995,440],
        [220,261.626,329.628,391.995],[261.626,349.228,440,523.251]]
def note(at,f,dur,amp,pan=0.5):
    start=round(at*sr);n=min(round(dur*sr),N-start)
    if n<=0:return
    t=np.arange(n)/sr;env=(1-np.exp(-t*160))*np.exp(-t*3.0)
    a=(np.sin(2*np.pi*f*t)+.22*np.sin(2*np.pi*2*f*t)*np.exp(-t*4)+.06*np.sin(2*np.pi*3*f*t))*env*amp
    music[start:start+n,0]+=a.astype(np.float32)*math.sqrt(1-pan)
    music[start:start+n,1]+=a.astype(np.float32)*math.sqrt(pan)
for b in range(16):
    chord=chords[b%4]
    for bt in [0,1.5,2.5]:
        for j,f in enumerate(chord):note(b*bar+bt*beat+j*.012,f,1.6,.038,.25+j*.16)
    # Melody has gaps; avoids an insistent loop under future speech.
    for j,idx in enumerate(([0,2,1,3] if b%2==0 else [2,1,0])):
        note(b*bar+(j*.5+.5)*beat,chord[idx]*2,.75,.022,.65)
    for bt in [0,2]:note(b*bar+bt*beat,chord[0]/2,.55,.036,.5)
    for bt in range(4):
        at=round((b*bar+bt*beat)*sr);n=min(int(.09*sr),N-at);t=np.arange(n)/sr
        noise=rng.normal(0,1,n);noise=np.concatenate(([0],np.diff(noise)))
        hit=noise*np.exp(-t*65)*.006
        music[at:at+n,:]+=hit[:,None].astype(np.float32)
# Seamless repeated composition, with a global soft opening and ending.
full=np.tile(music,(math.ceil(TOTAL/loopsecs)+1,1))[:TOTAL*sr]
t=np.arange(len(full))/sr;env=np.minimum(t/3,1)*np.minimum((TOTAL-t)/4,1)
full*=env[:,None]
with wave.open(str(ROOT/'明亮轻快配乐.wav'),'wb') as f:
    f.setnchannels(2);f.setsampwidth(2);f.setframerate(sr);f.writeframes((np.clip(full,-1,1)*32767).astype('<i2').tobytes())
(ROOT/'concat.txt').write_text(''.join(f"file '{i:02}.mp4'\n" for i in range(len(shots))),encoding='utf-8')
run(['-f','concat','-safe','0','-i','concat.txt','-c','copy','picture.mp4'],'mux.log')
# A short low-level stretch of machine sound accompanies the overall sorting demonstration.
run(['-i','picture.mp4','-i','明亮轻快配乐.wav','-ss','12','-t','16','-i',str(MEDIA/'高速整体演示（振动盘） (0-59s可用).MOV'),
     '-filter_complex','[1:a]volume=1.15[m];[2:a:0]highpass=f=180,lowpass=f=2600,volume=0.06,afade=t=in:d=1,afade=t=out:st=15:d=1,adelay=63000|63000[s];[m][s]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.85[a]',
     '-map','0:v:0','-map','[a]','-c:v','copy','-c:a','aac','-b:a','192k','-t',str(TOTAL),'-movflags','+faststart','FPGA视觉系统_完整剪辑_v2.mp4'],'mux.log')
print(f'COMPLETE {TOTAL} seconds',flush=True)

