from pathlib import Path
import io,subprocess
from PIL import Image,ImageDraw,ImageFont

P=Path(__file__).resolve().parent
M=P.parent
FF=M.parent/'dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe'
SOURCE=M/'重剪_v15/video_clean.mp4'
times=[8,10,12,13.5,15,17,18.5,20,22,23.5,25,26.5,28,29.5,31.5]
sheet=Image.new('RGB',(1920,1940),'#0d1d29');d=ImageDraw.Draw(sheet);font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',20)
for i,t in enumerate(times):
 raw=subprocess.run([str(FF),'-v','error','-ss',str(t),'-i',str(SOURCE),'-frames:v','1','-vf','scale=640:360','-f','image2pipe','-vcodec','png','-'],capture_output=True,check=True).stdout
 x,y=i%3*640,i//3*388;sheet.paste(Image.open(io.BytesIO(raw)),(x,y+28));d.text((x+7,y+2),f'{t}s',font=font,fill='white')
sheet.save(P/'hardware_source_contact.jpg',quality=95)
