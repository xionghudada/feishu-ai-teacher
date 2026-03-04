import lark_oapi as lark
from lark_oapi.api.bitable.v1 import *
from lark_oapi.api.drive.v1 import *
import requests
import base64
import io
from PIL import Image, ImageOps
import time
import os
import random  # 🆕 导入随机库用于人设抽卡

# ================= 环境变量配置 =================
APP_ID = os.getenv("APP_ID")
APP_SECRET = os.getenv("APP_SECRET")
APP_TOKEN = os.getenv("APP_TOKEN")
TABLE_ID = os.getenv("TABLE_ID")
AI_API_KEY = os.getenv("AI_API_KEY")

# 🔗 AI 服务配置
AI_API_BASE = "https://x666.me/v1/chat/completions"
AI_MODEL = "gemini-3-flash-preview" 

# 📋 飞书多维表格字段配置
FIELD_IMG = "上传作文图片"      
FIELD_RESULT = "评语"          
FIELD_STATUS = "单选"          
STATUS_TODO = "未完成"         
STATUS_DONE = "已完成"         
# ==========================================================

client = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).build()

def compress_image(image_binary, max_side=1024, quality=60):
    """图片压缩处理：限制尺寸、自动扶正方向、转为 JPEG Base64"""
    try:
        img = Image.open(io.BytesIO(image_binary))
        img = ImageOps.exif_transpose(img) # 解决手机拍照倒置
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

def call_ai_api_with_retry(image_b64_list, prompt, max_retries=3, temperature=0.85):
    """🛡️ 调用 AI 接口：提高温度值，激发文学创造力"""
    headers = {"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"}
    content_list = [{"type": "text", "text": prompt}]
    for b64 in image_b64_list:
        content_list.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

    payload = {
        "model": AI_MODEL,
        "messages": [{"role": "user", "content": content_list}],
        "temperature": temperature
    }

    for attempt in range(max_retries):
        try:
            resp = requests.post(AI_API_BASE, json=payload, headers=headers, timeout=60)
            if resp.status_code == 200:
                # 🧹 直接在此处 strip()，无需额外的 clean 函数
                return resp.json().get('choices', [{}])[0].get('message', {}).get('content', '').strip()
            time.sleep(5)
        except Exception as e:
            print(f"   ⚠️ 网络错误: {e}")
            time.sleep(3)
    return None

def main():
    print("🚀 云端脚本启动 (V10.0 6类人格随机抽取)...")
    
    # 🎭 性格矩阵：每种性格包含专属禁忌和引导语
    personas = [
        {"name": "细节控老班", "rule": "禁‘哇、感叹号’。首句必须从具体动作切入。用观察代替赞美。"},
        {"name": "好奇心顽童", "rule": "禁‘老师、应该’。首句必须是迫不及待的提议/提问，以平辈视角交流。"},
        {"name": "诗人姐姐", "rule": "禁‘逻辑、修改’。首句必须是充满画面感的比喻，用色彩和声音代替叙述。"},
        {"name": "幽默大叔", "rule": "禁‘规范、严肃’。必须包含一处‘自黑’或对文中趣事的调侃，语气风趣。"},
        {"name": "能量教练", "rule": "禁‘可能、好像’。语气短促有力，首句必须是充满力度的肯定。"},
        # {"name": "哲思智者", "rule": "禁‘满分、厉害’。侧重发现孩子展现的内在品质（如勇气、诚实真诚）。"}
    ]

    if not APP_ID or not AI_API_KEY:
        print("❌ 错误：环境变量缺失！")
        return

    # 1. 查找待处理记录
    filter_cmd = f'CurrentValue.[{FIELD_STATUS}] = "{STATUS_TODO}"'
    req = ListAppTableRecordRequest.builder().app_token(APP_TOKEN).table_id(TABLE_ID).filter(filter_cmd).build()
    resp = client.bitable.v1.app_table_record.list(req)
    
    if not resp.success() or not resp.data.items:
        print("✅ 所有作业已处理完毕。")
        return

    records = resp.data.items
    for i, record in enumerate(records):
        rec_id = record.record_id
        student_name = record.fields.get("学生姓名", "未知学生")
        print(f"\n[{i+1}/{len(records)}] 正在批改 {student_name}...")

        # 🎲 随机抽取一人设
        persona = random.choice(personas)
        print(f"   🎭 抽到性格：{persona['name']}")

        img_list = record.fields.get(FIELD_IMG)
        if not img_list: continue
            
        b64_images = []
        for img_info in img_list:
            down_resp = client.drive.v1.media.download(DownloadMediaRequest.builder().file_token(img_info['file_token']).build())
            if down_resp.success():
                b64 = compress_image(down_resp.file.read())
                if b64: b64_images.append(b64)
        
        if not b64_images: continue

        # 🧠 构建 Prompt
        prompt = f"""
# Role
你是一位资深小学语文老师。当前性格人设：【{persona['name']}】。
专属规则：{persona['rule']}。

# 一、阅卷前置规则
1. **完全忽略错别字**：自动修正理解，严禁提及“错字、字迹、书写”。
2. **包容创新表达**：如学生使用“呆头萌”、“绝绝子”等网络词汇，请将其视为“灵动闪光点”给予表扬。
3. **术语脱敏**：禁止使用“主谓宾、拟人”等术语，改用大白话。
4. **模版清除**：禁止出现“你的作文”、“读完你的文章”、“老师发现”等AI感极强的套话。

# 二、核心逻辑
1. **第一部分（点评）**：严格遵守【{persona['name']}】的首句要求。直接切入情节，引用细节。
2. **第二部分（诊所）**：找1处优化点，用“老师悄悄话”给出动作性建议。
3. **第三部分（魔法变身）**：重写为三年级满分范文，保留并升华孩子原有的灵动词汇。

# 三、Output Format（严格遵守，不加Markdown）

一、老师评语
[根据选定性格输出 150-200 字。首句直接切入情节。]

二、句子诊所
1. 原句：[引用原文，自动修正错别字]
   老师悄悄话：[大白话建议]
   试着改成：[示范]

三、魔法变身
[重写后的完整短文。]
"""
        
        ai_comment = call_ai_api_with_retry(b64_images, prompt)
        
        if ai_comment:
            update_req = UpdateAppTableRecordRequest.builder() \
                .app_token(APP_TOKEN).table_id(TABLE_ID).record_id(rec_id) \
                .request_body(AppTableRecord.builder().fields({FIELD_RESULT: ai_comment, FIELD_STATUS: STATUS_DONE}).build()).build()
            client.bitable.v1.app_table_record.update(update_req)
            print(f"   ✅ {student_name} 完成！")
        
        time.sleep(2)

if __name__ == "__main__":
    main()
