from pathlib import Path
import ast, json, subprocess, argparse, re, wave, math
import numpy as np

P=Path(__file__).resolve().parent; M=P.parent; OLD=M/'重剪_v4'
FF=str(M.parent/'dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe')
CONSOLE=next(M.glob('控制台全流程*.mp4'))
tree=ast.parse((M/'全片_v2/render_full.py').read_text(encoding='utf-8-sig'))
HEADER=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(x,ast.Name) and x.id=='header' for x in n.targets))
ENC=['-an','-c:v','h264_nvenc','-preset','p5','-cq','19','-b:v','0','-pix_fmt','yuv420p','-r','60','-video_track_timescale','60000']
shots=[
 dict(name='isp_overview',kind='sync',start=26,duration=22,detail='crop=1320:400:365:1200',detail_h=182,detail_y=618,sub=[(0.7,10.5,'通过控制台调节成像参数，\n观察 HDMI 上对应的亮度与色彩变化。'),(11.2,21.5,'参数控制与现场显示同屏呈现，\n把调节动作和画面变化对应起来。')]),
 dict(name='isp_processing',kind='sync',start=88,duration=34,detail='crop=1320:620:330:810',detail_h=282,detail_y=580,sub=[(0.7,10.4,'通过处理开关，\n选择当前场景需要的 FPGA 图像处理功能。'),(11.2,23.5,'开启彩色边缘叠加，\n在原始画面上突出工件轮廓。'),(24.2,33.5,'完成效果对照后，\n固定训练采图使用的成像与处理参数。')]),
 dict(name='capture',kind='console',start=159,length=12,duration=12,sub=[(.7,11.5,'采集当前场景的工件图像，\n为本次小样本训练准备数据。')]),
 dict(name='candidates',kind='console',start=334,length=18,duration=16,sub=[(.7,15.5,'利用自动生成的候选框定位工件，\n再由现场人员检查并确认姿态类别。')]),
 dict(name='confirm',kind='console',start=352,length=12,duration=8,sub=[(.5,7.5,'保留必要的人工确认，\n让样本位置与姿态标签对应。')]),
 dict(name='train',kind='console',start=487,length=14,duration=14,sub=[(.7,13.5,'完成标注后训练并转换模型，\n在控制台查看训练结果。')]),
 dict(name='test',kind='console',start=514,length=18,duration=12,sub=[(.7,11.5,'选择本次训练版本，\n通过现场图像检查位置与姿态判断。')]),
 dict(name='deploy',kind='console',file='部署后的运行结果.mp4',start=7,length=14,duration=14,sub=[(.7,13.5,'部署后在控制台查看 HDMI 监控与运行状态，\n完成从采图到现场运行的操作流程。')]),
]
opening=[
 dict(name='new_run',kind='footage',file='新视频.mp4',start=0,length=12,duration=12,sub=[(.6,11.5,'以电阻工件为例，\n展示振动送料场景下的视觉采集。')]),
 dict(name='new_pan',kind='footage',file='新视频.mp4',start=12,length=9.7,duration=13,sub=[(.6,12.5,'采集图像进入处理链路，\n并在 HDMI 屏幕上实时显示。')]),
]

def tc(t):return f'{int(t//3600)}:{int(t//60)%60:02}:{t%60:05.2f}'

def run(args,name):
    with (P/(name+'.log')).open('w',encoding='utf8') as log:
        subprocess.run([FF,'-y','-hide_banner','-nostdin',*args],cwd=P,stdout=log,stderr=log,check=True)

def render(s,preview=False):
    name=s['name'];dur=s['duration'];out=P/(name+'.mp4')
    if out.exists() and not preview:return
    ass=HEADER
    for a,b,text in s['sub']:
        ass+=f'Dialogue: 0,{tc(a)},{tc(b)},Default,,0,0,0,,{{\\fad(200,200)}}'+text.replace('\n',r'\N')+'\n'
    (P/(name+'.ass')).write_text(ass,encoding='utf-8-sig')
    if s['kind']=='sync':
        # Both panels use exactly the same source time. The lower-left inset is
        # an enlarged view of the same console frame, never a replacement preview.
        inputs=['-ss',str(s['start']),'-i',str(CONSOLE),'-ss',str(s['start']),'-i',str(M/'实拍ISP使用.MOV')]
        graph=(f'[0:v]setpts=PTS-STARTPTS,fps=60,split=2[c][d];'
          f'[c]scale=600:375,setsar=1[c0];[d]{s["detail"]},scale=600:{s["detail_h"]},setsar=1[d0];'
          '[1:v]setpts=PTS-STARTPTS,fps=60,crop=1800:1012:60:34,scale=1200:674,setsar=1[h0];'
          f'color=c=0x11202a:s=1920x1080:r=60:d={dur}[bg];'
          '[bg]drawbox=x=26:y=132:w=608:h=383:color=0x647f82:t=2,'
          f'drawbox=x=26:y={s["detail_y"]-4}:w=608:h={s["detail_h"]+8}:color=0x647f82:t=2,'
          'drawbox=x=656:y=158:w=1208:h=682:color=0xabc9bd:t=2,'
          'drawbox=x=634:y=491:w=22:h=2:color=0xabc9bd:t=fill[b0];'
          f'[b0][c0]overlay=30:136[b1];[b1][d0]overlay=30:{s["detail_y"]}[b2];'
          f'[b2][h0]overlay=660:162,ass={name}.ass[v]')
    else:
        source=M/s['file'] if 'file' in s else CONSOLE
        inputs=['-ss',str(s['start']),'-t',str(s['length']),'-i',str(source)]
        speed=s['length']/dur
        layout='scale=1920:1080' if s['kind']=='footage' else 'scale=1488:930,pad=1920:1080:216:20:color=0x11202a'
        graph=f'[0:v]setpts=(PTS-STARTPTS)/{speed},{layout},setsar=1,fps=60,tpad=stop_mode=clone:stop_duration=0.1,ass={name}.ass[v]'
    if preview:
        run([*inputs,'-filter_complex',graph,'-map','[v]','-frames:v','1','-q:v','2',name+'_layout.jpg'],name+'_preview')
    else:
        run([*inputs,'-filter_complex',graph,'-map','[v]','-t',str(dur),*ENC,str(out)],name)
        print(name+' complete',flush=True)

def assemble():
    manifest=json.loads((P/'opening.json').read_text(encoding='utf8'))
    order=[('title',6,OLD/'title.mp4'),('board',7,OLD/'board.mp4')]
    order.extend((s['name'],s['duration'],P/(s['name']+'.mp4')) for s in manifest)
    order.append(('parameter',22,OLD/'parameter.mp4'))
    order.extend((s['name'],s['duration'],P/(s['name']+'.mp4')) for s in shots)
    order.extend((n,d,OLD/(n+'.mp4')) for n,d in [('async',22),('tracking',30),('hardware',8),('area',12),('timing',12),('collage',28),('end',6)])
    offset=0;timeline=[];files=[]
    for name,dur,f in order:
        if not f.exists():raise FileNotFoundError(f)
        timeline.append(dict(name=name,start=offset,duration=dur,file=str(f)));offset+=dur
        files.append("file '"+f.as_posix()+"'\n")
    (P/'时间线.json').write_text(json.dumps(timeline,ensure_ascii=False,indent=2),encoding='utf8')
    (P/'concat.txt').write_text(''.join(files),encoding='utf8')
    # Reuse a complete middle phrase, so the longer cut has no fade-out at a loop boundary.
    with wave.open(str(OLD/'轻快钢琴打击乐_v4.wav'),'rb') as f:
        sr=f.getframerate();phrase=round((60/106)*4*16*sr);f.setpos(phrase)
        music=np.frombuffer(f.readframes(phrase),dtype='<i2').reshape(-1,2)
    music=np.tile(music,(math.ceil(offset*sr/len(music)),1))[:round(offset*sr)].copy()
    for count,fade,sl in [(sr*2,np.linspace(0,1,sr*2),slice(0,sr*2)),(sr*3,np.linspace(1,0,sr*3),slice(-sr*3,None))]:
        music[sl]=(music[sl].astype(np.float32)*fade[:,None]).astype('<i2')
    with wave.open(str(P/'轻快配乐_v5.wav'),'wb') as f:
        f.setnchannels(2);f.setsampwidth(2);f.setframerate(sr);f.writeframes(music.tobytes())
    captions=[]
    animated={
      'parameter':[(.8,10.6,'通过控制台调整 FPGA 图像处理参数，\n经由 RK 下发配置，观察处理效果。'),(11.3,21.5,'参数下发与状态回读构成双向交互，\n让 FPGA 处理链路能够在现场调试。')],
      'async':[(.8,10.5,'显示链路持续接收新画面，\n推理链路独立取图并执行识别。'),(11.2,21.5,'识别完成后异步更新结果缓存，\n让模型处理与现场预览分别调度。')],
      'tracking':[(.8,11,'通过跨帧关联，持续观察同一个工件，\n结合连续图像确认姿态。'),(11.8,21.8,'达到确认条件后输出控制事件，\n并记录该工件的处理状态。'),(22.5,29.5,'后续帧继续关联同一工件，\n利用处理状态减少重复触发。')],
      'collage':[(0,12,'从图像采集、处理到识别与控制，\n各个模块共同支撑现场应用。'),(12,28,'FPGA 负责图像采集与预处理，\nRK 完成识别、显示和外接控制。')],
    }
    for item in timeline:
        name=item['name'];local=animated.get(name,[])
        if name not in animated:
            ass=Path(item['file']).with_suffix('.ass');shift=0
            if name in ['area','timing']:
                ass=M/'全片_v2'/('16.ass' if name=='area' else '17.ass');shift=1
            for line in ass.read_text(encoding='utf-8-sig').splitlines():
                if not line.startswith('Dialogue: '):continue
                parts=line.split(',',9)
                if parts[3]!='Default':continue
                times=[]
                for v in parts[1:3]:
                    h,m,s=map(float,v.split(':'));times.append(h*3600+m*60+s-shift)
                local.append((max(0,times[0]),min(item['duration'],times[1]),re.sub(r'\{[^}]*\}','',parts[9]).replace(r'\N','\n')))
        for a,b,words in local:captions.append((item['start']+a,item['start']+b,words))
    def srt_time(t):
        ms=round(t*1000);return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'
    (P/'全片字幕.srt').write_text('\n\n'.join(f'{i}\n{srt_time(a)} --> {srt_time(b)}\n{words}' for i,(a,b,words) in enumerate(captions,1))+'\n',encoding='utf-8-sig')
    run(['-f','concat','-safe','0','-i','concat.txt','-i','轻快配乐_v5.wav','-map','0:v','-map','1:a','-c:v','copy','-c:a','aac','-b:a','192k','-t',str(offset),'-movflags','+faststart','FPGA视觉系统_重剪_v5.mp4'],'assemble')
    print(f'COMPLETE {offset}s',flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--preview',action='store_true');parser.add_argument('--assemble',action='store_true');args=parser.parse_args()
    if args.assemble:assemble()
    else:
        (P/'opening.json').write_text(json.dumps(opening,ensure_ascii=False,indent=2),encoding='utf8')
        for s in shots[:2] if args.preview else opening+shots:render(s,args.preview)
