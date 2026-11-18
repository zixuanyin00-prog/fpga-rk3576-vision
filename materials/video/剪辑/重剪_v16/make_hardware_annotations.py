"""Six measured-position callouts for stable hardware/workcell moments."""
from pathlib import Path
import io,json,math,subprocess
from PIL import Image,ImageDraw,ImageFont

P=Path(__file__).resolve().parent
M=P.parent
FF=M.parent/'dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe'
ROWS=[
 dict(id='rk',start=8.0,end=12.55,text='模型识别 · 画面显示',box=[1260,794,330,60],points=[[1280,794],[1280,730],[1130,505]],target='RK03大绿色底板背面',evidence='重剪_v12/板卡身份与功能核对.txt：RK03底板背面与原厂手册PDF第8页相符',preview=10),
 dict(id='fpga',start=13.35,end=17.55,text='图像采集\\N与预处理',box=[1720,290,184,98],points=[[1720,338],[1605,338],[1070,430]],target='透明罩内长散热器对应FPGA采集处理卡',evidence='重剪_v12/板卡身份与功能核对.txt：MLK-PFAC100丝印及原厂手册PDF第6页实物布局',preview=14.5),
 dict(id='control',start=18.35,end=20.55,text='脉冲联动电磁阀',box=[1420,238,282,60],points=[[1420,268],[1350,268],[1090,465]],target='独立小型电磁阀控制转接模块整体，不指认未知管脚协议',evidence='重剪_v12/板卡身份与功能核对.txt：最终action_gpio.py使用GPIO控制脉冲，未知板卡型号不作推断',preview=19.3),
 dict(id='camera',start=23.55,end=26.65,text='相机采集',box=[1250,198,190,60],points=[[1250,228],[1150,228],[970,330]],target='固定拍摄振动盘的相机镜头',evidence='当前station新视频10.8–14.45s连续原速段稳定实拍；23.5/24/25/26/26.5s实际截图核对',preview=24.5),
 dict(id='bowl',start=23.75,end=26.65,text='振动盘连续送料',box=[200,735,280,60],points=[[480,765],[590,765],[750,700]],target='振动盘送料轨道区域',evidence='相机下方可见送料轨道，指向轨道整体而非具体电阻；快速运镜开始前退出',preview=24.5),
 dict(id='hdmi',start=29.65,end=33.5,text='HDMI现场显示',box=[930,325,250,60],points=[[930,355],[840,355],[740,585]],target='HDMI显示屏内现场图像区域',evidence='新视频实拍运镜已经到达HDMI后的稳定29.65–33.5s；不虚构实时识别框或触发统计',preview=31),
]

def time(t):
 v=round(t*100);h,v=divmod(v,360000);m,v=divmod(v,6000);s,v=divmod(v,100)
 return f'{h}:{m:02}:{s:02}.{v:02}'

def dialogue(r,layer,text):
 return f'Dialogue: {layer},{time(r["start"])},{time(r["end"])},Default,,0,0,0,,{text}'

lines=[]
for r in ROWS:
 x,y,w,h=r['box']
 fade=r'\fad(150,150)'
 lines.append(dialogue(r,3,fr'{{\an7\pos({x},{y}){fade}\p1\bord0\shad0\1c&H291D0D&\1a&H40&}}m 0 0 l {w} 0 {w} {h} 0 {h}'))
 for a,b in zip(r['points'],r['points'][1:]):
  dx,dy=b[0]-a[0],b[1]-a[1];length=math.hypot(dx,dy);nx,ny=-dy/length,dx/length
  points=[[a[0]+nx,a[1]+ny],[b[0]+nx,b[1]+ny],[b[0]-nx,b[1]-ny],[a[0]-nx,a[1]-ny]]
  ox=math.floor(min(p[0] for p in points));oy=math.floor(min(p[1] for p in points))
  path='m '+' l '.join(' '.join(f'{value:.1f}' for value in [p[0]-ox,p[1]-oy]) for p in points)
  lines.append(dialogue(r,4,fr'{{\an7\pos({ox},{oy}){fade}\p1\bord0\shad0\1c&HCBDF78&}}'+path))
 tx,ty=r['points'][-1]
 lines.append(dialogue(r,4,fr'{{\an7\pos({tx-4},{ty-4}){fade}\p1\bord0\shad0\1c&HCBDF78&}}m 4 0 b 6.2 0 8 1.8 8 4 b 8 6.2 6.2 8 4 8 b 1.8 8 0 6.2 0 4 b 0 1.8 1.8 0 4 0'))
 lines.append(dialogue(r,5,fr'{{\an7\pos({x+16},{y+12}){fade}\fnMicrosoft YaHei\fs28\bord0\shad0\1c&HF3F5ED&}}'+r['text']))

(P/'annotations_hardware.ass').write_text('\n'.join(lines)+'\n',encoding='utf-8-sig')
(P/'annotations_hardware.json').write_text(json.dumps(dict(annotations=ROWS,font_size=28,line_colour='#78dfcb',background='#0d1d29',max_concurrent=2),ensure_ascii=False,indent=2),encoding='utf8')
original=(M/'重剪_v15/final.ass').read_text(encoding='utf-8-sig')
(P/'annotations_hardware_preview.ass').write_text(original+'\n'+'\n'.join(lines)+'\n',encoding='utf-8-sig')
sheet=Image.new('RGB',(1920,776),'#0d1d29');d=ImageDraw.Draw(sheet);font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',20)
for i,t in enumerate([10,14.5,19.3,24.5,26.2,31]):
 raw=subprocess.run([str(FF),'-v','error','-ss',str(t),'-i',str(M/'重剪_v15/video_clean.mp4'),'-frames:v','1','-vf',f'setpts=PTS+{t}/TB,ass=annotations_hardware_preview.ass,scale=640:360','-f','image2pipe','-vcodec','png','-'],cwd=P,capture_output=True,check=True).stdout
 x,y=i%3*640,i//3*388;sheet.paste(Image.open(io.BytesIO(raw)),(x,y+28));d.text((x+7,y+2),f'{t}s',font=font,fill='white')
sheet.save(P/'hardware_annotations_preview.jpg',quality=95)
print('Prepared six callouts, 30 ASS Dialogue rows.',flush=True)
