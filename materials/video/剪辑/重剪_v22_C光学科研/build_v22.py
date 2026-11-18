"""C alternate full film, authorized after B completed before Beijing 05:00."""
from pathlib import Path
import argparse,importlib.util,json,subprocess,wave
import numpy as np

P=Path(__file__).resolve().parent
M=P.parent
spec=importlib.util.spec_from_file_location('b_verified_pipeline',M/'重剪_v21/build_v21.py')
impl=importlib.util.module_from_spec(spec);spec.loader.exec_module(impl)
OUT=P/'FPGA视觉系统_C光学科研_全片_v22.mp4'
impl.P=P;impl.OUT=OUT
OLD=impl.OLD;FF=impl.FF;DURATION=impl.DURATION;NAMES=impl.NAMES


def prepare():
    impl.prepare()
    rows=json.loads((P/'timeline.json').read_text('utf8'))
    for s in rows:
        if s['name'] in NAMES:
            s['source'].update(renderer=str(P/'scenes_c.cjs'),geometry=str(M/'重剪_v21/geometry_b.cjs'))
    (P/'timeline.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),'utf8')
    (P/'render_plan.json').write_text(json.dumps([s for s in rows if s['name'] in NAMES],ensure_ascii=False,indent=2),'utf8')
    # C reserves a dark subtitle area in every scene, so the original white type applies.
    (P/'final.ass').write_bytes((OLD/'final.ass').read_bytes())
    print('C timeline: original voice/subtitles, dark background; 179.8s',flush=True)


def sound():
    synth=impl.load('c_synthesis',M/'视觉风格小样_v20/make_sound.py');sr=48000
    with wave.open(str(OLD/'混音.wav'),'rb') as w:
        assert (w.getframerate(),w.getnchannels(),w.getsampwidth())==(sr,2,2)
        base=np.frombuffer(w.readframes(w.getnframes()),dtype='<i2').astype(np.float64).reshape(-1,2)/32768
    fx=np.zeros_like(base);events=[]
    def add(at,x,peak_db=-30,pan=0):
        start=round(at*sr);n=min(len(x),len(fx)-start)
        x=x[:n]/max(.001,np.max(np.abs(x)))
        fx[start:start+n]+=x[:,None]*10**(peak_db/20)*np.array([np.sqrt((1-pan)/2),np.sqrt((1+pan)/2)])
        events.append(dict(time=at,peak_dbfs=peak_db,pan=pan))
    for s in json.loads((P/'timeline.json').read_text('utf8')):
        if s['name'] not in NAMES:continue
        at=s['start']
        if 'chapter' in s['name'] or s['name'] in ['opening_title','application']:
            add(at+.08,synth.wood(.055,410),-30,-.1)
            add(at+.17,synth.wood(.04,590),-34,.1)
            seconds=.17;t=np.arange(round(seconds*sr))/sr
            add(at+.04,synth.noise(seconds,1000,5000)*np.sin(np.pi*t/seconds)**2,-36)
        elif s['name'] not in ['tracking_short','trigger_short']:
            add(at+.12,synth.wood(.06,470),-33)
        if s['name']=='tracking_short':
            for dt,hz,pan in [(.18,420,-.3),(.94,520,0),(1.7,640,.3)]:
                add(at+dt,synth.wood(.045,hz),-31,pan)
            add(at+2.45,synth.beep(820,.07),-33,-.1)
            add(at+2.57,synth.beep(1095,.09),-34,.1)
        if s['name']=='trigger_short':
            add(at+1.15,synth.wood(.045,410),-34)
            add(at+1.84,synth.wood(.055,680),-31)
    mix=base+fx;gain=min(1,10**(-1.8/20)/np.max(np.abs(mix)))
    for file,data in [('光学科研音效.wav',fx),('混音.wav',mix*gain)]:
        with wave.open(str(P/file),'wb') as w:
            w.setnchannels(2);w.setsampwidth(2);w.setframerate(sr)
            w.writeframes(np.rint(np.clip(data,-1,1)*32767).astype('<i2').tobytes())
    (P/'sound_design.json').write_text(json.dumps(dict(style='C',original_voice_and_constant_bgm_unchanged=True,source_audio='v19 mixed voice and music',added_original_procedural_sfx=True,events=events,global_gain_db=round(20*np.log10(gain),3)),ensure_ascii=False,indent=2),'utf8')
    print('C original relay / optical shutter sound ready',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['prepare','assemble','sound','final']);args=parser.parse_args()
    if args.stage=='prepare':prepare()
    elif args.stage=='sound':sound()
    else:getattr(impl,args.stage)()
