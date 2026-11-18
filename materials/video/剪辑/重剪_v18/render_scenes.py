"""Render the explicit offline source intervals recorded in edit_plan.json."""
from pathlib import Path
import concurrent.futures, importlib.util, json, subprocess

P=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('build18',P/'build_v18.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)


def render(scene):
    source=scene['source'];file=Path(source['file']);assert file.is_file(),file
    start=source['start'];speed=source.get('playback_speed',1)
    count=round(scene['duration']*60)
    assert source['length']>=scene['duration']*speed-.001
    filters=[f'setpts=(PTS-STARTPTS)/{speed}']
    if source.get('crop'):filters.append(source['crop'])
    filters+=source.get('filters',[
        'scale=1920:1080:flags=lanczos',
        'drawbox=x=0:y=930:w=iw:h=150:color=0x0d1d29@0.65:t=fill'
    ])
    filters+=['setsar=1','fps=60']
    graph=','.join(filters)
    output=P/(scene['name']+'.mp4');marker=output.with_suffix('.source.json')
    sig=dict(source=source,duration=scene['duration'],file_size=file.stat().st_size,mtime_ns=file.stat().st_mtime_ns,filter=graph)
    if marker.is_file() and json.loads(marker.read_text(encoding='utf8'))==sig and b.tr.probe(output,count):
        print('reuse '+scene['name'],flush=True);return
    with (P/(scene['name']+'.log')).open('w',encoding='utf8') as log:
        # Read one small frame-boundary margin; the exact output frame count
        # selects the authored interval without padding a still at the end.
        subprocess.run([str(b.b.FF),'-y','-hide_banner','-nostdin','-ss',str(start),'-t',str(source['length']+.1),'-i',str(file),
                        '-vf',graph,'-frames:v',str(count),*b.tr.ENC,str(output)],stdout=log,stderr=log,check=True)
    assert b.tr.probe(output,count),(scene['name'],count)
    marker.write_text(json.dumps(sig,ensure_ascii=False,indent=2),encoding='utf8')
    print('rendered '+scene['name'],flush=True)


if __name__=='__main__':
    scenes=json.loads((P/'edit_plan.json').read_text(encoding='utf8'))['scenes']
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(render,scenes))
