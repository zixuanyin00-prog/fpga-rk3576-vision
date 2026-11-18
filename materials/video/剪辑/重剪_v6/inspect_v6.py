from pathlib import Path
import subprocess, io
from PIL import Image, ImageDraw, ImageFont

P=Path(__file__).resolve().parent
M=P.parent
FF=M.parent/'dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe'
console=next(M.glob('控制台全流程*.mp4'))
shots=[(console,t) for t in [162,166,170,174,489,493,497,501,520,523,526,530]]
shots += [(M/'部署后的运行结果.mp4',t) for t in [7,10,14,18,20]]
shots += [(console,t) for t in [26,40,88,105,117]]
out=Image.new('RGB',(1680,385*((len(shots)+2)//3)),'#11202a')
font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',18)
draw=ImageDraw.Draw(out)
for i,(file,t) in enumerate(shots):
    data=subprocess.check_output([str(FF),'-v','error','-ss',str(t),'-i',str(file),'-frames:v','1','-vf','scale=560:350:force_original_aspect_ratio=decrease,pad=560:350:(ow-iw)/2:(oh-ih)/2','-f','image2pipe','-vcodec','mjpeg','-threads','1','-'])
    out.paste(Image.open(io.BytesIO(data)).convert('RGB'),((i%3)*560,(i//3)*385+35))
    draw.text(((i%3)*560+8,(i//3)*385+6),file.name[:8]+' '+str(t),font=font,fill='white')
out.save(P/'ui_review.jpg')
