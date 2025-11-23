import lark_oapi as lark
from lark_oapi.api.bitable.v1 import *
from lark_oapi.api.drive.v1 import *
import requests
import base64
import io
from PIL import Image, ImageOps
import time
import os  # 👈 新增：用于读取 GitHub 的环境变量

# ================= 🟢 环境变量配置 (云端安全模式) =================
# 这些变量会自动从 GitHub Settings -> Secrets 中读取，无需在此处填写
APP_ID = os.getenv("APP_ID")
APP_SECRET = os.getenv("APP_SECRET")
APP_TOKEN = os.getenv("APP_TOKEN")
TABLE_ID = os.getenv("TABLE_ID")
AI_API_KEY = os.getenv("AI_API_KEY")

# 🔗 AI 服务地址 (通常固定，也可改为环境变量)
AI_API_BASE = "https://jestiqlunbtr.ap-southeast-1.clawcloudrun.com/v1/chat/completions"

# 🤖 模型选择 (保持你选择的 1.5-pro)
AI_MODEL = "gemini-2.5-pro"

# 📋 字段映射配置
FIELD_IMG = "上传作文图片"      
FIELD_RESULT = "评语"          
FIELD_STATUS = "单选"          
STATUS_TODO = "未完成"         
STATUS_DONE = "已完成"         
# ==========================================================

# 初始化飞书客户端
client = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).build()

def compress_image(image_binary, max_side=1024, quality=60):
    """ 图片压缩：限制长边 1024px，转 JPEG 压缩质量 60，且自动扶正方向 """
    try:
        img = Image.open(io.BytesIO(image_binary))
        
        # 🔄 关键步骤：根据 EXIF 信息自动旋转图片
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

def call_ai_api_with_retry(image_b64_list, prompt, max_retries=3):
    """ 🛡️ 带重试机制的 API 调用 """
    headers = {"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"}
    
    content_list = [{"type": "text", "text": prompt}]
    for b64 in image_b64_list:
        content_list.append({
            "type": "image_url", 
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
        })

    payload = {
        "model": AI_MODEL,
        "messages": [{"role": "user", "content": content_list}]
    }

    for attempt in range(max_retries):
        try:
            if attempt > 0: print(f"   🔄 第 {attempt+1} 次重试连接 AI...")
            
            resp = requests.post(AI_API_BASE, json=payload, headers=headers, timeout=60)
            
            if resp.status_code == 200:
                return resp.json().get('choices', [{}])[0].get('message', {}).get('content', '')
            
            elif resp.status_code in [503, 429, 500, 502, 504]:
                wait_time = 5 * (attempt + 1)
                print(f"   ⚠️ 服务拥堵 (Code {resp.status_code})，休息 {wait_time} 秒...")
                time.sleep(wait_time)
                continue 
            else:
                print(f"   ❌ API 错误: {resp.status_code} - {resp.text}")
                return None
                
        except requests.exceptions.RequestException as e:
            print(f"   ⚠️ 网络波动: {e}，准备重试...")
            time.sleep(3)
            
    print("   ❌ 重试 3 次均失败，跳过此条。")
    return None

def main():
    print("🚀 云端脚本启动 (GitHub Actions 版)...")
    
    # 检查环境变量是否获取成功
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
            
        # 2. 下载并处理图片 (保留你的严格检查逻辑)
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

        # 3. 调用 AI (保留三年级专属 Prompt)
        print(f"   🧠 AI ({AI_MODEL}) 正在思考 ({len(b64_images)} 图)...")
        
        prompt = """
Role
你是一位拥有 20 年教龄的小学语文老师（熟悉人教版三年级教材）。 正在批改一篇三年级学生的作文（图片形式）。 核心风格：温柔、利落、直击重点。拒绝花哨的格式，拒绝废话。

Core Rules (严格执行)
禁止废话：不要输出“好的”“根据作文”等前言，直接输出一级标题。

禁止脑补：看不清的字直接标注“（字迹模糊）”，严禁瞎猜。

童心守护：富有童趣的表达（如“云朵在睡觉”）严禁修改，必须表扬。

去术语化：禁止使用“主谓宾、搭配不当”等术语，用大白话解释。

Output Instructions (关键逻辑)
第一部分：字词体检（分为两类，没有就别写）
请将“错别字”和“书写不规范”彻底分开，不要套用同一个格式。

【错字/拼音】：只有真的写错字或用拼音时才列在这里。直接指出错误并给出正确写法。

【书写提醒】：针对写得散、歪、丑但不算错的字。绝不要写“改正：...”，直接给建议。

第二部分：句子优化（限 2-3 句）
重点解决“一逗到底”和“不通顺”。不要用表情符号刷屏，用简单的加粗和缩进区分即可。

第三部分：老师评语
简练点出亮点，给一个核心建议。

Output Format (Copy This Structure)
请严格按照以下 Markdown 格式输出，不要添加额外的表情符号，保持版面清爽。

一、字词体检
[错字/拼音]：应改为 [正确字词]。 (理由：[简短口语解释])

[错字/拼音]：应改为 [正确字词]。 (理由：[简短口语解释])

[书写不规范的字]：[直接给出书写建议，例如：这个字左右分家了，下次靠紧一点。]

(如果没有错字和书写问题，请只写：🎉 字迹工整，没有发现错别字！)

二、句子优化
原句：[引用原句]

问题：[口语化指出问题，如：这里逗号太多，读起来喘不过气。]

建议：[给出修改后的示范句]

原句：[引用原句]

问题：[口语化指出问题]

建议：[给出修改后的示范句] (如果句子都很通顺，请只写：🍃 句子读起来都很顺，保持现在的感觉就好！)

三、老师评语
✨ 亮点：[引用好词好句] —— [具体表扬理由]

💡 建议：[ 根据文字内容给 2 到 3条最核心的建议，如：下次记得一句话说完了加句号。]
"""
        
        ai_comment = call_ai_api_with_retry(b64_images, prompt)
        
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
            print("   ⚠️ AI 处理失败，保留状态为未完成。")
        
        print("   ⏳ 休息 5 秒...")
        time.sleep(5)

if __name__ == "__main__":
    main()
