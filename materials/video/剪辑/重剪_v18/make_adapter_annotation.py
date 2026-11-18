"""One verified adapter label on the unchanged FPGA board footage."""
from pathlib import Path
import json,math,subprocess

P=Path(__file__).resolve().parent
M=P.parent
FF=M.parent/'dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe'
start,end='0:00:13.65','0:00:17.55'
prefix=f'Dialogue: {{layer}},{start},{end},Default,,0,0,0,,'
lines=[]

def vector(layer,x,y,color,shape,alpha='00'):
    lines.append(prefix.format(layer=layer)+f'{{\\an7\\pos({x},{y})\\fad(150,150)\\p1\\bord0\\shad0\\1c&H{color}&\\1a&H{alpha}&}}'+shape)


vector(3,1500,610,'291D0D','m 0 0 l 300 0 300 60 0 60','40')
points=[(1500,640),(1370,640),(925,520)]
for (x1,y1),(x2,y2) in zip(points,points[1:]):
    dx,dy=x2-x1,y2-y1;distance=math.hypot(dx,dy);ox,oy=-dy/distance,dx/distance
    vertices=[(x1+ox,y1+oy),(x2+ox,y2+oy),(x2-ox,y2-oy),(x1-ox,y1-oy)]
    left=math.floor(min(x for x,y in vertices));top=math.floor(min(y for x,y in vertices))
    shape='m '+' l '.join(f'{x-left:.1f} {y-top:.1f}' for x,y in vertices)
    vector(4,left,top,'CBDF78',shape)
vector(4,921,516,'CBDF78','m 4 0 b 6.2 0 8 1.8 8 4 b 8 6.2 6.2 8 4 8 b 1.8 8 0 6.2 0 4 b 0 1.8 1.8 0 4 0')
lines.append(prefix.format(layer=5)+r'{\an7\pos(1516,622)\fad(150,150)\fnMicrosoft YaHei\fs28\bord0\shad0\1c&HF3F5ED&}PCIe 转接卡')
fragment='\n'.join(lines)+'\n'
(P/'additional_adapter.ass').write_text(fragment,encoding='utf-8-sig')
annotation=dict(start=13.65,end=17.55,text='PCIe 转接卡',box=[1500,610,300,60],points=points,
    target='FPGA透明罩下方黑色PCIe转接PCB；端点在CE/FCC丝印区域',
    source='板卡移到振动盘.MOV',source_range=[3.15,7.05],
    framing='v12/fpga_board.mp4 unchanged: crop2100:1180:740:120, scale1494:840,pad213:28',
    evidence=['项目计划.md:147 PCIe-M.2互连', '板卡移到振动盘.MOV黑色转接PCB与FPGA金手指插接区域'],
    limitations='No unverified adapter vendor/model or bandwidth value asserted')
(P/'additional_adapter.json').write_text(json.dumps(annotation,ensure_ascii=False,indent=2),encoding='utf8')
base=(M/'重剪_v17/final.ass').read_text(encoding='utf-8-sig')
header='\n'.join(line for line in base.splitlines() if not line.startswith('Dialogue:'))+'\n'
(P/'adapter_preview.ass').write_text(header+fragment,encoding='utf-8-sig')
video=M/'重剪_v17/FPGA视觉系统_图像处理与工业功能_v17.mp4'
for time in [13.8,14.8,17.3]:
    subprocess.run([str(FF),'-v','error','-y','-ss',str(time),'-i',str(video),'-vf',f'setpts=PTS+{time}/TB,ass=adapter_preview.ass','-frames:v','1','-fps_mode','passthrough',str(P/f'adapter_preview_{time}.png')],cwd=P,check=True)
print('Additional PCIe adapter label prepared and previewed at 13.8,14.8,17.3s.')
