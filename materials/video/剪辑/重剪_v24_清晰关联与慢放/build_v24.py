"""Evidence-led revision of the selected B film; retain the 179.8 second limit."""
from pathlib import Path
import argparse,asyncio,copy,functools,importlib.util,json,os,re,subprocess,sys,concurrent.futures,wave
import numpy as np
from PIL import ImageFont

P=Path(__file__).resolve().parent;M=P.parent;OLD=M/'重剪_v23_证据补强'
OUT=P/'FPGA视觉系统_B清晰关联与分段慢放_v24.mp4';DURATION=179.8
FF=M.parent/'dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe'
EVIDENCE={'isp_methods':10.5,'compression_pcie':6.4,'tracking_short':5.2,'dimensions':5.6,'alerts':6.5}


def load(name,file):
    spec=importlib.util.spec_from_file_location(name,file);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


base=load('v24_old',M/'重剪_v19/build_v19.py')
tr=load('v24_transitions',M/'重剪_v12/add_transitions.py');tr.P=P
mixbase=base.b;mixbase.P=P;mixbase.OUT=OUT


def normalize(text):
    return base.normalize(text).replace('百分之九十二点八七','92.87%').replace('八八零','880')


def prepare():
    original=json.loads((OLD/'timeline.json').read_text('utf8'));rows=[]
    oldrows={s['name']:s for s in original}
    for item in original:
        s=copy.deepcopy(item);name=s['name']
        if name in EVIDENCE:
            s['duration']=EVIDENCE[name];s['file']=str(P/'scenes'/(name+'.mp4'));s['source']={'renderer':'evidence_scenes_v24.cjs','basis':'verified source assets and records'}
        if name=='pose_bowl_lead':
            s.update(name='pose_sorting_slow',duration=9.5,file=str(P/'scenes/pose_sorting_slow.mp4'),label='姿态分拣　剔除白色反面电阻')
            s.pop('speed_label',None)
            s['source']=dict(file=str(M/'高速整体演示（振动盘） (0-59s可用).MOV'),start=40,length=3.5,crop='crop=1600:900:500:1300',speed_segments=[dict(start=0,end=1,speed=1),dict(start=1,end=1.75,speed=.5),dict(start=1.75,end=3.5,speed=.25)])
        if name in ['pose_burst_a','pose_burst_b']:continue
        if name=='pose_slow':
            s.update(file=str(P/'scenes/pose_slow.mp4'),label='姿态检测　慢放观察绿色确认框');s.pop('speed_label',None)
            s['source']=dict(file=str(M/'高速整体演示（HDMI）.MOV'),start=26,length=4.75,crop='crop=1200:675:1000:1050',speed_segments=[dict(start=0,end=3.25,speed=.5),dict(start=3.25,end=4.75,speed=.25)])
        if name=='category_action':
            s.update(duration=830/60,file=str(P/'scenes/category_action.mp4'))
            s['source']=dict(file=item['file'],start=0,length=830/60,playback_speed=1,filters=['null'])
        rows.append(s)
    cursor=0
    for s in rows:s['start']=cursor/60;cursor+=round(s['duration']*60)
    assert cursor==10788,cursor
    lookup={s['name']:s for s in rows}
    assert lookup['category_collect']['start']==168.5
    assert lookup['pose_sorting_slow']['duration']==9.5 and lookup['pose_slow']['duration']==12.5
    (P/'timeline.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),'utf8')
    (P/'evidence_render_plan.json').write_text(json.dumps([s for s in rows if s['name'] in EVIDENCE],ensure_ascii=False,indent=2),'utf8')
    voice=copy.deepcopy(json.loads((OLD/'voice_plan.json').read_text('utf8')))
    for v in voice:
        oldname=v['scene'];newname='pose_sorting_slow' if oldname in ['pose_bowl_lead','pose_burst_a','pose_burst_b'] else oldname
        if newname in lookup and oldname in oldrows:
            if newname=='pose_sorting_slow':offset=4.5
            else:offset=lookup[newname]['start']-oldrows[oldname]['start']
            v['start']+=offset;v['end']+=offset
        v['scene']=newname
    by={v['id']:v for v in voice}
    updates={
      7:(39.65,49.85,'这里分别展示高斯平滑、亮度映射、阈值分割、形态学处理、边缘提取和彩色轮廓。'),
      12:(79.95,84.85,'电阻从左向右移动，轨迹编号八八零保持一致。'),
      26:(88.35,93.65,'同时给出多个工件检测框的像素宽、高，便于比较。'),
      30:(109.2,112.9,'两段慢放，看清白色反面电阻被吹出。'),
      17:(113.1,118.4,None),
      28:(126,133.2,'进一步慢放，观察绿色确认框随工件位置更新。')
    }
    for i,(a,z,words) in updates.items():
        by[i].update(start=a,end=z)
        if words:by[i]['spoken_text']=words;by[i].pop('reuse_id',None)
    # Collection and closing retain their original screen slots after removing the execution tail.
    by[21].update(start=163.4,end=168.35)
    by[22].update(start=168.95,end=174.4);by[23].update(start=175.05,end=179.75)
    voice.sort(key=lambda r:r['start']);(P/'voice_plan.json').write_text(json.dumps(voice,ensure_ascii=False,indent=2),'utf8')
    chapters=[('项目介绍',0),('01 板卡介绍',6),('02 控制台与ISP',30.5),('03 识别与工业检测',lookup['chapter_logic']['start']),('04 实战：姿态检测',lookup['application']['start']),('类别检测与连续分拣',lookup['category_material']['start']),('系统亮点',174.8)]
    metadata=';FFMETADATA1\n'
    for i,(name,a) in enumerate(chapters):
        z=chapters[i+1][1] if i+1<len(chapters) else DURATION
        metadata+=f'[CHAPTER]\nTIMEBASE=1/1000\nSTART={round(a*1000)}\nEND={round(z*1000)}\ntitle={name}\n'
    (P/'chapters.ffmeta').write_text(metadata,'utf8')
    changes=dict(duration=179.8,deleted_v23_range=[164,168.5],ISP_seconds=10.5,tracking_seconds=5.2,tracking_id=880,tracking_source_frames=[89270,89278,89284],bowl_source=[40,43.5],bowl_speeds=[1,.5,.25],hdmi_source=[26,30.75],hdmi_speeds=[.5,.25],body_pixels_only=True)
    (P/'edit_changes.json').write_text(json.dumps(changes,ensure_ascii=False,indent=2),'utf8')
    print('Prepared v24: 179.8s; 4.5s execution tail removed; ISP/tracking extended',flush=True)


def render_speed_scene(s):
    src=s['source'];out=Path(s['file']);out.parent.mkdir(exist_ok=True);frames=round(s['duration']*60)
    signature=dict(source=src,frames=frames,render='continuous-source split speed changes + 60fps MCI')
    marker=out.with_suffix('.source.json')
    if marker.is_file() and json.loads(marker.read_text('utf8'))==signature and tr.probe(out,frames):print('reuse '+s['name'],flush=True);return
    segs=src['speed_segments'];n=len(segs)
    graph=[f'[0:v]{src["crop"]},scale=1280:720:flags=lanczos,split={n}'+''.join(f'[s{i}]' for i in range(n))]
    for i,a in enumerate(segs):graph.append(f'[s{i}]trim=start={a["start"]}:end={a["end"]},setpts=(PTS-STARTPTS)/{a["speed"]}[v{i}]')
    graph.append(''.join(f'[v{i}]' for i in range(n))+f'concat=n={n}:v=1:a=0,minterpolate=fps=60:mi_mode=mci:mc_mode=obmc:me_mode=bidir:vsbmc=1,scale=1920:1080:flags=lanczos,drawbox=x=0:y=930:w=iw:h=150:color=0x0d1d29@0.65:t=fill,setsar=1,fps=60,tpad=stop_mode=clone:stop=4,trim=end_frame={frames},setpts=N/(60*TB)[v]')
    with out.with_suffix('.log').open('w',encoding='utf8') as log:
        subprocess.run([str(FF),'-y','-hide_banner','-nostdin','-ss',str(src['start']),'-t',str(src['length']+.04),'-i',src['file'],'-filter_complex',';'.join(graph),'-map','[v]','-frames:v',str(frames),*tr.ENC,str(out)],stdout=log,stderr=log,check=True)
    assert tr.probe(out,frames),s['name'];marker.write_text(json.dumps(signature,ensure_ascii=False,indent=2),'utf8');print('rendered '+s['name'],flush=True)


def render_scene(s):
    file=Path(s['file'])
    if file.parent!=P/'scenes' or s['name'] in EVIDENCE:return
    file.parent.mkdir(exist_ok=True);src=s['source'];frames=round(s['duration']*60)
    if src.get('speed_segments'):return render_speed_scene(s)
    if tr.probe(file,frames):print('reuse '+s['name'],flush=True);return
    args=[str(FF),'-y','-hide_banner','-nostdin']
    if src.get('segments'):
        args+=['-i',src['file']];graph=[];labels=[]
        for i,r in enumerate(src['segments']):
            a=round(r['start']*60);z=round((r['start']+r['length'])*60);graph.append(f'[0:v]trim=start_frame={a}:end_frame={z},setpts=PTS-STARTPTS[v{i}]');labels.append(f'[v{i}]')
        graph.append(''.join(labels)+f'concat=n={len(labels)}:v=1:a=0,setsar=1,fps=60[v]');args+=['-filter_complex',';'.join(graph),'-map','[v]']
    else:
        speed=src.get('playback_speed',1);args+=['-ss',str(src.get('start',0)),'-t',str(src['length']+.04),'-i',src['file']]
        filters=[f'setpts=(PTS-STARTPTS)/{speed}']
        if src.get('crop'):filters.append(src['crop'])
        filters+=src.get('filters',['scale=1920:1080:flags=lanczos','drawbox=x=0:y=930:w=iw:h=150:color=0x0d1d29@0.65:t=fill'])
        if src.get('interpolate'):
            # Native capture is ~120fps. Interpolate the slowed display recording at 720p,
            # then upscale; this affects the film only, never model inputs or metrics.
            filters=['setpts=(PTS-STARTPTS)/0.8',src['crop'],'scale=1280:720:flags=lanczos','minterpolate=fps=60:mi_mode=mci:mc_mode=obmc:me_mode=bidir:vsbmc=1','scale=1920:1080:flags=lanczos','drawbox=x=0:y=930:w=iw:h=150:color=0x0d1d29@0.65:t=fill','tpad=stop_mode=clone:stop=3']
        filters+=['setsar=1','fps=60'];args+=['-vf',','.join(filters)]
    with file.with_suffix('.log').open('w',encoding='utf8') as log:subprocess.run([*args,'-frames:v',str(frames),*tr.ENC,str(file)],stdout=log,stderr=log,check=True)
    assert tr.probe(file,frames),s['name']
    print('rendered '+s['name'],flush=True)


def footage():
    rows=json.loads((P/'timeline.json').read_text('utf8'))
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(render_scene,rows))


async def voice():
    tts=load('v24_tts',M/'重剪_v6/synthesize_voice.py');tts.OUT=P/'voice';tts.OUT.mkdir(exist_ok=True)
    tts.edge_tts.Communicate=functools.partial(tts.edge_tts.Communicate,proxy=os.environ.get('HTTPS_PROXY'))
    old={r['id']:r for r in json.loads((OLD/'配音生成记录.json').read_text('utf8'))};records=[]
    for cue in json.loads((P/'voice_plan.json').read_text('utf8')):
        prev=old[cue['id']]
        if cue['spoken_text']==prev['spoken_text']:r=copy.deepcopy(prev)
        else:
            tts.RATES[cue['scene']]=0;r=await tts.generate(dict(cue,end=cue['start']+18))
        assert r['duration']<=cue['end']-cue['start']+.001,(cue['id'],r['duration'],cue['end']-cue['start'])
        r.update(id=cue['id'],scene=cue['scene'],start=cue['start'],end=cue['end']);records.append(r)
        print('voice '+str(cue['id'])+' '+str(r['duration']),flush=True)
    for a,z in zip(records,records[1:]):assert a['start']+a['duration']+.12<=z['start'],(a['id'],z['id'])
    (P/'配音生成记录.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),'utf8')


def captions():
    original=(M/'重剪_v19/final.ass').read_text('utf-8-sig');header='\n'.join(s for s in original.splitlines() if not s.startswith('Dialogue:'))
    header=header.replace('[Events]','Style: BSubtitle,Microsoft YaHei,44,&H00473718,&H00473718,&H00E9DDCB,&H00000000,0,0,0,0,100,100,0.5,0,1,0,0,2,100,100,44,1\n[Events]')
    lines=[header];rows=json.loads((P/'timeline.json').read_text('utf8'));oldrows=json.loads((M/'重剪_v19/timeline.json').read_text('utf8'));font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',44)
    names=set(EVIDENCE)|{'opening_title','chapter_board','chapter_console','chapter_logic','fewshot_value','trigger_short','application','end'}
    light=[(s['start'],s['start']+s['duration']) for s in rows if s['name'] in names]
    caps=[];srt=[]
    for r in json.loads((P/'配音生成记录.json').read_text('utf8')):
        words=normalize(r['spoken_text']);text=words
        if font.getlength(text)>1320:
            cuts=[i+1 for i,c in enumerate(text) if c in '，；：。' and 4<i<len(text)-4];i=min(cuts,key=lambda i:max(font.getlength(text[:i]),font.getlength(text[i:]))) if cuts else len(text)//2;text=text[:i]+'\n'+text[i:]
        a=max(0,r['start']-.1);z=min(179.7,r['start']+r['duration']+.15)
        caps.append(dict(id=r['id'],scene=r['scene'],text=text,startMs=round(a*1000),endMs=round(z*1000)));srt.append(f'{len(srt)+1}\n{base.b.stamp(a,True)} --> {base.b.stamp(z,True)}\n{text}')
        points=sorted({a,z,*[q for span in light for q in span if a<q<z]})
        for lo,hi in zip(points,points[1:]):
            style='BSubtitle' if any(x<=(lo+hi)/2<y for x,y in light) else 'Default';body=text.replace('\n',r'\N')
            lines.append(f'Dialogue: 1,{base.b.stamp(lo)},{base.b.stamp(hi)},{style},,0,0,0,,{{\\fad({100 if lo==a else 0},{100 if hi==z else 0})}}'+body)
        assert re.sub(r'\s','',text)==re.sub(r'\s','',words)
    # Existing arrows over unchanged hardware and console recordings remain tied to their shots.
    for line in original.splitlines():
        if not line.startswith('Dialogue:'):continue
        f=line.split(',',9);layer=int(f[0].split(':')[1]);a,z=[sum(float(q)*m for q,m in zip(t.split(':'),[3600,60,1])) for t in f[1:3]]
        if layer<2 or z>62.2:continue
        old=next((s for s in oldrows if s['start']<=a and z<=s['start']+s['duration']+.001),None)
        if old is None:continue
        new=next(s for s in rows if s['name']==old['name']);offset=new['start']-old['start'];f[1]=base.b.stamp(a+offset);f[2]=base.b.stamp(z+offset);lines.append(','.join(f))
    labels={'rk_board':'RK3576｜识别与显示','fpga_board':'安路 PH1A90｜图像采集与预处理','control_board':'自制电磁阀转接板｜脉冲控制'}
    for s in rows:
        label=labels.get(s['name'],s.get('label'))
        if label:lines.append(f'Dialogue: 0,{base.b.stamp(s["start"]+.15)},{base.b.stamp(s["start"]+s["duration"]-.15)},Rule,,0,0,0,,{{\\fad(120,120)}}{label}')
        if s.get('speed_label'):lines.append(f'Dialogue: 3,{base.b.stamp(s["start"]+.3)},{base.b.stamp(s["start"]+s["duration"]-.3)},Default,,0,0,0,,{{\\an3\\pos(1840,930)\\fs26\\bord1.3\\shad0\\fad(120,120)}}{s["speed_label"]}')
    for scene in rows:
        elapsed=0
        for segment in scene['source'].get('speed_segments',[]):
            length=(segment['end']-segment['start'])/segment['speed'];a=scene['start']+elapsed;z=a+length;elapsed+=length
            value=f"{segment['speed']:g}×"+(' 慢放' if segment['speed']<1 else '')
            lines.append(f'Dialogue: 3,{base.b.stamp(a+.1)},{base.b.stamp(z-.05)},Default,,0,0,0,,{{\\an3\\pos(1840,930)\\fs26\\bord1.3\\shad0\\fad(80,80)}}{value}')
    def badge(a,z,text,width=1000,y=54,size=31):
        x=960-width/2
        lines.append(f'Dialogue: 3,{base.b.stamp(a)},{base.b.stamp(z)},Default,,0,0,0,,{{\\an7\\pos({x},{y})\\p1\\bord0\\shad0\\1c&H291D0D&\\1a&H18&\\fad(150,150)}}m 0 0 l {width} 0 {width} 78 0 78')
        lines.append(f'Dialogue: 4,{base.b.stamp(a)},{base.b.stamp(z)},Default,,0,0,0,,{{\\an8\\pos(960,{y+15})\\fs{size}\\bord0\\shad0\\1c&HF3F5ED&\\fad(150,150)}}{text}')
    badge(118.8,124.9,'1080p · 60 FPS　｜　采集 → HDMI 显示 < 50 ms',1100,size=31)
    badge(126.5,133.3,'A 数字正向　　B 数字倒向　　C 白色反面',1000,size=30)
    badge(133.6,138.9,'分拣功能未开启',660,size=35)
    ass='\n'.join(lines)+'\n';assert not any(w in ass for w in ['提示词','引子','不表示','零漏检','完美筛选'])
    (P/'final.ass').write_text(ass,'utf-8-sig');(P/'captions.json').write_text(json.dumps(caps,ensure_ascii=False,indent=2),'utf8');(P/'全片字幕.srt').write_text('\n\n'.join(srt)+'\n','utf-8-sig')
    print('captions match spoken words',flush=True)


def compose():
    captions();mixbase.audio()
    synth=load('v24_material_sound',M/'视觉风格小样_v20/make_sound.py');sr=48000
    with wave.open(str(P/'混音.wav'),'rb') as w:
        audio=np.frombuffer(w.readframes(w.getnframes()),dtype='<i2').astype(np.float64).reshape(-1,2)/32768
    fx=np.zeros_like(audio);events=[]
    def add(at,hz,db=-32,pan=0):
        x=synth.bell(hz,.25);x=x/max(.001,np.max(np.abs(x)))*10**(db/20);a=round(at*sr);n=min(len(x),len(fx)-a)
        fx[a:a+n]+=x[:n,None]*np.array([np.sqrt((1-pan)/2),np.sqrt((1+pan)/2)]);events.append(dict(time=at,hz=hz,peak_dbfs=db))
    rows=json.loads((P/'timeline.json').read_text('utf8'))
    for s in rows:
        if s['name']=='tracking_short':
            for i in range(3):add(s['start']+.12+i*.12,660+i*80,-34,(i-1)*.2)
        elif s['name'] in set(EVIDENCE)|{'opening_title','chapter_board','chapter_console','chapter_logic','application','fewshot_value','trigger_short','end'}:
            add(s['start']+.12,520 if s['name'].startswith('chapter') or s['name']=='application' else 660)
    mixed=audio+fx;gain=min(1,10**(-1.8/20)/np.max(np.abs(mixed)))
    for name,data in [('证据页音效.wav',fx),('混音.wav',mixed*gain)]:
        with wave.open(str(P/name),'wb') as w:w.setnchannels(2);w.setsampwidth(2);w.setframerate(sr);w.writeframes(np.rint(np.clip(data,-1,1)*32767).astype('<i2').tobytes())
    (P/'音效对轴.json').write_text(json.dumps(dict(events=events,music_level='constant low bed',voice_and_music_source='retimed narration and same background music',gain=gain),ensure_ascii=False,indent=2),'utf8')


def assemble():
    argv=sys.argv;sys.argv=['add_transitions.py','--timeline',str(P/'timeline.json')];tr.main();sys.argv=argv;(P/'video_transitions.mp4').replace(P/'video_clean.mp4')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['prepare','footage','voice','compose','assemble','final']);a=parser.parse_args()
    if a.stage=='voice':asyncio.run(voice())
    elif a.stage=='final':mixbase.final()
    else:globals()[a.stage]()
