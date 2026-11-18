"""Only two additions to v12: a 2s project title and a 7s few-shot value segment."""
from pathlib import Path
import argparse,asyncio,copy,importlib.util,json,re,subprocess,wave
import numpy as np

P=Path(__file__).resolve().parent
M=P.parent
OLD=M/'重剪_v12'
FF=M.parent/'dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe'
OUT=P/'FPGA视觉系统_小样本价值补充_v13.mp4'
BASELINE=OLD/'FPGA视觉系统_章节与材料顺序修订_v12.mp4'
CUT=75.5
INSERT_START=77.0
DURATION=179.8

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    obj=importlib.util.module_from_spec(spec);spec.loader.exec_module(obj)
    return obj

tr=module('transition_tools',OLD/'add_transitions.py');tr.P=P
builder=module('v12_builder',OLD/'build_v12.py')

def shift(t):return round(t+(2 if t<CUT else 8),4)

def prepare():
    baseline=json.loads((OLD/'timeline.json').read_text(encoding='utf8'))
    report=json.loads((OLD/'成片核对.json').read_text(encoding='utf8'))
    assert BASELINE.stat().st_size==report['size_bytes']
    rows=[dict(name='opening_title',start=0,duration=2,file=str(P/'opening_title.mp4'),source=dict(kind='existing_title',file=str(M/'重剪_v9/title.mp4'),start=1,length=2))]
    for s in baseline:
        if s['name']=='chapter_logic':
            rows.append(dict(name='fewshot_value',start=INSERT_START,duration=7,file=str(P/'fewshot_value.mp4'),source=dict(kind='animation',script='fewshot_v13.cjs',overlap_in=.5,overlap_out=.5)))
        s=copy.deepcopy(s);s['start']=shift(s['start']);rows.append(s)
    assert max(s['start']+s['duration'] for s in rows)==DURATION
    (P/'timeline.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf8')
    (P/'fewshot_value.ass').write_text(builder.base.HEADER,encoding='utf-8-sig')
    with (P/'opening_title.log').open('w',encoding='utf8') as log:
        subprocess.run([str(FF),'-y','-hide_banner','-nostdin','-ss','1','-i',str(M/'重剪_v9/title.mp4'),'-frames:v','120',*tr.ENC,str(P/'opening_title.mp4')],stdout=log,stderr=log,check=True)
    assert tr.probe(P/'opening_title.mp4',120)
    chapters=json.loads((OLD/'章节时间.json').read_text(encoding='utf8'))
    for i,c in enumerate(chapters):c['start']=0 if i==0 else shift(c['start'])
    metadata=';FFMETADATA1\n'
    for i,c in enumerate(chapters):
        c['end']=chapters[i+1]['start'] if i+1<len(chapters) else DURATION
        metadata+=f'[CHAPTER]\nTIMEBASE=1/1000\nSTART={round(c["start"]*1000)}\nEND={round(c["end"]*1000)}\ntitle={c["title"]}\n'
    (P/'chapters.ffmeta').write_text(metadata,encoding='utf8')
    (P/'章节时间.json').write_text(json.dumps(chapters,ensure_ascii=False,indent=2),encoding='utf8')
    print('Prepared 179.8s: original 171.8 + title 2 + addition 7 - two 0.5s overlaps.',flush=True)

async def voice():
    tts=module('tts_v13',M/'重剪_v6/synthesize_voice.py');tts.P=P;tts.OUT=P/'voice';tts.OUT.mkdir(exist_ok=True);tts.RATES['fewshot_value']=0
    for f in tts.OUT.glob('*.mp3'):
        if f.stat().st_size==0:f.unlink()
    s=dict(id=24,scene='fewshot_value',start=77.65,end=83.35,spoken_text='本次十五张原图，建立三类姿态模型；场景变化，再采样训练。')
    r=await tts.generate(s)
    (P/'新增配音记录.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf8')
    print(f'New speech {r["duration"]:.2f}s, rate {r["rate"]}%',flush=True)

def write_text():
    r=json.loads((P/'新增配音记录.json').read_text(encoding='utf8'))
    records=json.loads((OLD/'配音生成记录.json').read_text(encoding='utf8'))
    for s in records:s.update(start=shift(s['start']),end=shift(s['end']))
    records.append(r);records.sort(key=lambda s:s['start'])
    caps=json.loads((OLD/'captions.json').read_text(encoding='utf8'))
    for c in caps:c.update(startMs=round(shift(c['startMs']/1000)*1000),endMs=round(shift(c['endMs']/1000)*1000))
    words=builder.renderer.v8.caption_text(r['spoken_text'])
    a=r['start']-.12;b=r['start']+r['duration']+.18
    caps.append(dict(id=r['id'],scene='fewshot_value',text=words,startMs=round(a*1000),endMs=round(b*1000),timestampMs=None,confidence=None));caps.sort(key=lambda c:c['startMs'])
    ass=[]
    for line in (OLD/'final.ass').read_text(encoding='utf-8-sig').splitlines():
        if line.startswith('Dialogue:'):
            fields=line.split(',',9)
            times=[]
            for t in fields[1:3]:
                h,m,s=map(float,t.split(':'));times.append(h*3600+m*60+s)
            assert not times[0]<CUT<times[1]
            fields[1]=builder.base.ass_time(shift(times[0]));fields[2]=builder.base.ass_time(shift(times[1]));line=','.join(fields)
        ass.append(line)
    ass.append(f'Dialogue: 1,{builder.base.ass_time(a)},{builder.base.ass_time(b)},Default,,0,0,0,,{{\\fad(120,120)}}'+words.replace('\n',r'\N'))
    (P/'final.ass').write_text('\n'.join(ass)+'\n',encoding='utf-8-sig')
    (P/'captions.json').write_text(json.dumps(caps,ensure_ascii=False,indent=2),encoding='utf8')
    (P/'配音生成记录.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf8')
    srt=[]
    for i,c in enumerate(caps,1):srt.append(f'{i}\n{builder.base.srt_time(c["startMs"]/1000)} --> {builder.base.srt_time(c["endMs"]/1000)}\n{c["text"]}')
    (P/'全片字幕.srt').write_text('\n\n'.join(srt)+'\n',encoding='utf-8-sig')
    labels=json.loads((OLD/'分拣规则与板卡标识.json').read_text(encoding='utf8'))
    for s in labels:s.update(start=shift(s['start']),end=shift(s['end']))
    (P/'分拣规则与板卡标识.json').write_text(json.dumps(labels,ensure_ascii=False,indent=2),encoding='utf8')

def audio():
    sr=48000
    def read(file):
        with wave.open(str(file),'rb') as f:
            assert f.getframerate()==sr and f.getsampwidth()==2
            return np.frombuffer(f.readframes(f.getnframes()),dtype='<i2').copy().reshape(-1,f.getnchannels())
    record=json.loads((P/'新增配音记录.json').read_text(encoding='utf8'))
    stats=subprocess.run([str(FF),'-hide_banner','-nostdin','-i',record['file'],'-af','loudnorm=I=-18:TP=-2:LRA=9:print_format=json','-f','null','-'],capture_output=True,text=True,encoding='utf8',check=True).stderr
    v=json.loads(re.findall(r'\{\s*"input_i"[\s\S]*?\}',stats)[-1])
    filt=f'loudnorm=I=-18:TP=-2:LRA=9:measured_I={v["input_i"]}:measured_TP={v["input_tp"]}:measured_LRA={v["input_lra"]}:measured_thresh={v["input_thresh"]}:offset={v["target_offset"]}:linear=true'
    subprocess.run([str(FF),'-v','error','-nostdin','-y','-i',record['file'],'-af',filt,'-ar',str(sr),'-c:a','pcm_s16le',str(P/'新增旁白_电平匹配.wav')],check=True)
    old=read(OLD/'混音.wav');music=read(OLD/'低音量配乐.wav');newvoice=read(P/'新增旁白_电平匹配.wav')
    new=music[75*sr:82*sr].astype(np.float64)/32768
    at=round((record['start']-INSERT_START)*sr)
    new[at:at+len(newvoice)]+=newvoice/32768
    gain=min(1,10**(-1.6/20)/float(np.max(np.abs(new))))
    new=np.rint(np.clip(new*gain,-1,1)*32767).astype('<i2')
    result=np.zeros((round(DURATION*sr),2),dtype='<i2');n=round(CUT*sr);overlap=sr//2
    result[:2*sr]=music[:2*sr]
    result[2*sr:2*sr+n]=old[:n]
    begin=77*sr;end=84*sr;suffix=old[n:]
    w=np.linspace(0,1,overlap,endpoint=False)[:,None]
    result[begin:begin+overlap]=np.rint(result[begin:begin+overlap]*(1-w)+new[:overlap]*w).astype('<i2')
    result[begin+overlap:end-overlap]=new[overlap:-overlap]
    result[end-overlap:end]=np.rint(new[-overlap:]*(1-w)+suffix[:overlap]*w).astype('<i2')
    result[end:]=suffix[overlap:]
    # Only the new title boundary is softened; v12 speech begins after this.
    edge=round(.03*sr)
    result[2*sr-edge:2*sr]=np.rint(result[2*sr-edge:2*sr]*np.linspace(1,0,edge)[:,None]).astype('<i2')
    result[2*sr:2*sr+edge]=np.rint(result[2*sr:2*sr+edge]*np.linspace(0,1,edge)[:,None]).astype('<i2')
    assert np.array_equal(result[2*sr+edge:begin],old[edge:75*sr])
    assert np.array_equal(result[end:],old[76*sr:])
    with wave.open(str(P/'混音.wav'),'wb') as f:
        f.setnchannels(2);f.setsampwidth(2);f.setframerate(sr);f.writeframes(result.tobytes())
    (P/'音频保持核对.json').write_text(json.dumps(dict(original_audio_preserved_outside_new_boundaries=True,new_voice_gain=gain,new_voice_measurement=v,mix_sample_peak_dbfs=round(20*np.log10(float(np.max(np.abs(result.astype(np.float64))))/32768),2)),ensure_ascii=False,indent=2),encoding='utf8')

def video():
    timeline=json.loads((OLD/'timeline.json').read_text(encoding='utf8'))
    index=next(i for i,s in enumerate(timeline) if s['name']=='console_run')
    assert timeline[index+1]['name']=='chapter_logic'
    left=Path(timeline[index]['file']);right=Path(timeline[index+1]['file']);addition=P/'fewshot_value.mp4'
    assert tr.probe(addition,420)
    parts=P/'transition_parts';parts.mkdir(exist_ok=True)
    def render(name,inputs,graph,frames):
        file=parts/name
        sources=[dict(file=str(x),size=x.stat().st_size,mtime_ns=x.stat().st_mtime_ns) for x in inputs]
        args=[]
        for x in inputs:args+=['-i',str(x)]
        tr.render(file,args,graph,frames,dict(kind='v13_insertion',sources=sources,graph=graph),parts)
        return file
    a=round(timeline[index]['duration']*60);b=round(timeline[index+1]['duration']*60)
    run=render('body_run.mp4',[left],f'[0:v]trim=start_frame=12:end_frame={a-30},setpts=PTS-STARTPTS,setsar=1,fps=60[v]',a-42)
    logic=render('body_logic.mp4',[right],f'[0:v]trim=start_frame=30:end_frame={b-12},setpts=PTS-STARTPTS,setsar=1,fps=60[v]',b-42)
    center=render('body_fewshot.mp4',[addition],'[0:v]trim=start_frame=30:end_frame=390,setpts=PTS-STARTPTS,setsar=1,fps=60[v]',360)
    def bridge(name,l,r,start,end):
        graph=(f'[0:v]trim=start_frame={start}:end_frame={end},setpts=N/(60*TB),setsar=1,fps=60[a];'
               '[1:v]trim=start_frame=0:end_frame=30,setpts=N/(60*TB),setsar=1,fps=60[b];'
               "[a][b]blend=all_expr='A*(1-T/0.5)+B*(T/0.5)':shortest=1,trim=end_frame=30,setpts=N/(60*TB)[v]")
        return render(name,[l,r],graph,30)
    first=bridge('bridge_into_fewshot.mp4',left,addition,a-30,a)
    last=bridge('bridge_out_of_fewshot.mp4',addition,right,390,420)
    old=json.loads((OLD/'transition_parts/verification.json').read_text(encoding='utf8'))
    order=[P/'opening_title.mp4'];counts=[120]
    for item in old['parts']:
        f=Path(item['file']);name=f.name
        if name==f'body_{index:02}.mp4':order.append(run);counts.append(a-42)
        elif name==f'body_{index+1:02}.mp4':order.append(logic);counts.append(b-42)
        elif name==f'bridge_{index:02}.mp4':order.extend([first,center,last]);counts.extend([30,360,30])
        else:order.append(f);counts.append(item['frames'])
    assert sum(counts)==10788
    concat=parts/'concat.txt';concat.write_text(''.join("file '"+f.as_posix()+"'\n" for f in order),encoding='utf8')
    with (parts/'assemble.log').open('w',encoding='utf8') as log:
        subprocess.run([str(FF),'-y','-hide_banner','-nostdin','-f','concat','-safe','0','-i',str(concat),'-map','0:v:0','-c:v','copy','-an','-movflags','+faststart',str(P/'video_inserted.mp4')],stdout=log,stderr=log,check=True)
    assert tr.probe(P/'video_inserted.mp4',10788)
    print('Assembled 179.8s / 10788 frames.',flush=True)

def final():
    with (P/'final_render.log').open('w',encoding='utf8') as log:
        subprocess.run([str(FF),'-y','-hide_banner','-nostdin','-i','video_inserted.mp4','-i','混音.wav','-i','chapters.ffmeta','-map','0:v:0','-map','1:a:0','-map_metadata','2','-map_chapters','2','-vf','ass=final.ass',*tr.ENC[1:],'-c:a','aac','-b:a','256k','-t',str(DURATION),'-movflags','+faststart',str(OUT)],cwd=P,stdout=log,stderr=log,check=True)
    assert tr.probe(OUT,10788)
    print('COMPLETE '+str(OUT),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--voice',action='store_true');parser.add_argument('--compose',action='store_true');parser.add_argument('--video',action='store_true');parser.add_argument('--final',action='store_true');args=parser.parse_args()
    if args.voice:asyncio.run(voice())
    elif args.compose:write_text();audio()
    elif args.video:video()
    elif args.final:final()
    else:prepare()
