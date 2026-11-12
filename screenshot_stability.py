"""只接受同一标签中连续稳定的截图，返回已经检查过的最后一帧。"""
import time
from PIL import ImageChops, ImageStat
from website_screenshot import normalize_screenshot

MOTION = r"""
return (() => {
 const visible = e => {
   if (!e || !e.getBoundingClientRect) return false;
   const r=e.getBoundingClientRect(), s=getComputedStyle(e);
   return r.width>8 && r.height>8 && r.top<innerHeight && r.bottom>0 &&
     r.left<innerWidth && r.right>0 && s.visibility!=='hidden' && s.display!=='none';
 };
 // 浏览器原生 CSS transition / animation，包括淡入淡出、位移。
 const moving=document.getAnimations().some(a => {
   if (a.playState!=='running' || !visible(a.effect?.target)) return false;
   const t=a.effect.getComputedTiming();
   return t.progress!==null && t.progress>0 && t.progress<1;
 });
 // Swiper 的 JS 状态也覆盖部分非 CSS 实现。
 const swiper=Array.from(document.querySelectorAll('.swiper,.swiper-container'))
   .some(e=>visible(e) && e.swiper?.animating);
 return moving || swiper;
})();
"""

def similar(a, b):
    # 缩图抑制微小渲染噪声；同时限制平均差和明显变化的像素比例。
    diff=ImageChops.difference(a.resize((320,180)).convert('RGB'),
                               b.resize((320,180)).convert('RGB'))
    mean=sum(ImageStat.Stat(diff).mean)/3
    hist=diff.convert('L').histogram()
    changed=sum(hist[13:])/(320*180)
    return mean<0.8 and changed<0.004

def stable_capture(driver):
    """约0.8秒内取三帧；运动或检测失败不输出自动截图。"""
    frames=[]
    for i in range(3):
        if i:
            time.sleep(0.4)
        if driver.execute_script(MOTION):
            return None
        frame=normalize_screenshot(driver.get_screenshot_as_png())
        if driver.execute_script(MOTION):
            return None
        frames.append(frame)
    if not all(similar(frames[0], frame) for frame in frames[1:]):
        return None
    # 使用已验证的像素，避免验证结束后重新截图时恰逢下一次轮播。
    return frames[-1]
