# -*- coding: utf-8 -*-
"""多标签就绪检测、人工处理队列及按原序号截图。"""
import argparse
import os
import json
import time
from datetime import datetime
from pathlib import Path

from PIL import ImageStat
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import SessionNotCreatedException, WebDriverException
from website_screenshot import read_urls, normalize_screenshot, encode_jpeg_under_target
from screenshot_stability import stable_capture

BASE = Path(__file__).resolve().parent
PROBE = (BASE / 'screenshot_probe.js').read_text(encoding='utf-8')

def create_driver(headless, page_load_strategy='none', user_data_dir=None, profile_directory='Default'):
    options = Options()
    options.page_load_strategy = page_load_strategy
    if headless:
        options.add_argument('--headless=new')
    if user_data_dir:
        user_data_dir=Path(user_data_dir).resolve()
        user_data_dir.mkdir(parents=True,exist_ok=True)
        options.add_argument(f'--user-data-dir={user_data_dir}')
        if profile_directory:
            options.add_argument(f'--profile-directory={profile_directory}')
    options.add_argument('--window-size=1640,1000')
    options.add_argument('--disable-background-timer-throttling')
    options.add_argument('--disable-renderer-backgrounding')
    options.add_argument('--disable-backgrounding-occluded-windows')
    options.add_argument('--no-first-run')
    options.add_argument('--no-default-browser-check')
    # 使用本机 Chrome 的真实 UA，不伪装旧版本或修改验证相关浏览器属性。
    driver_path=os.environ.get('SCREENSHOT_CHROMEDRIVER')
    last_error=None
    for attempt in range(1,5):
        try:
            driver = webdriver.Chrome(options=options, service=Service(driver_path) if driver_path else Service())
            break
        except (SessionNotCreatedException, WebDriverException) as exc:
            last_error=exc
            if attempt==4:
                raise
            if user_data_dir:
                print(f'Chrome Profile可能仍在释放，{attempt}/4 启动失败，2秒后重试：{user_data_dir}',flush=True)
            time.sleep(2)
    else:
        raise last_error
    driver.set_script_timeout(10)
    return driver

def viewport(driver):
    driver.execute_cdp_cmd('Emulation.setDeviceMetricsOverride', dict(
        width=1600, height=900, deviceScaleFactor=1, mobile=False))

def suspicious(image):
    gray=image.crop((0,100,1600,850)).resize((160,75)).convert('L')
    hist=gray.histogram()
    black=sum(hist[:18])/12000
    white=sum(hist[242:])/12000
    return black>0.65 or white>0.92 or ImageStat.Stat(gray).stddev[0]<5

def main():
    p=argparse.ArgumentParser(description='最多10个网页并行加载，按原始序号输出首屏')
    p.add_argument('--input',type=Path,default=BASE/'urls.txt')
    p.add_argument('--output',type=Path,default=BASE/'output')
    p.add_argument('--tabs',type=int,default=10)
    p.add_argument('--timeout',type=float,default=60)
    p.add_argument('--min-wait',type=float,default=8)
    p.add_argument('--headless',action='store_true')
    p.add_argument('--manual-each',action='store_true')
    modes=p.add_mutually_exclusive_group()
    modes.add_argument('-d','--interactive',dest='mode',action='store_const',const='default',help='人工辅助截图（默认模式）')
    modes.add_argument('-f',dest='mode',action='store_const',const='force',help='无人值守：稳定后截图，超时直接截图')
    p.set_defaults(mode='default')
    args=p.parse_args()
    if args.mode=='force' and args.manual_each:
        p.error('-f 不能与 --manual-each 同时使用')
    args.interactive = args.mode=='default'
    if not 1<=args.tabs<=10 or args.timeout<=0 or args.min_wait<0:
        p.error('tabs 应为1~10；timeout必须大于0；min-wait不能为负数')
    if args.headless and args.interactive:
        p.error('人工辅助模式需要显示浏览器；无界面运行请使用 -f --headless')
    urls=read_urls(args.input.resolve())
    # 每次单独目录，失败项不会被上一次同名图片冒充。
    out=args.output.resolve()/datetime.now().strftime('run_%Y%m%d_%H%M%S_%f')
    out.mkdir(parents=True)
    tasks=[dict(index=i,url=u,status='queued',reason='') for i,u in enumerate(urls,1)]
    driver=create_driver(args.headless)

    def report():
        fields=('index','url','final_url','status','reason','filename','bytes','review_status')
        (out/'results.json').write_text(json.dumps(
            [{k:t[k] for k in fields if k in t} for t in tasks],ensure_ascii=False,indent=2),encoding='utf-8')

    def close(t):
        driver.switch_to.window(t['handle'])
        # 保留一个空标签，避免最后一个标签关闭后整个浏览器退出。
        if len(driver.window_handles)==1:
            driver.switch_to.new_window('tab')
            driver.switch_to.window(t['handle'])
        driver.close()
        driver.switch_to.window(driver.window_handles[0])

    def save(t, image, forced=False):
        data=encode_jpeg_under_target(image,100*1024)
        filename=f"img{t['index']}.jpg"
        (out/filename).write_bytes(data)
        status = forced if isinstance(forced,str) else ('manual_saved' if forced else 'saved')
        t.update(status=status,filename=filename,bytes=len(data),review_status='pending_ai_review')
        print(f"已保存 {filename}：1600×900，{len(data)/1024:.1f}KB",flush=True)
        close(t)
        report()

    try:
        while any(t['status'] in ('queued','loading','manual') for t in tasks):
            active=[t for t in tasks if t['status'] in ('loading','manual')]
            for t in [t for t in tasks if t['status']=='queued'][:args.tabs-len(active)]:
                driver.switch_to.new_window('tab')
                viewport(driver)
                t.update(handle=driver.current_window_handle,status='loading',start=time.monotonic(),good=0)
                try:
                    driver.get(t['url'])
                except Exception as exc:
                    t.update(status='manual',reason=str(exc))
                print(f"打开 img{t['index']}：{t['url']}",flush=True)

            for t in [t for t in tasks if t['status']=='loading']:
                try:
                    driver.switch_to.window(t['handle'])
                    if not args.interactive:
                        # 基础采集只判断时间与画面稳定；内容质量交给后续AI审核。
                        elapsed=time.monotonic()-t['start']
                        t['final_url']=driver.current_url
                        if elapsed>=args.timeout:
                            t['reason']='达到等待上限，直接截图；未保证画面稳定或内容完整'
                            save(t,normalize_screenshot(driver.get_screenshot_as_png()),'timeout_saved')
                        elif elapsed>=args.min_wait:
                            image=stable_capture(driver)
                            if image is not None:
                                t['reason']='画面稳定；尚未经内容审核'
                                save(t,image)
                        continue
                    state=driver.execute_script(PROBE)
                    t['final_url']=state['url']
                    reason=''
                    if state['challenge']: reason='检测到人机验证，请人工完成'
                    elif state['cookie']: reason='检测到 Cookie/隐私弹窗，请人工选择'
                    elif state['error']: reason='疑似错误页面，请检查网址或服务器响应'
                    elif args.manual_each and not t.get('approved') and time.monotonic()-t['start']>=args.min_wait: reason='逐页人工确认'
                    if reason:
                        t.update(status='manual',reason=reason)
                        print(f"待人工 img{t['index']}：{reason}",flush=True)
                        continue
                    ready=state['ready'] and not state['pending'] and not state['broken'] and state['url']!='about:blank'
                    t['good']=t['good']+1 if ready else 0
                    if t['good']>=2 and time.monotonic()-t['start']>=args.min_wait:
                        driver.execute_script('window.scrollTo(0,0)')
                        image=stable_capture(driver)
                        if image is None:
                            t['reason']='轮播/动画/视频画面仍在变化，未取得稳定帧'
                        elif not suspicious(image):
                            save(t,image)
                            continue
                        else:
                            t['reason']='首屏大面积黑色/空白，需确认是否正常设计'
                    if time.monotonic()-t['start']>=args.timeout:
                        t.update(status='manual',reason=t['reason'] or f"等待超时：未就绪={state['pending']}；失败资源={state['broken'][:4]}")
                        print(f"待人工 img{t['index']}：{t['reason']}",flush=True)
                except Exception as exc:
                    t.update(status='manual',reason=str(exc))

            manual=[t for t in tasks if t['status']=='manual']
            if not args.interactive:
                # 导航/检测异常也不进入input；尽力留存当前画面用于诊断。
                for t in manual:
                    try:
                        driver.switch_to.window(t['handle'])
                        save(t,normalize_screenshot(driver.get_screenshot_as_png()),'error_saved')
                    except Exception as exc:
                        t.update(status='failed',reason=t['reason']+'; 截图失败：'+str(exc),review_status='pending_ai_review')
                        try:
                            close(t)
                        except Exception:
                            pass
                manual=[]
            # 自动队列先推进；仅在当前没有加载任务时独占浏览器供人工操作。
            if manual and not any(t['status']=='loading' for t in tasks):
                for t in manual:
                    if args.headless:
                        t['status']='needs_manual'
                        close(t)
                        continue
                    try:
                        driver.switch_to.window(t['handle'])
                        print(f"\n人工处理 img{t['index']}：{t['url']}\n原因：{t['reason']}\n浏览器已停留在该标签，处理期间不会切走。",flush=True)
                        command=input('处理后回车重新检测；r 刷新；s 跳过；f 确认画面并强制保存：').strip().lower()
                        if command=='s':
                            t['status']='skipped'; close(t)
                        elif command=='f':
                            save(t,normalize_screenshot(driver.get_screenshot_as_png()),True)
                        else:
                            if command=='r': driver.refresh()
                            t['approved']=True
                            t.update(status='loading',start=time.monotonic(),good=0,reason='')
                    except Exception as exc:
                        t.update(status='failed',reason=str(exc))
            report()
            time.sleep(1)
    except KeyboardInterrupt:
        print('\n已中止，完成的图片和状态已保留。')
    finally:
        report()
        driver.quit()
    print(f'结果目录：{out}')

if __name__=='__main__':
    main()
