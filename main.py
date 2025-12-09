import lark_oapi as lark
from lark_oapi.api.bitable.v1 import *
from lark_oapi.api.drive.v1 import *
import requests
import base64
import io
from PIL import Image, ImageOps
import time
import os
import re  # 👈 用于正则清洗

# ================= 🟢 环境变量配置 =================
# 这里的变量会自动从 GitHub Secrets 读取，不需要手动填写
APP_ID = os.getenv("APP_ID")
APP_SECRET = os.getenv("APP_SECRET")
APP_TOKEN = os.getenv("APP_TOKEN")
TABLE_ID = os.getenv("TABLE_ID")
AI_API_KEY = os.getenv("AI_API_KEY")

# 🔗 AI 服务配置
AI_API_BASE = "https://x666.me/v1/chat/completions"
AI_MODEL = "gemini-2.5-flash"

# 📋 飞书多维表格字段配置
FIELD_IMG = "上传作文图片"      
FIELD_RESULT = "评语"          
FIELD_STATUS = "单选"          
STATUS_TODO = "未完成"         
STATUS_DONE = "已完成"         
# ==========================================================

# 初始化飞书客户端
client = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).build()

def compress_image(image_binary, max_side=1024, quality=60):
    """
    图片压缩处理：限制尺寸、自动扶正方向、转为 JPEG Base64
    """
    try:
        img = Image.open(io.BytesIO(image_binary))
        # 🔄 关键：根据 EXIF 信息自动旋转图片（解决手机拍照倒置问题）
        img = ImageOps.exif_transpose(img)

        if img.mode in ("RGBA", "P"): img = img.convert("RGB")
        
        w, h = img.size
        if max(w, h) > max_side:
            scale = max_side / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
            
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=quality, optimize=True)
        return base64.b64encode(buffer.getvalue()).decode('utf-8')
    except Exception as e:
        print(f"   ❌ 图片压缩出错: {e}")
        return None

def clean_ai_output(text):
    """
    🧹 V8.1 强力清洗逻辑：
    1. 物理删除 'A改为A' 的废话。
    2. 物理拦截 '小子->小刀' 这类过度推理。
    """
    if not text: return text
    
    cleaned_lines = []
    lines = text.split('\n')
    
    # 正则匹配模式：[xxx] 应改为 [yyy]
    pattern = re.compile(r'\[(.*?)\]\s*应改为\s*\[(.*?)\]')
    
    # 🚫 黑名单：如果出现这些过度纠错，直接拦截
    # 逻辑：有些字长得完全不像，OCR识别错了，AI却强行根据语义去改，这种情况要拦截
    blacklist_pairs = [
        ("小子", "小刀"),
        ("小子", "大刀"),
        ("几乎", "几乎"), # 防止正则漏掉
    ]
    
    print("   🧹 正在执行代码级清洗...")
    
    for line in lines:
        match = pattern.search(line)
        should_skip = False
        
        if match:
            original = match.group(1).strip()
            corrected = match.group(2).strip()
            
            # 🛑 规则1：硬性拦截 A == B
            if original == corrected:
                print(f"      🗑️ 拦截废话: '{original}' -> '{corrected}' (已物理删除)")
                should_skip = True
            
            # 🛑 规则2：黑名单拦截 (针对 AI 的“聪明反被聪明误”)
            for bad_orig, bad_corr in blacklist_pairs:
                if original == bad_orig and corrected == bad_corr:
                    print(f"      🛡️ 拦截过度推理: '{original}' -> '{corrected}' (判定为OCR错误，强制忽略)")
                    should_skip = True
                    break
        
        if not should_skip:
            cleaned_lines.append(line)
        
    return '\n'.join(cleaned_lines)

def call_ai_api_with_retry(image_b64_list, prompt, max_retries=3, temperature=0.1):
    """
    🛡️ 调用 AI 接口：
    temperature=0.1: 让 AI 极度冷静，严格遵守逻辑
    clean_ai_output: 调用清洗函数
    """
    headers = {"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"}
    
    content_list = [{"type": "text", "text": prompt}]
    for b64 in image_b64_list:
        content_list.append({
            "type": "image_url", 
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
        })

    payload = {
        "model": AI_MODEL,
        "messages": [{"role": "user", "content": content_list}],
        "temperature": temperature 
    }

    for attempt in range(max_retries):
        try:
            if attempt > 0: print(f"   🔄 第 {attempt+1} 次重试...")
            
            # print(f"   🧐 发送请求... 温度: {temperature}")
            
            resp = requests.post(AI_API_BASE, json=payload, headers=headers, timeout=60)
            
            if resp.status_code == 200:
                raw_text = resp.json().get('choices', [{}])[0].get('message', {}).get('content', '')
                # ✅ 在返回前，先清洗一遍
                return clean_ai_output(raw_text)
            
            elif resp.status_code in [503, 429, 500, 502, 504]:
                wait_time = 5 * (attempt + 1)
                print(f"   ⚠️ 服务拥堵 (Code {resp.status_code})，休息 {wait_time} 秒...")
                time.sleep(wait_time)
                continue 
            else:
                print(f"   ❌ API 错误: {resp.status_code} - {resp.text}")
                return None
                
        except Exception as e:
            print(f"   ⚠️ 网络/程序错误: {e}")
            time.sleep(3)
            
    return None

def main():
    print("🚀 云端脚本启动 (V8.1 语义误判防御版)...")
    
    if not APP_ID or not AI_API_KEY:
        print("❌ 错误：未读取到环境变量，请检查 GitHub Secrets 配置！")
        return

    # 1. 查找状态为“未完成”的记录
    filter_cmd = f'CurrentValue.[{FIELD_STATUS}] = "{STATUS_TODO}"'
    req = ListAppTableRecordRequest.builder() \
        .app_token(APP_TOKEN).table_id(TABLE_ID) \
        .filter(filter_cmd).build()

    resp = client.bitable.v1.app_table_record.list(req)
    if not resp.success():
        print(f"❌ 获取记录失败: {resp.msg}")
        return

    records = resp.data.items
    if not records:
        print("✅ 所有作业都已批改完成！")
        return

    print(f"📋 发现 {len(records)} 份待批改作业。")

    for i, record in enumerate(records):
        rec_id = record.record_id
        fields = record.fields
        student_name = fields.get("学生姓名", "未知学生")
        
        print(f"\n[{i+1}/{len(records)}] 正在批改 {student_name} 的作业...")

        img_list = fields.get(FIELD_IMG)
        if not img_list:
            print("   ⚠️ 未上传图片，跳过。")
            continue
            
        # 2. 下载并处理图片
        b64_images = []
        all_downloads_success = True
        
        for img_info in img_list:
            file_token = img_info['file_token']
            print(f"   ⬇️ 下载图片...", end="", flush=True)
            
            down_req = DownloadMediaRequest.builder().file_token(file_token).build()
            down_resp = client.drive.v1.media.download(down_req)
            
            if down_resp.success():
                b64 = compress_image(down_resp.file.read())
                if b64: 
                    b64_images.append(b64)
                    print(" OK")
                else:
                    print(" 压缩失败")
                    all_downloads_success = False
                    break 
            else:
                print(f" 失败 ({down_resp.msg})")
                all_downloads_success = False
                break 
        
        if not all_downloads_success:
            print("   ⛔ 存在图片下载/处理失败，为防止误判，跳过此学生。")
            continue 
            
        if not b64_images:
            print("   ⚠️ 图片列表为空，跳过。")
            continue

        # 3. 调用 AI (注入 V8.1 终极提示词)
        print(f"   🧠 AI ({AI_MODEL}) 正在思考...")
        
        # 👇👇👇 V8.1 提示词：针对“语义过度联想”进行了特训 👇👇👇
        prompt = """
# 小学作文批改主控指令 (Master Prompt) v8.1
# 核心目标：100% 杜绝 OCR 误判和过度语义联想

## Role (角色设定)
你是一位严格的 AI 助教。你的任务是发现亮点，仅修正**字形明显错误**的错别字，绝不能“无中生有”。

## I. 绝对防御机制 (Pre-flight Firewall)

1.  **数字防御（一 ≠ ー）**：
    * 看见 "ー只"、"—个"、"-个"，必须默认为 "一只"、"一个"。**严禁报错**。

2.  **同字防御（神 ≠ 神）**：
    * **严禁**输出 `[原文] 应改为 [原文]`。如果汉字完全一样，必须直接删除该条目。

3.  **禁止语义强改（核心规则·针对“小子/小刀”案）**：
    * **严禁根据上下文“猜”字**。
    * **例子**：虽然文中后面提到了“镰刀”，但如果学生写的是“小子”（或者是OCR识别成了“小子”），且“子”和“刀”的字形相差巨大，**严禁**改为“小刀”。
    * **理由**：这通常是OCR把“虫子”或“样子”识别错了，而不是学生真的写了“小刀”的错别字。此时应**保持沉默**，不要强行纠错。

## II. 典型案例教学 (Few-Shot - 严格模仿)

**请仔细学习以下【错误案例】与【正确案例】的区别，严禁重犯错误案例中的逻辑！**

### 案例 1：同字互改（绝对禁止）
* ❌ [几乎] 应改为 [几乎] -> **必须删除！**
* ✅ (什么都不输出)

### 案例 2：OCR 误识别“一”（绝对禁止）
* ❌ [ー] 应改为 [一] -> **必须删除！**
* ✅ (什么都不输出)

### 案例 3：过度语义联想（绝对禁止·针对性训练）
* **场景**：原文 OCR 识别为“螳螂挥舞着**小子**”，后文有“镰刀”。
* ❌ [小子] 应改为 [小刀] -> **必须删除！(这是OCR错误，严禁强改)**
* ✅ (什么都不输出，或者在书写建议里委婉提醒)

### 案例 4：连笔误判
* ❌ [及和] 应改为 [和] -> **必须删除！**
* ✅ (什么都不输出)

### 案例 5：真实的错别字（只有这种才保留）
* ✅ [功击] 应改为 [攻击] -> **保留** (理由：打仗要用“攻”)。

## III. Output Format (最终输出格式)

请严格只输出以下三部分，**严禁**使用 Markdown 符号。

一、字词体检

[错字/拼音] 应改为 [正确字词] (理由：简短口语解释)
[书写不规范的字] (建议：仅针对字迹潦草但不算错字的情况，给出温柔提醒；若无，此行留空)
(若全篇无真实错字：🎉 字迹工整，没有发现错别字，写得很认真！)

二、句子优化

原句：[引用原句]
问题：[老师猜你是想说……这里读起来有点……]
建议：[修改后的示范句]
(若句子整体通顺：句子读起来很顺，老师暂时没有发现需要大改的地方，保持现在的感觉就很好！)

三、老师评语

[这里输出一段纯文本评语，不要分点，不要换行。内容包含：1.温暖开场；2.引用2处细节表扬；3.给出1个具体的修改行动指令；4.鼓励结尾。字数150-200字。]
"""
        
        # 4. 调用 AI (开启 0.1 低温模式 + 代码清洗)
        ai_comment = call_ai_api_with_retry(b64_images, prompt, temperature=0.1)
        
        if ai_comment:
            print("   ✍️ 写入评语...")
            update_req = UpdateAppTableRecordRequest.builder() \
                .app_token(APP_TOKEN).table_id(TABLE_ID).record_id(rec_id) \
                .request_body(AppTableRecord.builder().fields({
                    FIELD_RESULT: ai_comment,
                    FIELD_STATUS: STATUS_DONE
                }).build()).build()
                
            if client.bitable.v1.app_table_record.update(update_req).success():
                print(f"   ✅ 完成！")
            else:
                print("   ❌ 回写失败")
        else:
            print("   ⚠️ AI 处理失败，跳过回写。")
        
        print("   ⏳ 休息 5 秒...")
        time.sleep(5)

if __name__ == "__main__":
    main()
