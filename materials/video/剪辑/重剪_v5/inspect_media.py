from pathlib import Path
import subprocess, io
from PIL import Image, ImageDraw, ImageFont

P=Path(__file__).resolve().parent
M=P.parent
font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',19)
rows=[]
console=next(M.glob('控制台全流程*.mp4'))
for file,times in [(console,[25,45,88,100,110,120,310,320,330,345,487,518]),(M/'实拍ISP使用.MOV',[25,45,88,100,110,120]),(M/'部署后的运行结果.mp4',[3,10,18])]:
    thumbs=[]
    for t in times:
        data=subprocess.check_output(['ffmpeg','-v','error','-ss',str(t),'-i',str(file),'-frames:v','1','-vf','scale=560:350:force_original_aspect_ratio=decrease,pad=560:350:(ow-iw)/2:(oh-ih)/2','-f','image2pipe','-vcodec','mjpeg','-threads','1','-'])
        im=Image.open(io.BytesIO(data)).convert('RGB')
        tile=Image.new('RGB',(560,385),'#14232c');tile.paste(im,(0,35))
        ImageDraw.Draw(tile).text((8,7),f'{file.name[:12]} · {t}s',font=font,fill='white')
        thumbs.append(tile)
    for i in range(0,len(thumbs),3):
        row=Image.new('RGB',(1680,385),'#14232c')
        for j,im in enumerate(thumbs[i:i+3]):row.paste(im,(j*560,0))
        rows.append(row)
sheet=Image.new('RGB',(1680,len(rows)*385))
for i,row in enumerate(rows):sheet.paste(row,(0,i*385))
sheet.save(P/'素材检视.jpg')
