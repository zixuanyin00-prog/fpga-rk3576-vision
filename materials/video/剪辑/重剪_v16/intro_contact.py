from pathlib import Path
import io, subprocess
from PIL import Image, ImageDraw, ImageFont

P=Path(__file__).resolve().parent
M=P.parent
FF=M.parent/'dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe'
shots=[('广角总览.MOV',t) for t in [0,2,4,6,8,10,12]]+[('板卡移到振动盘.MOV',t) for t in [0,3,6,9]]+[('新视频.mp4',t) for t in [0,3,6,9]]
sheet=Image.new('RGB',(1920,1940),'#0d1d29');d=ImageDraw.Draw(sheet)
font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',20)
for i,(file,t) in enumerate(shots):
 raw=subprocess.run([str(FF),'-v','error','-ss',str(t),'-i',str(M/file),'-frames:v','1','-vf','scale=640:360','-f','image2pipe','-vcodec','png','-'],capture_output=True,check=True).stdout
 x,y=i%3*640,i//3*388;sheet.paste(Image.open(io.BytesIO(raw)),(x,y+28));d.text((x+7,y+2),f'{file} {t}s',font=font,fill='white')
sheet.save(P/'intro_source_contact.jpg',quality=95)
