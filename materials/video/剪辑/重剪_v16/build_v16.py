"""Workcell opening, balanced highlights, linked control views and local callouts."""
from pathlib import Path
import argparse, asyncio, copy, importlib.util, json, re, subprocess

P=Path(__file__).resolve().parent
M=P.parent
OLD=M/'重剪_v15'
OUT=P/'FPGA视觉系统_全貌与联动标注_v16.mp4'


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value


b=module('v16_base',OLD/'build_v15.py')
b.P=P;b.OLD=OLD;b.OUT=OUT
b.CHANGES=[
    dict(id=6,scene='console_image',start=36.0,end=45.3,spoken_text='控制台经 R K 下发参数，F P G A 回传状态；右侧屏幕呈现对应的成像效果。'),
    dict(id=23,scene='end',start=175.05,end=179.6,spoken_text='双向调参、小样本学习、快速部署，支撑高速分拣。'),
]


def text_and_audio():
    b.captions()
    original=(P/'final.ass').read_text(encoding='utf-8-sig')
    lines=[line for line in original.splitlines() if not line.endswith('}让工件自动分开')]
    for file in ['annotations_hardware.ass','annotations_console.ass','annotations_application.ass']:
        for line in (P/file).read_text(encoding='utf-8-sig').splitlines():
            if line.startswith('Dialogue:'):
                assert line.split(',',9)[3] in ['Default','Title','Small','Rule'],(file,line)
                lines.append(line)
    (P/'final.ass').write_text('\n'.join(lines)+'\n',encoding='utf-8-sig')
    assert not any(word in '\n'.join(lines) for word in ['引子','提示词','不表示'])
    b.audio()


def video():
    tr=module('v16_transitions',M/'重剪_v12/add_transitions.py');tr.P=P
    replacements={'intro':P/'intro.mp4','console_image':P/'console_image.mp4','end':P/'end.mp4'}
    old=json.loads((M/'重剪_v12/timeline.json').read_text(encoding='utf8'))
    current=json.loads((OLD/'timeline.json').read_text(encoding='utf8'))
    by_name={s['name']:s for s in current}
    sources={'intro':json.loads((P/'source_intro.json').read_text(encoding='utf8')),
             'console_image':json.loads((P/'source_console_layout.json').read_text(encoding='utf8')),
             'end':dict(kind='animation',script=str(P/'end_v16.cjs'),duration=5)}
    timeline=copy.deepcopy(current)
    for s in timeline:
        if s['name'] in replacements:s.update(file=str(replacements[s['name']]),source=sources[s['name']])
    (P/'timeline.json').write_text(json.dumps(timeline,ensure_ascii=False,indent=2),encoding='utf8')
    parts=P/'transition_parts';parts.mkdir(exist_ok=True)
    def render(name,inputs,graph,frames):
        out=parts/name;args=[]
        for file in inputs:args+=['-i',str(file)]
        signature=dict(sources=[dict(file=str(f),size=f.stat().st_size,mtime_ns=f.stat().st_mtime_ns) for f in inputs],graph=graph,frames=frames)
        tr.render(out,args,graph,frames,signature,parts)
        return out
    paths=[Path(line[6:-1]) for line in (OLD/'transition_parts/concat.txt').read_text(encoding='utf8').splitlines()]
    for i,file in enumerate(paths):
        match=re.fullmatch(r'body_(\d+)\.mp4',file.name)
        if match:
            index=int(match[1]);scene=old[index]
            if scene['name'] not in replacements:continue
            n=round(scene['duration']*60);a=12 if index else 0;c=n-(12 if index<len(old)-1 else 0)
            paths[i]=render(file.name,[replacements[scene['name']]],f'[0:v]trim=start_frame={a}:end_frame={c},setpts=PTS-STARTPTS,setsar=1,fps=60[v]',c-a)
        match=re.fullmatch(r'bridge_(\d+)\.mp4',file.name)
        if match:
            index=int(match[1]);left,right=old[index:index+2]
            if not {left['name'],right['name']} & replacements.keys():continue
            inputs=[replacements.get(s['name'],Path(by_name[s['name']]['file'])) for s in [left,right]]
            paths[i]=render(file.name,inputs,tr.bridge_graph(round(left['duration']*60)),24)
    concat=parts/'concat.txt';concat.write_text(''.join("file '"+f.as_posix()+"'\n" for f in paths),encoding='utf8')
    with (parts/'assemble.log').open('w',encoding='utf8') as log:
        subprocess.run([str(b.FF),'-y','-hide_banner','-nostdin','-f','concat','-safe','0','-i',str(concat),'-map','0:v:0','-c:v','copy','-an','-movflags','+faststart',str(P/'video_clean.mp4')],stdout=log,stderr=log,check=True)
    assert tr.probe(P/'video_clean.mp4',10788)
    print('v16 clean video assembled, 179.8s',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['voice','compose','video','final']);args=parser.parse_args()
    if args.stage=='voice':asyncio.run(b.voice())
    elif args.stage=='compose':text_and_audio()
    elif args.stage=='video':video()
    else:b.final()
