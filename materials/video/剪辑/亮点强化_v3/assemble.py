from pathlib import Path
import subprocess,json
P=Path(__file__).resolve().parent
OLD=P.parent/'全片_v2'
old=json.loads((OLD/'剪辑时间线.json').read_text(encoding='utf8'))
order=[0,1,2,3,4,'parameter',6,7,'small',8,9,10,11,12,13,14,'async','tracking',15,16,17,18,19]
caps={
'parameter':[(.8,10.6,'通过控制台调整 FPGA 图像处理参数，\n经由 RK 下发配置，观察处理效果。'),(11.3,21.5,'参数下发与状态回读构成双向交互，\n让 FPGA 处理链路能够在现场调试。')],
'async':[(.8,10.5,'显示链路持续接收新画面，\n推理链路独立取图并执行识别。'),(11.2,21.5,'识别完成后异步更新结果缓存，\n让模型处理与现场预览分别调度。')],
'tracking':[(.8,11,'通过跨帧关联，持续观察同一个工件，\n结合连续图像确认姿态。'),(11.8,21.8,'达到确认条件后输出控制事件，\n并记录该工件的处理状态。'),(22.5,29.5,'后续帧继续关联同一工件，\n利用处理状态减少重复触发。')],
'small':[]}
durs={'parameter':22,'async':22,'tracking':30,'small':6}
names={'parameter':'亮点一：在线调节 FPGA 处理','small':'亮点二：小样本现场部署','async':'亮点三：显示与推理独立运行','tracking':'亮点四：连续确认再执行'}
timeline=[];subs=[];files=[];offset=0
for item in order:
 if isinstance(item,int):
  s=old[item];duration=s['duration'];entries=s['sub'];name=s.get('title') or s['name'] or s['kind'];f=OLD/f'{item:02}.mp4'
 else:duration=durs[item];entries=caps[item];name=names[item];f=P/(item+'.mp4')
 if not f.exists():raise FileNotFoundError(f)
 files.append("file '"+f.as_posix()+"'\n")
 timeline.append({'start':offset,'duration':duration,'name':name,'source':str(f)})
 for a,b,txt in entries:subs.append((offset+a,offset+b,txt))
 offset+=duration
def srt(t):
 ms=round(t*1000);return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'
(P/'完整字幕.srt').write_text('\n\n'.join(f'{i+1}\n{srt(a)} --> {srt(b)}\n{s}' for i,(a,b,s) in enumerate(subs)),encoding='utf-8-sig')
(P/'剪辑时间线.json').write_text(json.dumps(timeline,ensure_ascii=False,indent=2),encoding='utf8')
(P/'concat.txt').write_text(''.join(files),encoding='utf8')
ffmpeg=P.parent.parent/'dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe'
args=[str(ffmpeg),'-hide_banner','-y','-nostdin','-f','concat','-safe','0','-i',str(P/'concat.txt'),'-i',str(OLD/'明亮轻快配乐.wav'),'-ss','12','-t','16','-i',str(P.parent/'高速整体演示（振动盘） (0-59s可用).MOV'),
 '-filter_complex',f'[1:a]atrim=start=38.4:end=76.8,asetpts=PTS-STARTPTS,aloop=loop=-1:size=1693440,atrim=duration={offset},afade=t=in:d=3,afade=t=out:st={offset-4}:d=4,volume=1.15[m];[2:a:0]highpass=f=180,lowpass=f=2600,volume=0.06,afade=t=in:d=1,afade=t=out:st=15:d=1,adelay=63000|63000[s];[m][s]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.85[a]',
 '-map','0:v:0','-map','[a]','-c:v','h264_nvenc','-preset','p5','-rc','vbr','-cq','20','-b:v','0','-pix_fmt','yuv420p','-r','60','-c:a','aac','-b:a','192k','-t',str(offset),'-movflags','+faststart',str(P/'FPGA视觉系统_亮点强化版_v3.mp4')]
with (P/'assemble.log').open('w') as log:subprocess.run(args,stdout=log,stderr=log,check=True)
print(f'COMPLETE {offset}s',flush=True)
