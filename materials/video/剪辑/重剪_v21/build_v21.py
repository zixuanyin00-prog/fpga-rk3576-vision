"""Restyle all authored animations while preserving the approved 179.8s edit."""
from pathlib import Path
import argparse,copy,hashlib,importlib.util,json,re,shutil,subprocess,sys,wave
import numpy as np

P=Path(__file__).resolve().parent
M=P.parent
OLD=M/'重剪_v19'
FF=M.parent/'dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe'
OUT=P/'FPGA视觉系统_B曲面装置_全片_v21.mp4'
DURATION=179.8
NAMES=['opening_title','chapter_board','chapter_console','chapter_logic','application','isp_methods','fewshot_value','compression_pcie','tracking_short','trigger_short','dimensions','alerts','end']


def load(name,file):
    spec=importlib.util.spec_from_file_location(name,file)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    return m


def stamp(t):
    c=round(t*100)
    return f'{c//360000}:{c//6000%60:02d}:{c//100%60:02d}.{c%100:02d}'


def seconds(t):
    h,m,s=map(float,t.split(':'))
    return h*3600+m*60+s


def prepare():
    rows=json.loads((OLD/'timeline.json').read_text('utf8'))
    for s in rows:
        if s['name'] in NAMES:
            s['previous_source']=copy.deepcopy(s['source'])
            s['file']=str(P/'scenes'/(s['name']+'.mp4'))
            s['source']=dict(renderer=str(P/'scenes_b.cjs'),geometry=str(P/'geometry_b.cjs'),native_render_fps=60,frame_step=1/60)
    assert round(sum(s['duration']*60 for s in rows))==10788
    (P/'timeline.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),'utf8')
    (P/'render_plan.json').write_text(json.dumps([s for s in rows if s['name'] in NAMES],ensure_ascii=False,indent=2),'utf8')
    for name in ['chapters.ffmeta','章节时间.json','captions.json','全片字幕.srt','配音生成记录.json','字幕配音逐句核对.json']:
        shutil.copy2(OLD/name,P/name)
    # Split only the visual styling at light/dark scene boundaries. Words and voice timing are unchanged.
    intervals=[(s['start'],s['start']+s['duration']) for s in rows if s['name'] in NAMES]
    original=(OLD/'final.ass').read_text('utf-8-sig')
    result=[]
    for line in original.splitlines():
        if line.startswith('[Events]'):
            result.append('Style: BSubtitle,Microsoft YaHei,44,&H00473718,&H00473718,&H00E9DDCB,&H00000000,0,0,0,0,100,100,0.5,0,1,0,0,2,100,100,44,1')
        if not line.startswith('Dialogue: 1,'):
            result.append(line);continue
        f=line.split(',',9);a,z=seconds(f[1]),seconds(f[2])
        cuts=sorted({a,z,*[x for span in intervals for x in span if a<x<z]})
        body=re.sub(r'\\fad\([^)]*\)','',f[9])
        for lo,hi in zip(cuts,cuts[1:]):
            r=f.copy();r[1],r[2]=stamp(lo),stamp(hi)
            light=any(x<=(lo+hi)/2<y for x,y in intervals)
            r[3]='BSubtitle' if light else 'Default'
            r[9]=f'{{\\fad({100 if lo==a else 0},{100 if hi==z else 0})}}'+body
            result.append(','.join(r))
    ass='\n'.join(result)+'\n'
    assert not any(w in ass for w in ['提示词','引子','不表示','零漏检','完美筛选'])
    (P/'final.ass').write_text(ass,'utf-8-sig')
    print('Prepared: 13 replacements, 48.0s; total 179.8s',flush=True)


def assemble():
    tr=load('v21_transitions',M/'重剪_v12/add_transitions.py');tr.P=P
    rows=json.loads((P/'timeline.json').read_text('utf8'))
    parts=P/'transition_parts';parts.mkdir(exist_ok=True)
    order=[]
    for i,s in enumerate(rows):
        n=round(s['duration']*60);a=12 if i else 0;z=n-(12 if i<len(rows)-1 else 0)
        if s['name'] in NAMES:
            file=Path(s['file']);stat=file.stat();out=parts/f'body_{i:02}.mp4'
            tr.render(out,['-i',str(file)],f'[0:v]trim=start_frame={a}:end_frame={z},setpts=PTS-STARTPTS,setsar=1,fps=60[v]',z-a,dict(file=str(file),size=stat.st_size,mtime_ns=stat.st_mtime_ns,a=a,z=z),parts)
        else:out=OLD/'transition_parts'/f'body_{i:02}.mp4'
        assert tr.probe(out,z-a),str(out)
        order.append(out)
        if i==len(rows)-1:continue
        nxt=rows[i+1]
        if {s['name'],nxt['name']} & set(NAMES):
            out=parts/f'bridge_{i:02}.mp4';files=[Path(s['file']),Path(nxt['file'])]
            inputs=[arg for file in files for arg in ['-i',str(file)]]
            sig=[dict(file=str(file),size=file.stat().st_size,mtime_ns=file.stat().st_mtime_ns) for file in files]
            tr.render(out,inputs,tr.bridge_graph(n),24,dict(sources=sig,graph_version=2,frames=24),parts)
        else:out=OLD/'transition_parts'/f'bridge_{i:02}.mp4'
        assert tr.probe(out,24),str(out)
        order.append(out)
    concat=parts/'concat.txt'
    concat.write_text(''.join("file '"+f.as_posix()+"'\n" for f in order),'utf8')
    with (parts/'assemble.log').open('w',encoding='utf8') as log:
        subprocess.run([str(FF),'-hide_banner','-y','-nostdin','-f','concat','-safe','0','-i',str(concat),'-map','0:v','-c:v','copy','-an','-movflags','+faststart',str(P/'video_clean.mp4')],stdout=log,stderr=log,check=True)
    assert tr.probe(P/'video_clean.mp4',10788)
    print('Assembled 10788 frames',flush=True)


def sound():
    synth=load('b_sound',M/'视觉风格小样_v20/make_sound.py')
    sr=48000
    with wave.open(str(OLD/'混音.wav'),'rb') as w:
        assert (w.getframerate(),w.getnchannels(),w.getsampwidth())==(sr,2,2)
        base=np.frombuffer(w.readframes(w.getnframes()),dtype='<i2').astype(np.float64).reshape(-1,2)/32768
    fx=np.zeros_like(base);events=[]
    rows=json.loads((P/'timeline.json').read_text('utf8'))
    def add(at,x,peak_db=-29,pan=0):
        start=round(at*sr);n=min(len(x),len(fx)-start)
        normalized=x[:n]/max(.001,np.max(np.abs(x)))
        fx[start:start+n]+=normalized[:,None]*10**(peak_db/20)*np.array([np.sqrt((1-pan)/2),np.sqrt((1+pan)/2)])
    for s in rows:
        if s['name'] not in NAMES:continue
        at=s['start']+.12
        if s['name']!='tracking_short':
            add(at,synth.bell(520 if 'chapter' in s['name'] or s['name']=='application' else 660,.32),-29)
            events.append(dict(scene=s['name'],time=at,type='short ceramic resonance',peak_dbfs=-29))
        if s['duration']>2:
            duration=.32;t=np.arange(round(duration*sr))/sr
            add(at-.08,synth.noise(duration,1800,5400)*np.sin(np.pi*t/duration)**2,-36,-.15)
        if s['name']=='tracking_short':
            for offset,hz in [(.18,660),(.94,740),(1.7,830)]:
                add(s['start']+offset,synth.bell(hz,.22),-31,(offset-1.2)*.5)
                events.append(dict(scene=s['name'],time=s['start']+offset,type='observation resonance',peak_dbfs=-31))
            add(s['start']+2.45,synth.bell(440,.35)+synth.bell(660,.35)*.25,-29)
            events.append(dict(scene=s['name'],time=s['start']+2.45,type='confirmed resonance',peak_dbfs=-29))
        if s['name']=='trigger_short':
            add(s['start']+1.84,synth.wood(.075,380),-33)
            events.append(dict(scene=s['name'],time=s['start']+1.84,type='control pulse touch',peak_dbfs=-33))
    mix=base+fx
    gain=min(1,10**(-1.8/20)/np.max(np.abs(mix)))
    for file,data in [('曲面装置音效.wav',fx),('混音.wav',mix*gain)]:
        with wave.open(str(P/file),'wb') as w:
            w.setnchannels(2);w.setsampwidth(2);w.setframerate(sr)
            w.writeframes(np.rint(np.clip(data,-1,1)*32767).astype('<i2').tobytes())
    (P/'sound_design.json').write_text(json.dumps(dict(original_voice_and_constant_bgm_unchanged=True,added_original_procedural_sfx=True,events=events,global_gain_db=round(20*np.log10(gain),3)),ensure_ascii=False,indent=2),'utf8')
    print('Sound complete',flush=True)


def final():
    with (P/'final_render.log').open('w',encoding='utf8') as log:
        subprocess.run([str(FF),'-hide_banner','-y','-nostdin','-i','video_clean.mp4','-i','混音.wav','-i','chapters.ffmeta','-map','0:v:0','-map','1:a:0','-map_metadata','2','-map_chapters','2','-vf','ass=final.ass','-c:v','h264_nvenc','-preset','p5','-cq','18','-b:v','0','-pix_fmt','yuv420p','-r','60','-video_track_timescale','60000','-c:a','aac','-b:a','256k','-t',str(DURATION),'-movflags','+faststart',str(OUT)],cwd=P,stdout=log,stderr=log,check=True)
    print('COMPLETE '+str(OUT),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['prepare','assemble','sound','final']);args=parser.parse_args()
    globals()[args.stage]()
