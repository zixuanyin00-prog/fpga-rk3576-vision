from pathlib import Path
import argparse, json, re, subprocess, ast

P=Path(__file__).resolve().parent
M=P.parent
FF=str(M.parent/'dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe')
CONSOLE=next(M.glob('控制台全流程*.mp4'))
ENC=['-an','-c:v','h264_nvenc','-preset','p5','-cq','19','-b:v','0','-pix_fmt','yuv420p','-r','60','-video_track_timescale','60000']
tree=ast.parse((M/'全片_v2/render_full.py').read_text(encoding='utf-8-sig'))
HEADER=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(x,ast.Name) and x.id=='header' for x in n.targets))
HEADER=HEADER.replace('2,90,90,38,1','2,100,100,44,1')
SOURCES={
 'pose':dict(kind='footage',file='电阻姿态展示.MOV',start=1,length=8),
 'board':dict(kind='footage',file='板卡移到振动盘.MOV',start=0,length=6),
 'new_run':dict(kind='footage',file='新视频.mp4',start=4,length=8),
 'new_pan':dict(kind='footage',file='新视频.mp4',start=12,length=9.7),
 'isp_overview':dict(kind='sync',start=26,detail="crop=1320:270:365:'if(lt(t,3),1330,1130)'",detail_h=123,detail_y=627,context='crop=2440:1330:0:270,scale=600:327'),
 'isp_processing':dict(kind='sync',start=88,detail='crop=1320:450:330:970',detail_h=204,detail_y=590,context='crop=2440:1430:0:0,scale=600:352'),
 'capture':dict(kind='console',start=162,length=12,crop='crop=2440:1510:0:0',scale='scale=1450:897',x=235,y=23),
 'candidates':dict(kind='console',start=334,length=16,crop='crop=2440:1510:0:0',scale='scale=1450:897',x=235,y=23),
 'confirm':dict(kind='console',start=352,length=8,crop='crop=2440:1510:0:0',scale='scale=1450:897',x=235,y=23),
 'train':dict(kind='console',start=496,length=14,crop='crop=2440:820:0:0',scale='scale=1740:584',x=90,y=183),
 'test':dict(kind='console',start=520,length=12,crop='crop=2440:1180:0:0',scale='scale=1710:826',x=105,y=53),
 'deploy':dict(kind='console',file='部署后的运行结果.mp4',start=7,length=6.8,crop='crop=2440:1050:0:300',scale='scale=1740:749',x=90,y=97),
 'hardware':dict(kind='footage',file='电磁阀和转接板展示.MOV',start=0,length=2.8),
 'area':dict(kind='report',file='性能工具截图/01_TD_资源利用率.png'),
 'timing':dict(kind='report',file='性能工具截图/02_TD_时序与覆盖率.png'),
}
ANIMATIONS={'title','parameter','coarsefine','async','tracking','collage','end'}

def ass_time(t):
    return f'{int(t//3600)}:{int(t//60)%60:02}:{t%60:05.2f}'

def srt_time(t):
    ms=round(t*1000)
    return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'

def prepare():
    data=json.loads((P/'narrative_proposal.json').read_text(encoding='utf8'))
    scenes=data['scenes']
    # Only explicitly authored audience text is passed into ASS/JSON. Editorial
    # notes and source metadata are never projected onto a video frame.
    changes={
     'pose':[(.5,7.5,'以电阻为例，工件的正反面和数字方向各不相同，\n系统需要把这些差别识别成明确的姿态类别。')],
     'parameter':[(.4,5.9,'小型工件的表面特征细微，\n光照与成像设置会影响后续能够辨认的细节。'),(6.1,11.7,'控制台经 RK 向 FPGA 写入处理配置，\n板端再返回状态，确认参数是否已经生效。'),(11.9,17.5,'配置下发与状态回读形成双向控制，\n现场人员可直接调整 FPGA 的处理参数。')],
     'isp_overview':[(.5,7.1,'先调整成像增益，\n让工件从偏暗的背景中显现出更多表面细节。'),(7.3,14.3,'增益改变时，HDMI 画面的亮度随之变化；\n现场人员据此检查工件文字与背景是否容易区分。'),(14.5,21.5,'参数调节围绕识别需要展开，\n让后续模型从清晰、可辨认的图像中学习特征。')],
     'isp_processing':[(.5,7.1,'亮度合适之后，\n继续选择适合工件特征的图像处理功能。'),(7.3,14.3,'各个处理环节可以独立开关，\n便于比较处理前后的边缘与纹理。'),(14.5,21.7,'边缘响应反映图像中的灰度变化，\n可用于观察工件轮廓与背景的分离程度。'),(22,25.7,'彩色边缘叠加突出轮廓，\n保留原画面的色彩信息。'),(25.9,33.5,'效果确认后，固定本次训练采图参数，\n让同一批样本保持一致的成像条件。')],
     'capture':[(.5,5.8,'固定成像后采集现场工件，\n把不同位置与姿态整理成训练图像。'),(6,11.5,'现场换位置、换视角后可以重新采样，\n用少量本地样本建立适合当前任务的模型。')],
     'train':[(.5,6.7,'本次演示用 15 张现场原图，\n建立三类姿态样本，学习工件的外观差异。'),(6.9,13.5,'训练结果转换为 RK 侧使用的 INT8 模型，\n采图、标注、训练与转换都在控制台内完成。')],
     'coarsefine':[(.5,4.9,'粗推理先在全图定位工件，\n确定需要进一步分析的目标区域。'),(5.1,9.1,'随后裁取目标的原生 RGB 像素，\n保留局部纹理，交给细推理。'),(9.3,13.5,'细推理区分正反面与数字方向，\n完成目标的姿态判断。')],
     'test':[(.5,5.8,'训练后用现场图像检查结果，\n逐个对照目标位置与姿态类别。'),(6,11.5,'检查聚焦具体工件的判断是否匹配，\n再决定这次训练的模型是否适合当前任务。')],
     'deploy':[(.5,6.7,'训练得到的模型用于设备端识别，\n运行入口同时集成 HDMI 监控与参数操作。'),(6.9,13.5,'现场人员在同一控制台，\n检查成像设置、识别结果与设备运行状态。')],
     'tracking':[(.5,5.3,'同一工件会连续出现在多帧中，\n每帧识别都是一次观测。'),(5.5,10.3,'跨帧关联结合位置变化与重叠关系，\n为同一工件延续身份。'),(10.5,15.3,'同一身份积累姿态观测，\n以多帧一致性减少单帧波动的影响。'),(15.5,20.3,'满足确认条件后生成控制事件，\n并为该工件记录已处理状态。'),(20.5,25.5,'后续帧继续跟踪同一身份，\n查询已处理状态，减少重复触发。')],
     'collage':[(.4,5.9,'相机采集的像素经过 FPGA 预处理，\n再送到 RK，进入定位、姿态识别和连续确认。'),(6.1,11.7,'视觉结果连接现场执行设备，\n控制台贯穿调参、训练与运行监控。'),(11.9,17.5,'双向调参解决现场成像适配，\n两阶段推理和连续确认把图像变成可用的判断。'),(17.7,23.5,'从采集图像到持续识别工件，\n各环节共同完成现场视觉处理任务。')]
    }
    offset=0; captions=[];timeline=[];srt=[];draft=[]
    for s in scenes:
        name=s['name'];dur=s['duration']
        if name in changes:
            s['captions']=[dict(start=a,end=b,text=t) for a,b,t in changes[name]]
        assert name in ANIMATIONS or name in SOURCES
        last=0;ass=HEADER
        for c in s['captions']:
            assert last<=c['start']<c['end']<=dur
            assert len(c['text'].splitlines())<=2
            last=c['end']
            a,b=c['start'],c['end'];words=c['text']
            ass+=f'Dialogue: 0,{ass_time(a)},{ass_time(b)},Default,,0,0,0,,{{\\fad(160,160)}}'+words.replace('\n',r'\N')+'\n'
            captions.append(dict(text=words,startMs=round((offset+a)*1000),endMs=round((offset+b)*1000),timestampMs=None,confidence=None))
            srt.append(f'{len(srt)+1}\n{srt_time(offset+a)} --> {srt_time(offset+b)}\n{words}')
            draft.append(f'{srt_time(offset+a)}\n{words}')
        (P/(name+'.ass')).write_text(ass,encoding='utf-8-sig')
        timeline.append(dict(name=name,start=offset,duration=dur,file=str(P/(name+'.mp4')),source=SOURCES.get(name,dict(kind='animation'))))
        offset+=dur
    assert offset==310
    (P/'scenes.json').write_text(json.dumps(dict(duration=offset,scenes=scenes),ensure_ascii=False,indent=2),encoding='utf8')
    (P/'captions.json').write_text(json.dumps(captions,ensure_ascii=False,indent=2),encoding='utf8')
    (P/'时间线.json').write_text(json.dumps(timeline,ensure_ascii=False,indent=2),encoding='utf8')
    (P/'全片字幕.srt').write_text('\n\n'.join(srt)+'\n',encoding='utf-8-sig')
    (P/'字幕稿.txt').write_text('\n\n'.join(draft)+'\n',encoding='utf-8-sig')
    old=(M/'重剪_v5/全片字幕.srt').read_text(encoding='utf-8-sig')
    cn=lambda t:len(re.findall(r'[\u4e00-\u9fff]',t))
    rate=max(cn(c['text'])/((c['endMs']-c['startMs'])/1000) for c in captions)
    stats=dict(old_chinese_characters=cn(old),new_chinese_characters=sum(cn(c['text']) for c in captions),captions=len(captions),max_chinese_per_second=round(rate,2),duration=offset)
    (P/'文本统计.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(stats),flush=True)
    return timeline

def run(args,name):
    with (P/(name+'.log')).open('w',encoding='utf8') as log:
        subprocess.run([FF,'-y','-hide_banner','-nostdin',*args],cwd=P,stdout=log,stderr=log,check=True)

def render(item,preview=False):
    name=item['name'];dur=item['duration'];s=item['source'];kind=s['kind']
    if kind=='animation':return
    out=P/(name+'.mp4')
    if out.exists() and not preview:return
    if kind=='sync':
        inputs=['-ss',str(s['start']),'-i',str(CONSOLE),'-ss',str(s['start']),'-i',str(M/'实拍ISP使用.MOV')]
        graph=(f'[0:v]setpts=PTS-STARTPTS,fps=60,split=2[c][d];'
         f'[c]{s["context"]},setsar=1[c0];[d]{s["detail"]},scale=600:{s["detail_h"]},setsar=1[d0];'
         '[1:v]setpts=PTS-STARTPTS,fps=60,crop=1800:1012:60:34,scale=1200:674,setsar=1[h0];'
         f'color=c=0x0d1d29:s=1920x1080:r=60:d={dur}[bg];'
         '[bg]drawbox=x=26:y=142:w=608:h=360:color=0x647f82:t=2,'
         f'drawbox=x=26:y={s["detail_y"]-4}:w=608:h={s["detail_h"]+8}:color=0x647f82:t=2,'
         'drawbox=x=656:y=158:w=1208:h=682:color=0xabc9bd:t=2,'
         'drawbox=x=634:y=460:w=22:h=2:color=0xabc9bd:t=fill[b0];'
         f'[b0][c0]overlay=30:146[b1];[b1][d0]overlay=30:{s["detail_y"]}[b2];'
         f'[b2][h0]overlay=660:162,ass={name}.ass[v]')
    elif kind=='report':
        inputs=['-loop','1','-framerate','60','-i',str(M/s['file'])]
        graph=f'[0:v]scale=1510:887,pad=1920:1080:205:25:color=0x0d1d29,setsar=1,ass={name}.ass[v]'
    else:
        source=M/s['file'] if 'file' in s else CONSOLE
        inputs=['-ss',str(s['start']),'-t',str(s['length']),'-i',str(source)]
        if kind=='console':
            layout=f'{s["crop"]},{s["scale"]},pad=1920:1080:{s["x"]}:{s["y"]}:color=0x0d1d29'
        else:
            layout='scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,drawbox=x=0:y=938:w=iw:h=142:color=black@0.24:t=fill'
        graph=f'[0:v]setpts=(PTS-STARTPTS)/{s["length"]/dur},{layout},setsar=1,fps=60,tpad=stop_mode=clone:stop_duration=0.1,ass={name}.ass[v]'
    if preview:
        run([*inputs,'-filter_complex',graph,'-map','[v]','-frames:v','1','-q:v','2',name+'_layout.jpg'],name+'_preview')
    else:
        run([*inputs,'-filter_complex',graph,'-map','[v]','-t',str(dur),*ENC,str(out)],name)
        print(name+' complete',flush=True)

def assemble(timeline):
    for s in timeline:
        if not Path(s['file']).exists():raise FileNotFoundError(s['file'])
    (P/'concat.txt').write_text(''.join("file '"+Path(s['file']).as_posix()+"'\n" for s in timeline),encoding='utf8')
    run(['-f','concat','-safe','0','-i','concat.txt','-i',str(M/'重剪_v5/轻快配乐_v5.wav'),'-map','0:v','-map','1:a','-c:v','copy','-c:a','aac','-b:a','192k','-t','310','-movflags','+faststart','FPGA视觉系统_叙事重剪_v6.mp4'],'assemble')
    print('COMPLETE 310s',flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--prepare',action='store_true');parser.add_argument('--preview',action='store_true');parser.add_argument('--assemble',action='store_true');parser.add_argument('--part');args=parser.parse_args()
    timeline=prepare()
    if args.assemble:assemble(timeline)
    elif not args.prepare:
        for s in timeline:
            if not args.part or s['name']==args.part:render(s,args.preview)
