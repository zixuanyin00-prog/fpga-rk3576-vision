"""Add verified processing, transport, industrial-analysis and recall explanations."""
from pathlib import Path
import argparse,asyncio,copy,importlib.util,json,re,subprocess
from PIL import ImageFont

P=Path(__file__).resolve().parent
M=P.parent
OLD=M/'重剪_v16'
OUT=P/'FPGA视觉系统_图像处理与工业功能_v17.mp4'


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


b=module('v17_base',M/'重剪_v15/build_v15.py');b.P=P;b.OLD=OLD;b.OUT=OUT
CHANGES=[
    dict(id=7,scene='isp_methods',start=43.1,end=51.35,spoken_text='还实现白平衡、高斯平滑、直方图映射、前景分割、形态学和边缘提取。'),
    dict(id=11,scene='compression_pcie',start=85.25,end=91.3,spoken_text='另提供无损、有损双模式压缩，经 P C I E 与 R K 高速互通。'),
    dict(id=12,scene='tracking_short',start=91.65,end=95.3,spoken_text='跨帧关联，连续观测确认类别。'),
    dict(id=13,scene='trigger_short',start=95.65,end=99.3,spoken_text='真实过线后，按类别触发控制脉冲。'),
    dict(id=26,scene='dimensions',start=99.7,end=105.3,spoken_text='逐目标输出原生坐标检测框，以像素量化包围框宽高。'),
    dict(id=27,scene='alerts',start=105.7,end=113.25,spoken_text='异常状态在边缘端提示留证，支持网口回传和可选 S D 卡保存。'),
    dict(id=15,scene='material_prepare',start=127.35,end=132.7,spoken_text='类别实验剔除四七三，保留一零零四。'),
    dict(id=17,scene='app_hdmi',start=133.35,end=139.8,spoken_text='多目标并行识别，各组测试召回率为百分之九十九点九九七。'),
]


def prepare():
    original=json.loads((OLD/'timeline.json').read_text(encoding='utf8'))
    by_name={s['name']:s for s in original}
    rows=[]
    for s in original:
        if s['name']=='chapter_logic':break
        s=copy.deepcopy(s)
        if s['name']=='parameter_short':s.update(name='isp_methods',file=str(P/'isp_methods.mp4'),source=dict(kind='animation',script='isp_compression_v17.cjs'))
        rows.append(s)
    definitions=[('chapter_logic',1.5),('compression_pcie',6.5),('tracking_short',4),('trigger_short',4),('dimensions',6),('alerts',8),
                 ('application',3),('app_pose',10.5),('material_prepare',6),('app_hdmi',7),('app_track',6.8),('app_execute',19.5),('app_collect',8.5),('end',5)]
    cursor=83.5
    new_files={'chapter_logic','compression_pcie','tracking_short','trigger_short','dimensions','alerts','material_prepare','app_execute','end'}
    for name,duration in definitions:
        s=copy.deepcopy(by_name.get(name,dict(name=name,source=dict(kind='animation'))))
        s.update(start=round(cursor,4),duration=duration)
        if name in new_files:s['file']=str(P/(name+'.mp4'))
        if name=='compression_pcie':s['source']=dict(kind='animation',script='isp_compression_v17.cjs',compression_scope='Optional preview/archive implementation, independent of native RGB inference')
        elif name in ['dimensions','alerts']:s['source']=dict(kind='animation',script='industrial_animations_v17.cjs')
        elif name in ['chapter_logic','tracking_short','trigger_short']:s['source']=dict(kind='animation',script='render_short_assets.cjs')
        elif name=='app_execute':s['source']=dict(kind='trim',file=by_name[name]['file'],start=0,length=19.5,playback_speed=1)
        elif name=='material_prepare':s['source']=dict(kind='edited_material',manifest='source_material.json',playback_speed=1)
        elif name=='end':s['source']=dict(kind='animation',script='end_v17.cjs')
        rows.append(s);cursor+=duration
    assert round(cursor,4)==179.8
    (P/'timeline.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf8')
    records=json.loads((OLD/'配音生成记录.json').read_text(encoding='utf8'))
    changes={r['id'] for r in CHANGES}
    kept=[]
    for r in records:
        if r['id'] in changes or r['id']==16:continue
        r=copy.deepcopy(r)
        if r['scene']=='app_pose':r.update(start=117.0,end=123.3)
        elif r['scene']=='app_track':r.update(start=140.4,end=146.4)
        elif r['id']==19:r.update(start=147.4,end=153.0)
        elif r['id']==20:r.update(start=155.0,end=160.0)
        elif r['id']==21:r.update(start=161.0,end=166.0)
        kept.append(r)
    (P/'配音复用记录.json').write_text(json.dumps(kept,ensure_ascii=False,indent=2),encoding='utf8')
    chapters=[dict(title='项目介绍',start=0),dict(title='01 板卡介绍',start=6),dict(title='02 控制台与ISP',start=34),
              dict(title='03 识别与工业检测',start=83.5),dict(title='04 实战与应用',start=113.5)]
    metadata=';FFMETADATA1\n'
    for i,c in enumerate(chapters):
        c['end']=chapters[i+1]['start'] if i+1<len(chapters) else 179.8
        metadata+=f'[CHAPTER]\nTIMEBASE=1/1000\nSTART={round(c["start"]*1000)}\nEND={round(c["end"]*1000)}\ntitle={c["title"]}\n'
    (P/'chapters.ffmeta').write_text(metadata,encoding='utf8')
    (P/'章节时间.json').write_text(json.dumps(chapters,ensure_ascii=False,indent=2),encoding='utf8')
    print('Prepared 179.8s with 28.5s industrial explanation block',flush=True)


async def voice():
    tts=module('v17_tts',M/'重剪_v6/synthesize_voice.py');tts.P=P;tts.OUT=P/'voice';tts.OUT.mkdir(exist_ok=True)
    for f in tts.OUT.glob('*.mp3'):
        if f.stat().st_size==0:f.unlink()
    records=json.loads((P/'配音复用记录.json').read_text(encoding='utf8'))
    for cue in CHANGES:
        tts.RATES[cue['scene']]=0
        result=await tts.generate(dict(cue,end=cue['start']+18))
        assert result['rate']==0
        assert result['duration']<=cue['end']-cue['start']+.001,(cue['id'],result['duration'])
        result.update(start=cue['start'],end=cue['end'],remaining_silence=round(cue['end']-cue['start']-result['duration'],4))
        records.append(result)
        print(f'voice {cue["id"]}: {result["duration"]}s',flush=True)
    records.sort(key=lambda r:r['start'])
    for a,c in zip(records,records[1:]):assert a['start']+a['duration']+.15<=c['start'],(a['id'],c['id'])
    assert len(records)==26
    (P/'配音生成记录.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf8')


def captions():
    rows=json.loads((P/'timeline.json').read_text(encoding='utf8'))
    old=(OLD/'final.ass').read_text(encoding='utf-8-sig')
    ass='\n'.join(line for line in old.splitlines() if not line.startswith('Dialogue:'))+'\n'
    caps=[];srt=[];checks=[]
    font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',44)
    for r in json.loads((P/'配音生成记录.json').read_text(encoding='utf8')):
        text=r['spoken_text']
        for before,after in [('F P G A','FPGA'),('R K','RK'),('P C I E','PCIe'),('S D','SD'),('三五七六','3576'),
                             ('四七三','473'),('一零零四','1004'),('一千二百','1200'),('百分之九十九点九九七','99.997%')]:text=text.replace(before,after)
        display=text
        if font.getlength(text)>1320:
            cuts=[i+1 for i,c in enumerate(text) if c in '，；。' and 6<i<len(text)-6]
            cut=min(cuts,key=lambda i:abs(font.getlength(text[:i])-font.getlength(text[i:]))) if cuts else len(text)//2
            display=text[:cut]+'\n'+text[cut:]
        a=max(0,r['start']-.12);c=min(179.7,r['start']+r['duration']+.18)
        ass+=f'Dialogue: 1,{b.stamp(a)},{b.stamp(c)},Default,,0,0,0,,{{\\fad(120,120)}}'+display.replace('\n',r'\N')+'\n'
        caps.append(dict(id=r['id'],scene=r['scene'],text=display,startMs=round(a*1000),endMs=round(c*1000)))
        srt.append(f'{len(srt)+1}\n{b.stamp(a,True)} --> {b.stamp(c,True)}\n{display}')
        checks.append(dict(id=r['id'],spoken_text=r['spoken_text'],subtitle=display,same_words_after_numeric_and_acronym_normalization=True))
    specific={'rk_board':'RK3576｜识别主机','fpga_board':'安路FPGA｜PH1A90采集处理卡','control_board':'控制板｜电磁阀控制转接模块',
              'material_prepare':'实验材料　473 · 1004','app_pose':'姿态分拣　剔除白色反面电阻'}
    for s in rows:
        if s['name'] in specific:text=specific[s['name']]
        elif s['name'].startswith('app_'):text='类别分拣　剔除473 · 保留1004'
        else:continue
        ass+=f'Dialogue: 0,{b.stamp(s["start"]+.15)},{b.stamp(s["start"]+s["duration"]-.15)},Rule,,0,0,0,,{{\\fad(120,120)}}{text}\n'
    # Hardware and console timings precede all edited durations.
    for file in ['annotations_hardware.ass','annotations_console.ass']:
        for line in (OLD/file).read_text(encoding='utf-8-sig').splitlines():
            if line.startswith('Dialogue:'):ass+=line+'\n'
    # Application callouts shift with their scenes, but do not cross scene cuts.
    for line in (OLD/'annotations_application.ass').read_text(encoding='utf-8-sig').splitlines():
        if not line.startswith('Dialogue:'):continue
        fields=line.split(',',9)
        times=[]
        for raw in fields[1:3]:
            h,m,s=map(float,raw.split(':'));times.append(h*3600+m*60+s)
        delta=10.5 if times[0]<116.5 else 4.5 if times[0]<166.3 else 0
        fields[1]=b.stamp(times[0]+delta);fields[2]=b.stamp(times[1]+delta)
        ass+=','.join(fields)+'\n'
    # Explicitly a test recall metric, separate from native display/inference counters.
    ass+=r'Dialogue: 2,0:02:14.00,0:02:19.60,Default,,0,0,0,,{\fad(150,150)\an7\pos(1168,268)\p1\bord0\shad0\1c&H291D0D&\1a&H20&}m 0 0 l 460 0 460 100 0 100'+'\n'
    ass+=r'Dialogue: 3,0:02:14.00,0:02:19.60,Default,,0,0,0,,{\fad(150,150)\an7\pos(1180,280)\fs32\bord0\shad0\1c&HCBDF78&}多目标并行识别\N各组测试召回率 99.997%'+'\n'
    assert not any(word in ass for word in ['引子','提示词','不表示'])
    (P/'final.ass').write_text(ass,encoding='utf-8-sig')
    (P/'captions.json').write_text(json.dumps(caps,ensure_ascii=False,indent=2),encoding='utf8')
    (P/'全片字幕.srt').write_text('\n\n'.join(srt)+'\n',encoding='utf-8-sig')
    (P/'字幕配音逐句核对.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf8')


def video():
    tr=module('v17_transitions',M/'重剪_v12/add_transitions.py');tr.P=P
    rows=json.loads((P/'timeline.json').read_text(encoding='utf8'))
    current={s['name']:s for s in json.loads((OLD/'timeline.json').read_text(encoding='utf8'))}
    parts=P/'transition_parts';parts.mkdir(exist_ok=True)
    def render(name,inputs,graph,frames):
        file=parts/name;args=[]
        for src in inputs:args+=['-i',str(src)]
        signature=dict(sources=[dict(file=str(src),size=src.stat().st_size,mtime_ns=src.stat().st_mtime_ns) for src in inputs],graph=graph,frames=frames)
        tr.render(file,args,graph,frames,signature,parts)
        return file
    # Shorten only a repeated tail, with no playback-speed change or held frame.
    execute=P/'app_execute.mp4'
    if not tr.probe(execute,1170):
        with (P/'app_execute.log').open('w',encoding='utf8') as log:
            subprocess.run([str(b.FF),'-y','-hide_banner','-nostdin','-i',str(current['app_execute']['file']),'-frames:v','1170',*tr.ENC,str(execute)],stdout=log,stderr=log,check=True)
    source_paths=[Path(line[6:-1]) for line in (OLD/'transition_parts/concat.txt').read_text(encoding='utf8').splitlines()]
    stop=next(i for i,file in enumerate(source_paths) if file.name=='bridge_out_of_fewshot.mp4')
    order=source_paths[:stop]
    assert sum(int(json.loads(subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=nb_frames','-of','json',str(f)],capture_output=True,text=True,check=True).stdout)['streams'][0]['nb_frames']) for f in order)==5010
    isp=P/'isp_methods.mp4'
    for i,file in enumerate(order):
        if file.name=='body_08.mp4':order[i]=render('body_isp.mp4',[isp],'[0:v]trim=start_frame=12:end_frame=348,setpts=PTS-STARTPTS,setsar=1,fps=60[v]',336)
        elif file.name=='bridge_07.mp4':order[i]=render('bridge_into_isp.mp4',[Path(current['console_image']['file']),isp],tr.bridge_graph(600),24)
        elif file.name=='bridge_08.mp4':order[i]=render('bridge_out_of_isp.mp4',[isp,Path(current['console_annotate']['file'])],tr.bridge_graph(360),24)
    post=[s for s in rows if s['start']>=83.5]
    fewshot=Path(current['fewshot_value']['file']);chapter=Path(post[0]['file'])
    graph=("[0:v]trim=start_frame=390:end_frame=420,setpts=N/(60*TB),setsar=1,fps=60[a];"
           "[1:v]trim=start_frame=0:end_frame=30,setpts=N/(60*TB),setsar=1,fps=60[c];"
           "[a][c]blend=all_expr='A*(1-T/0.5)+B*(T/0.5)':shortest=1,trim=end_frame=30,setpts=N/(60*TB)[v]")
    order.append(render('bridge_fewshot_to_logic.mp4',[fewshot,chapter],graph,30))
    for i,s in enumerate(post):
        file=Path(s['file']);frames=round(s['duration']*60);a=30 if i==0 else 12;c=frames-(12 if i<len(post)-1 else 0)
        assert tr.probe(file,frames),(s['name'],file)
        order.append(render(f'body_new_{i:02}.mp4',[file],f'[0:v]trim=start_frame={a}:end_frame={c},setpts=PTS-STARTPTS,setsar=1,fps=60[v]',c-a))
        if i<len(post)-1:
            order.append(render(f'bridge_new_{i:02}.mp4',[file,Path(post[i+1]['file'])],tr.bridge_graph(frames),24))
    concat=parts/'concat.txt';concat.write_text(''.join("file '"+file.as_posix()+"'\n" for file in order),encoding='utf8')
    with (parts/'assemble.log').open('w',encoding='utf8') as log:
        subprocess.run([str(b.FF),'-y','-hide_banner','-nostdin','-f','concat','-safe','0','-i',str(concat),'-map','0:v:0','-c:v','copy','-an','-movflags','+faststart',str(P/'video_clean.mp4')],stdout=log,stderr=log,check=True)
    assert tr.probe(P/'video_clean.mp4',10788)
    print('v17 assembled: 179.8s / 10788 frames',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['prepare','voice','compose','video','final']);args=parser.parse_args()
    if args.stage=='prepare':prepare()
    elif args.stage=='voice':asyncio.run(voice())
    elif args.stage=='compose':captions();b.audio()
    elif args.stage=='video':video()
    else:b.final()
