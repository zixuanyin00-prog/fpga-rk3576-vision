const {chromium}=require('playwright');
const fs=require('node:fs');
const {pathToFileURL}=require('node:url');
const path=require('node:path');
async function main(){
  const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  const page=await browser.newPage({viewport:{width:1600,height:1100},deviceScaleFactor:1});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(pathToFileURL(path.join(__dirname,'preview.html')).href);
  await page.waitForFunction(()=>Boolean(window.previewControl));
  const states=[];
  for(const t of [0,8.99,9,9.4,23.99,24,40.99,41,53.99,54,65.99,66,86.99,87,99.99]){
    await page.evaluate(t=>window.previewControl.setTime(t),t);
    states.push(await page.evaluate(()=>window.previewControl.getState()));
  }
  await page.evaluate(()=>window.previewControl.setTime(31));
  await page.screenshot({path:path.join(__dirname,'output/browser-preview.png'),fullPage:true});
  await page.locator('#play').click();
  await page.waitForTimeout(350);
  await page.locator('#play').click();
  const afterPlay=await page.evaluate(()=>window.previewControl.getState());
  if(afterPlay.time<=31||afterPlay.playing)throw new Error('Play / pause failed');
  await page.locator('#chapters button').nth(5).click();
  const afterChapter=await page.evaluate(()=>window.previewControl.getState());
  if(Math.abs(afterChapter.time-66.02)>.1)throw new Error('Chapter seek failed');
  await page.locator('#seek').fill('49');
  const afterSeek=await page.evaluate(()=>window.previewControl.getState());
  if(afterSeek.time!==49)throw new Error('Timeline seek failed');
  await page.setViewportSize({width:390,height:850});
  await page.waitForTimeout(200);
  const mobile=await page.evaluate(()=>({viewport:innerWidth,scroll:document.documentElement.scrollWidth,canvas:document.querySelector('canvas').getBoundingClientRect().toJSON()}));
  if(mobile.scroll>mobile.viewport)throw new Error('Mobile horizontal overflow');
  await page.screenshot({path:path.join(__dirname,'output/browser-mobile.png'),fullPage:true});
  await browser.close();
  if(errors.length)throw new Error(errors.join('\n'));
  const report={passed:true,boundaryFrames:states.length,playPause:true,chapterSeek:true,timelineSeek:true,mobile,errors};
  fs.writeFileSync(path.join(__dirname,'output/preview-verification.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}
main().catch(e=>{console.error(e);process.exitCode=1;});
