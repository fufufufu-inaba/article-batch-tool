"""一个可见Chrome、多个异步标签，尽量最小化运行且不主动抢焦点。"""
import asyncio
import json
import re
import time
from pathlib import Path

async def run(selected,args,out,records):
    print('正在加载浏览器组件（自动采集无需ChromeDriver）……',flush=True)
    from playwright.async_api import async_playwright
    from research import domain_of, write_json, company_output_paths, write_company_files, write_companies_bundle, COMPONENT_DIR
    top_list=getattr(args,'article_style','company-profile')=='no-company-images'
    if not top_list:
        from website_screenshot import normalize_screenshot, encode_jpeg_under_target
    probe=(COMPONENT_DIR/'screenshot_probe.js').read_text(encoding='utf-8')
    probe='() => {'+probe+'}'
    accept_cookies="""() => {
      const visible = el => {
        const r=el.getBoundingClientRect(), s=getComputedStyle(el);
        return r.width>3 && r.height>3 && r.top<innerHeight && r.bottom>0 && r.left<innerWidth && r.right>0 &&
          s.display!=='none' && s.visibility!=='hidden' && Number(s.opacity)>0 && !el.disabled;
      };
      const textOf = el => ((el.innerText || el.value || el.getAttribute('aria-label') || el.title || '')+'').trim().replace(/\\s+/g,' ');
      const candidates = Array.from(document.querySelectorAll('button,a,input[type="button"],input[type="submit"],[role="button"]')).filter(visible);
      const accept = /^(accept|agree|allow|ok|got it|i accept|accept all|agree all|allow all|yes|continue|同意|接受|允许|确定|好的|全部接受|我同意|继续)$/i;
      const loose = /(accept all|agree all|allow all|accept cookies|agree cookies|同意全部|接受全部|全部接受|允许全部|接受所有|同意并继续)/i;
      let target = candidates.find(el => accept.test(textOf(el))) || candidates.find(el => loose.test(textOf(el)));
      if(!target) {
        const dialogs = Array.from(document.querySelectorAll('[role="dialog"],[id*="cookie" i],[class*="cookie" i],[id*="consent" i],[class*="consent" i]')).filter(visible);
        for(const box of dialogs) {
          const buttons = Array.from(box.querySelectorAll('button,a,input[type="button"],input[type="submit"],[role="button"]')).filter(visible);
          target = buttons.find(el => accept.test(textOf(el))) || buttons.find(el => loose.test(textOf(el)));
          if(target) break;
        }
      }
      if(target) {
        target.click();
        return textOf(target) || 'clicked';
      }
      return '';
    }"""
    content="""() => {
      const root=document.querySelector('main,article,[role=main]')||document.body;
      return {title:document.title,url:location.href,ready:document.readyState,text:root?.innerText||'',
      links:Array.from(document.querySelectorAll('a[href]')).map(a=>({text:a.innerText,url:a.href}))};
    }"""
    about_pattern=re.compile(r'\babout(?:\s+us)?\b|company[- /_]?profile|who\s+we\s+are|关于|公司简介',re.I)
    content_wait=getattr(args,'content_wait',20)

    def has_about_reference(data,domain):
        if about_pattern.search((data.get('title') or '')+' '+(data.get('url') or '')):
            return True
        for link in data.get('links') or []:
            url=link.get('url') or ''
            if domain_of(url)==domain and about_pattern.search((link.get('text') or '')+' '+url):
                return True
        return False

    async def wait_for_top_content(page,domain,is_home):
        """先等待页面加载；首页在找到About线索后可提前结束等待。"""
        deadline=time.monotonic()+content_wait
        latest=None
        signature=None
        stable_since=None
        last_error=''
        while True:
            now=time.monotonic()
            try:
                data=await page.evaluate(content)
                latest=data
                about_links=tuple((link.get('text') or '',link.get('url') or '') for link in data.get('links') or [] if about_pattern.search((link.get('text') or '')+' '+(link.get('url') or '')))
                current_signature=(data.get('url'),hash(data.get('text') or ''),about_links if is_home else ())
                if current_signature!=signature:
                    signature=current_signature
                    stable_since=now
                ready=data.get('ready')=='complete'
                found=has_about_reference(data,domain) if is_home else True
                if found and ready and stable_since is not None and now-stable_since>=2:
                    return data,False,last_error
            except Exception as exc:
                last_error=str(exc)
                stable_since=None
            if time.monotonic()>=deadline:
                return latest,True,last_error
            await asyncio.sleep(min(1,max(0,deadline-time.monotonic())))
    semaphore=asyncio.Semaphore(args.tabs)
    used=set()
    jobs=[]
    for index,c in enumerate(selected,1):
        base=re.sub('[^a-zA-Z0-9_-]','_',c['brand']).lower() or 'company'
        if base in {'con','prn','aux','nul'} or re.fullmatch('(com|lpt)[0-9]',base): base='site_'+base
        stem=base; n=1
        while stem in used: n+=1; stem=f'{base}_{n}'
        used.add(stem); jobs.append((index,c,stem))
    async with async_playwright() as pw:
        print('正在启动本机Chrome，启动超时上限30秒；窗口会最小化到任务栏。',flush=True)
        browser=await pw.chromium.launch(channel='chrome',headless=False,timeout=30000,args=[
            '--start-minimized',
            '--disable-background-timer-throttling',
            '--disable-renderer-backgrounding',
            '--disable-backgrounding-occluded-windows',
        ])
        context=await browser.new_context(viewport={'width':1600,'height':900},device_scale_factor=1)
        print('采集Chrome已就绪，开始打开官网标签。',flush=True)
        async def worker(index,c,stem):
            async with semaphore:
                page=await context.new_page()
                page.set_default_timeout(10000)
                paths=None
                record=dict(c,input_index=index,status='pending',issues=[],pages=[],article_style=getattr(args,'article_style','company-profile'))
                try:
                    print('后台标签打开：'+c['homepage'],flush=True)
                    response=None
                    try:
                        response=await page.goto(c['homepage'],wait_until='domcontentloaded' if top_list else 'load',timeout=args.timeout*1000)
                    except Exception as exc:
                        message='首页加载异常，检查当前页面：' if top_list else '页面load等待异常，按当前页面继续等待后截图：'
                        record['issues'].append(message+str(exc))
                    if top_list:
                        try:
                            label=await page.evaluate(accept_cookies)
                        except Exception:
                            label=''
                        if label:
                            record['issues'].append('已尝试自动点击Cookie确认按钮：'+label)
                        home,home_timed_out,home_error=await wait_for_top_content(page,c['domain'],True)
                        try:
                            state=await page.evaluate(probe)
                        except Exception as exc:
                            state={}
                            home_error=str(exc)
                        http_error=response is not None and response.status>=400
                        if http_error:
                            record['website_http_status']=response.status
                        if page.url=='about:blank' or http_error:
                            record['status']='skipped_unavailable'
                            record['about_status']='not_checked'
                            record['issues'].append('网站无法正常访问或返回HTTP错误，未保存网页正文')
                        elif state.get('challenge') or state.get('error'):
                            record['status']='needs_review_access'
                            record['about_status']='not_checked'
                            record['issues'].append('等待页面加载后仍是验证/错误页，需人工复查')
                        elif not home:
                            record['status']='needs_review_load_timeout'
                            record['about_status']='load_timeout'
                            record['issues'].append('等待页面正文超时，暂不判定为没有About Us：'+home_error)
                        elif not has_about_reference(home,c['domain']) and home.get('ready')!='complete':
                            record['status']='needs_review_load_timeout'
                            record['about_status']='load_timeout'
                            record['issues'].append('首页尚未完成加载，暂不判定为没有About Us')
                        else:
                            if domain_of(home['url'])!=c['domain']:
                                record['issues'].append('官网重定向至其他主域名，请核实主体')
                            home_url=home['url'].split('#')[0]
                            home_is_about=bool(about_pattern.search((home.get('title') or '')+' '+home['url']))
                            inline_about=False
                            about_links=[]
                            seen={home_url}
                            for link in home.get('links') or []:
                                raw_url=link.get('url') or ''
                                if not about_pattern.search((link.get('text') or '')+' '+raw_url):
                                    continue
                                url=raw_url.split('#')[0]
                                if domain_of(url)!=c['domain']:
                                    continue
                                if url==home_url:
                                    inline_about=True
                                elif url not in seen:
                                    seen.add(url)
                                    about_links.append(url)
                            about_pages=[]
                            if home_is_about or inline_about:
                                about_pages.append(home)
                            about_incomplete=False
                            for url in about_links[:args.about_pages]:
                                try:
                                    about_response=None
                                    previous_url=page.url.split('#')[0]
                                    try:
                                        about_response=await page.goto(url,wait_until='domcontentloaded',timeout=args.timeout*1000)
                                    except Exception as exc:
                                        record['issues'].append('About Us导航等待异常，继续检查页面：'+url+' '+str(exc))
                                    if page.url.split('#')[0]==previous_url and url!=previous_url:
                                        about_incomplete=True
                                        record['issues'].append('About Us导航未进入目标页面，暂不判定该页面没有正文：'+url)
                                        continue
                                    about_page,about_timed_out,about_error=await wait_for_top_content(page,c['domain'],False)
                                    try:
                                        about_state=await page.evaluate(probe)
                                    except Exception as exc:
                                        about_state={}
                                        about_error=str(exc)
                                    if (about_response is not None and about_response.status>=400) or about_state.get('error'):
                                        record['issues'].append('About Us页面返回错误：'+url)
                                        continue
                                    if about_state.get('challenge'):
                                        about_incomplete=True
                                        record['issues'].append('About Us页面仍处于验证中：'+url)
                                        continue
                                    if about_page and about_page.get('url','').split('#')[0]!=home_url and domain_of(about_page.get('url',''))==c['domain']:
                                        about_pages.append(about_page)
                                        if about_timed_out:
                                            record['issues'].append('About Us页面加载未完全结束，已保存当前正文供复查：'+url)
                                    elif about_timed_out and (not about_page or about_page.get('ready')!='complete'):
                                        about_incomplete=True
                                        record['issues'].append('About Us页面加载超时，暂不判定为空白：'+url+' '+about_error)
                                    else:
                                        record['issues'].append('About Us链接没有打开独立的有效页面：'+url)
                                except Exception as exc:
                                    about_incomplete=True
                                    record['issues'].append('About Us页面检查失败：'+url+' '+str(exc))
                            if about_pages:
                                record['pages']=[home]+[item for item in about_pages if item.get('url')!=home_url]
                                record['about_status']='found'
                                record['about_urls']=[item.get('url','') for item in about_pages]
                                record['collected_at']=time.strftime('%Y-%m-%dT%H:%M:%S')
                                record['status']='collected_needs_review'
                                if home_timed_out:
                                    record['issues'].append('首页达到内容等待上限，已根据当前About线索继续采集')
                                print('About Us内容已保存：'+stem,flush=True)
                            elif about_incomplete:
                                record['status']='needs_review_about_timeout'
                                record['about_status']='load_timeout'
                                record['issues'].append('About Us页面未完成加载，暂不判定为无介绍内容')
                            else:
                                record['status']='skipped_no_about_us'
                                record['about_status']='not_found'
                                record['issues'].append('网站已打开，但未找到可访问的About Us/公司介绍页面，未保存网页内容')
                    else:
                        paths=company_output_paths(out,stem)
                        clicked=[]
                        wait_seconds=getattr(args,'post_load_wait',7)
                        end=time.monotonic()+wait_seconds
                        while time.monotonic()<end:
                            try:
                                label=await page.evaluate(accept_cookies)
                                if label:
                                    clicked.append(label)
                            except Exception:
                                pass
                            await asyncio.sleep(.5)
                        if clicked:
                            record['issues'].append('已尝试自动点击Cookie确认按钮：'+', '.join(clicked[:3]))
                        state=await page.evaluate(probe)
                        http_error=response is not None and response.status>=400
                        if (page.url=='about:blank' or page.url.startswith('chrome-error:') or
                                state.get('error') or http_error):
                            record['status']='skipped_unavailable'
                            record['issues'].append('指定官网无法正常访问或为错误页，不保存截图和公司正文')
                            if http_error:
                                record['website_http_status']=response.status
                            print('跳过不可访问官网：'+c['homepage'],flush=True)
                            return
                        if state.get('challenge'):
                            record['issues'].append('当前页面可能为人机验证，截图需人工复查')
                        image=normalize_screenshot(await page.screenshot(timeout=10000))
                        data=await asyncio.to_thread(encode_jpeg_under_target,image,102400)
                        paths['image'].write_bytes(data)
                        record.update(screenshot=str(paths['image'].relative_to(out)),capture_mode='load_plus_wait',collected_at=time.strftime('%Y-%m-%dT%H:%M:%S'))
                        print('首屏已保存，开始About：'+stem,flush=True)
                        home=await page.evaluate(content)
                        record['pages'].append(home)
                        if domain_of(home['url'])!=c['domain']: record['issues'].append('跳转到其他主域名，需核实')
                        links=[]
                        seen={home['url'].split('#')[0]}
                        for link in home['links']:
                            url=link['url'].split('#')[0]
                            if domain_of(url)==c['domain'] and url not in seen and about_pattern.search(link['text']+' '+url):
                                seen.add(url); links.append(url)
                        for url in links[:args.about_pages]:
                            try:
                                await page.goto(url,wait_until='domcontentloaded',timeout=args.timeout*1000)
                                await asyncio.sleep(3)
                                state=await page.evaluate(probe)
                                if state['challenge'] or state['error']:
                                    record['issues'].append('About验证或错误页：'+url); continue
                                record['pages'].append(await page.evaluate(content))
                            except Exception as exc: record['issues'].append('About失败：'+url+' '+str(exc))
                        if not links: record['issues'].append('未发现公司介绍链接，仅保存首页正文')
                        if len(home['text'].strip())<150: record['issues'].append('首页正文过少，需人工核实')
                        record['status']='collected_needs_review'
                except Exception as exc:
                    record['status']='skipped_unavailable' if top_list else 'failed'; record['issues'].append(str(exc))
                finally:
                    if top_list and record['status']!='collected_needs_review':
                        record['pages']=[]
                        print('官网或About Us检查未通过，不保存网页正文：'+c['homepage']+' ['+record['status']+']',flush=True)
                    for item in record['pages']:
                        item.pop('links',None)
                    if (not top_list and record['status']!='skipped_unavailable') or record['status']=='collected_needs_review':
                        write_company_files(out,stem,c,record,args)
                    records.append(record); records.sort(key=lambda r:r['input_index'])
                    if record['status']=='skipped_unavailable' and c.get('manual_urls'):
                        skipped_path=out/'manual_url_skipped.json'
                        skipped=json.loads(skipped_path.read_text(encoding='utf-8')) if skipped_path.exists() else []
                        if not any(item.get('domain')==c['domain'] for item in skipped):
                            skipped.append(dict(c,skip_reason='; '.join(record['issues'])))
                        write_json(skipped_path,skipped)
                    write_json(out/'results.json',records)
                    write_companies_bundle(out,records,args)
                    await page.close()
        try:
            await asyncio.gather(*(worker(*job) for job in jobs))
        finally:
            await context.close(); await browser.close()
