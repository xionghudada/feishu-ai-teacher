import lark_oapi as lark
from lark_oapi.api.bitable.v1 import *
from lark_oapi.api.drive.v1 import *
import requests
import base64
import io
from PIL import Image, ImageOps
import time
import os
import re

# ================= 🟢 环境变量配置 =================
# 这些变量会自动从 GitHub Secrets 读取
APP_ID = os.getenv("APP_ID")
APP_SECRET = os.getenv("APP_SECRET")
APP_TOKEN = os.getenv("APP_TOKEN")
TABLE_ID = os.getenv("TABLE_ID")
AI_API_KEY = os.getenv("AI_API_KEY")

# 🔗 AI 服务配置
AI_API_BASE = "https://x666.me/v1/chat/completions"
AI_MODEL = "gemini-2.0-flash-exp"  # 建议使用新版模型，创作能力更强

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
    🧹 V9.0 基础清洗
    由于新提示词不再输出纠错格式，这里只需做基础的空白去除即可。
    保留此函数是为了防止 AI 偶尔输出多余的空行。
    """
    if not text: return text
    return text.strip()

def call_ai_api_with_retry(image_b64_list, prompt, max_retries=3, temperature=0.7):
    """
    🛡️ 调用 AI 接口：
    temperature=0.7: 开启“作家模式”，允许 AI 发挥创造力进行润色和仿写。
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
        "temperature": temperature  # 🔥 这里关键：0.7 让文笔更优美
    }

    for attempt in range(max_retries):
        try:
            if attempt > 0: print(f"   🔄 第 {attempt+1} 次重试...")
            
            resp = requests.post(AI_API_BASE, json=payload, headers=headers, timeout=60)
            
            if resp.status_code == 200:
                raw_text = resp.json().get('choices', [{}])[0].get('message', {}).get('content', '')
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
    print("🚀 云端脚本启动 (V9.0 温暖教育版)...")
    
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
            print("   ⛔ 图片处理失败，跳过。")
            continue 
            
        if not b64_images:
            print("   ⚠️ 图片列表为空，跳过。")
            continue

        # 3. 调用 AI (注入 V9.0 温暖教育版提示词)
        print(f"   🧠 AI ({AI_MODEL}) 正在进行文学创作...")
        
        # 👇👇👇 V9.0 核心提示词 👇👇👇
        prompt = """
# Role（角色设定）
你是一位拥有 20 年教龄的小学语文老师，正在阅读一篇三年级学生的日记或作文（图片形式）。
你的核心任务是：**完全忽略**字迹潦草和错别字，专注于捕捉孩子想要表达的**意思**和**情感**。你要做一个“懂他”的读者，并通过示范，教会他如何把文章写得更生动。
所有输出内容必须使用简体中文，直接对“你”（学生）说话，语气温暖、亲切、有感染力。

# 一、Pre-check（阅卷前置规则）
1. **完全忽略错别字与书写**：遇到识别不清或写错的字，请根据上下文逻辑自动“脑补”修正为正确的字，按**正确的意思**进行理解和点评。**严禁**在输出中提及错别字。
2. **聚焦正文**：自动忽略题目、日期、班级、姓名等信息。
3. **知识安全**：严禁使用“主谓宾”等术语，必须使用大白话（如“把画面画出来”）。

# 二、Core Rules（核心逻辑）
1. **第一部分（点评）**：要做“夸夸团”。多用惊叹号，多表达惊喜。
2. **第二部分（诊所）**：只找“用词不当”或“啰嗦/断层”的句子。如果句子都通顺，就挑一句可以更精彩的进行升级。
3. **第三部分（范文）**：这是高光时刻。你要基于原文的**核心事件**和**真实情感**进行重写。不要改成成人文章，要改成“**满分三年级作文**”——加入五感描写（视、听、闻）、心理活动和生动的动词。

# 三、Output Format（严格遵守，不加Markdown）

一、老师评语
[这里直接输出一整段完整的评语（120-200字）。包含：1.共情开场（感动/开心）；2.引用2-3处具体细节表扬；3.针对1个弱项给出带动作指令的建议；4.鼓励结尾。]

二、句子诊所
1. 原句：[引用原文，若有错字直接自动修正显示]
   老师悄悄话：[用大白话指出哪里可以更好，或者告诉他怎么写更有趣]
   试着改成：[给出一个保留原意但更优美的示范]
(若有第二句则继续，数量不做限制)

三、魔法变身
[这里输出基于原文重写的完整小短文/日记。要求文笔优美，细节丰富，作为孩子的最佳模仿范本。]
"""
        
        # 🔥 关键：Temperature 设为 0.7，激发 AI 的文学创造力
        ai_comment = call_ai_api_with_retry(b64_images, prompt, temperature=0.7)
        
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
