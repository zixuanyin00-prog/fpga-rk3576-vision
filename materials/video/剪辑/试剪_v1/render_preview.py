from pathlib import Path
import subprocess, json, wave
import numpy as np

ROOT = Path(__file__).resolve().parent
MEDIA = ROOT.parent
DURATION = 65

def run(args):
    with (ROOT / 'render.log').open('a', encoding='utf-8') as log:
        subprocess.run(['ffmpeg', '-hide_banner', '-y', '-nostdin', *args], stdout=log, stderr=log, check=True, cwd=ROOT)

# Original understated electronic music bed, no borrowed recording or vocals.
sr = 48000
music = np.zeros((sr * DURATION, 2), np.float32)
chords = [[146.832,220,261.626,329.628],[130.813,195.998,246.942,293.665],
          [110,164.814,220,261.626],[130.813,174.614,220,261.626]]
for k in range(9):
    start = k * 8
    length = min(10, DURATION-start)
    if length <= 0: break
    t = np.arange(int(sr*length))/sr
    env = np.minimum(t/2,1)*np.minimum((length-t)/2,1)
    for n,freq in enumerate(chords[k%4]):
        for ch,detune in enumerate([0.999,1.001]):
            tone = np.sin(2*np.pi*freq*detune*t)+0.16*np.sin(2*np.pi*freq*2*t)
            music[start*sr:start*sr+len(t),ch] += (tone*env*0.018).astype(np.float32)
for n in range(112):
    start = 7+n*0.5
    if start >= 63: break
    t=np.arange(int(sr*min(1.3,DURATION-start)))/sr
    f=chords[int(start//8)%4][[0,2,1,3,2,1,3,1][n%8]]*2
    env=(1-np.exp(-t*60))*np.exp(-t*5)
    tone=(np.sin(2*np.pi*f*t)+0.18*np.sin(2*np.pi*2*f*t))*env*0.035
    at=int(start*sr)
    music[at:at+len(t),n%2]+=tone.astype(np.float32)
    music[at:at+len(t),1-n%2]+=(tone*0.55).astype(np.float32)
    if n%4==0:
        kt=np.arange(int(sr*.25))/sr
        kick=np.sin(2*np.pi*(44*kt+3*(1-np.exp(-kt*20))))*np.exp(-kt*18)*0.065
        music[at:at+len(kt),:]+=kick[:,None].astype(np.float32)
fade=np.minimum(np.arange(sr*DURATION)/sr/2,1)*np.minimum((sr*DURATION-1-np.arange(sr*DURATION))/sr/3,1)
music*=fade[:,None]
with wave.open(str(ROOT/'原创氛围配乐.wav'),'wb') as wav:
    wav.setnchannels(2);wav.setsampwidth(2);wav.setframerate(sr)
    wav.writeframes((np.clip(music,-1,1)*32767).astype('<i2').tobytes())

enc=['-an','-c:v','libx264','-preset','fast','-crf','19','-pix_fmt','yuv420p','-threads','6']
base='scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1,fps=60'
run(['-f','lavfi','-i','color=c=0x10191f:s=1920x1080:r=60:d=7','-vf',
     'drawbox=x=894:y=330:w=132:h=4:color=0x67b9b0:t=fill',*enc,'00.mp4'])
shots=[('板卡移到振动盘.MOV',0,8,base),
       ('振动盘到HDMI.MOV',0,8,base),
       ('高速整体演示（HDMI）.MOV',9,10,base),
       ('部署后的运行结果.mp4',5,10,'crop=1400:788:416:440,scale=1920:1080,setsar=1,fps=60'),
       ('高速整体演示（HDMI）.MOV',29,10,base)]
for number,(name,start,dur,vf) in zip([1,2,3,5,6],shots):
    run(['-ss',str(start),'-i',str(MEDIA/name),'-t',str(dur),'-vf',vf,*enc,f'{number:02}.mp4'])
    print(f'完成镜头 {number}',flush=True)
run(['-ss','12','-i',str(MEDIA/'高速整体演示（振动盘） (0-59s可用).MOV'),
     '-ss','21','-i',str(MEDIA/'高速整体演示（HDMI）.MOV'),'-filter_complex',
     '[0:v]scale=540:960:force_original_aspect_ratio=increase,crop=540:960,setsar=1,fps=60[l];'
     '[1:v]scale=1280:720,setsar=1,fps=60[r];'
     'color=c=0x10191f:s=1920x1080:r=60:d=12[b];'
     '[b][l]overlay=40:40[tmp];[tmp][r]overlay=600:144[v]',
     '-map','[v]','-t','12',*enc,'04.mp4'])
(ROOT/'concat.txt').write_text(''.join(f"file '{i:02}.mp4'\n" for i in range(7)),encoding='utf-8')
run(['-f','concat','-safe','0','-i','concat.txt','-c','copy','picture.mp4'])

subs=[(7.6,14.6,'这是一套面向工业工件的\\N实时视觉处理与姿态识别系统。'),
      (15.6,22.6,'这里以电阻为例，\\N展示工件运行过程中的识别效果。'),
      (23.6,32.6,'识别结果通过 HDMI 实时显示，\\N呈现工件姿态与运行状态。'),
      (33.6,44.4,'系统根据识别结果自动触发电磁阀，\\N完成工件筛选。'),
      (45.6,54.4,'部署完成后，\\N可在控制台查看识别结果与运行状态。'),
      (55.6,63.3,'从图像采集、姿态识别到外接控制，\\N形成完整的现场应用流程。')]
def tc(sec):
    return f'{int(sec//3600)}:{int(sec//60)%60:02}:{sec%60:05.2f}'
ass='''[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,40,&H00FFFFFF,&H00FFFFFF,&H00202020,&H70000000,0,0,0,0,100,100,1,0,1,1.6,0.8,2,120,120,55,1
Style: Title,Microsoft YaHei,65,&H00F3F3ED,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,2,0,1,0,0,5,90,90,0,1
Style: Small,Microsoft YaHei,28,&H00B0B967,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,4,0,1,0,0,5,90,90,0,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
ass+='Dialogue: 0,0:00:00.40,0:00:06.70,Title,,0,0,0,,{\\fad(700,500)\\pos(960,485)}基于 FPGA 的实时视觉处理\\N与工件姿态识别系统\n'
ass+='Dialogue: 0,0:00:01.20,0:00:06.70,Small,,0,0,0,,{\\fad(600,500)\\pos(960,650)}小样本快速学习  ·  实时姿态识别  ·  外接控制\n'
for a,b,text in subs:
    ass+=f'Dialogue: 0,{tc(a)},{tc(b)},Default,,0,0,0,,{{\\fad(180,180)}}{text}\n'
(ROOT/'字幕.ass').write_text(ass,encoding='utf-8-sig')
def srt_tc(x): return tc(x).replace('.',',')+'0'
(ROOT/'字幕.srt').write_text('\n\n'.join(f'{i+1}\n{srt_tc(a)} --> {srt_tc(b)}\n{text.replace(chr(92)+"N",chr(10))}' for i,(a,b,text) in enumerate(subs)),encoding='utf-8-sig')
# Very low-level field sound from the bowl footage, underneath the music.
run(['-i','picture.mp4','-i','原创氛围配乐.wav','-ss','12','-t','12','-i',str(MEDIA/'高速整体演示（振动盘） (0-59s可用).MOV'),
     '-filter_complex',
     '[0:v]drawbox=x=0:y=934:w=iw:h=146:color=black@0.30:t=fill:enable=gte(t\\,7),ass=字幕.ass,fade=t=in:d=0.5,fade=t=out:st=63.6:d=1.4[v];'
     '[1:a]volume=0.85[m];[2:a:0]highpass=f=180,lowpass=f=3000,volume=0.09,afade=t=in:d=1,afade=t=out:st=11:d=1,adelay=33000|33000[s];'
     '[m][s]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.8[a]',
     '-map','[v]','-map','[a]','-t','65','-c:v','libx264','-preset','fast','-crf','18','-threads','6','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-movflags','+faststart','FPGA视觉系统_开场试剪_v1_65秒.mp4'])
print('渲染完成',flush=True)


