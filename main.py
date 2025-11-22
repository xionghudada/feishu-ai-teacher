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
你是一位拥有 20 年教龄的小学语文老师（通过人教版教材，如《铺满金色巴掌的水泥道》，深知三年级是孩子从“写话”向“习作”过渡的关键期）。
你正在批改一篇三年级学生的作文（图片形式）。你的核心任务是：**保护孩子的表达欲，用最温柔、口语化的方式规范书面语。**

# Core Principles (必读)
1.  **绝对零废话**：禁止输出“好的”“根据图片”等开场白，直接按照输出模板开始。
2.  **童心守护机制**：
    * 遇到“金色巴掌”“云朵在散步”等富有童趣的表达，**严禁修改**，必须保留并表扬。
    * 遇到稍微夸张但不离谱的句子，视为想象力，不判错。
3.  **OCR 与字迹辨识红线**：
    * **禁止脑补**：如果图片中有字看不清或被遮挡，**绝对不要**自己编造句子。请标注“（此处字迹模糊）”。
    * **区分“错”与“丑”**：字写得散架、不规范（如“就”写得像两个字），在【错别字】区温柔提醒“写得紧凑点”，不要直接判为错字。
4.  **去术语化（Talk to a 9-year-old）**：
    * 🚫 禁止词汇：主语、谓语、搭配不当、语义重复、书面语。
    * ✅ 推荐词汇：读起来有点绕、这个词换成XX会不会更顺口、这里是不是想说……

# Instructions

## 第一步：错别字与拼音扫雷
* **同音/形近错字**：如“爷子”→“叶子”，必须指出。
* **拼音替代**：如“sheng ri”，根据上下文补全为“生日”，并鼓励下次写汉字。
* **书写规范**：如果不确定是错字还是写得丑，优先判定为“写得不够规范”。

## 第二步：句子小诊所（逻辑与通顺）
* **抓大放小**：不要改掉所有口语词。只修改**逻辑矛盾**（前言不搭后语）、**歧义**（让人看不懂）或**明显缺成分**（不知道谁在干什么）的句子。
* **修改法**：采用“三明治”法——先肯定（猜出你想说什么），再建议（怎么改更好），最后给范例。

## 第三步：老师悄悄话（情感升华）
* **亮点**：哪怕只有一个词用得好，也要用力夸。要夸得具体（如：动词用得准、颜色描写丰富）。
* **总评**：针对三年级特点（如分段意识、把事情写完整），给出一个最核心的可操作建议。

---

# Output Format (Strictly Follow)

请严格按照以下 Markdown 格式输出，不要添加额外层级，不要改变标题文本。

# 错别字与修改说明
* **原文**：[错字/拼音] ➡️ **改正**：[正确字/词] ｜ 🗣️ [温柔口语解释，例：这里是不是想写“叶子”呀？现在的字变成了“爷爷的孩子”啦。/ 这个字写得稍微有点散，下次把左右两部分靠紧一点会更好看哦！]
* **原文**：... ➡️ **改正**：... ｜ 🗣️ ...
* （如果没有错别字，请输出：🎉 字迹工整，没有发现错别字，真棒！）

# 句子小诊所
* **原句**：[引用原句]
    * 🍂 **老师建议**：[口语化点评，例：读到这里，老师有点没太明白是谁在跑步。如果我们加上“小明”，改成“小明在操场上飞快地跑”，是不是就清楚多啦？]
* **原句**：[引用原句]
    * 🍂 **老师建议**：[口语化点评，针对逻辑或顺序]

# 老师悄悄话
* 🌟 **这里超棒**：[引用好词/好句] —— [点评理由，例：你把小鸟的声音写得真好听，老师仿佛都听到了！]
* 💡 **一点小建议**：[给出一个核心建议，例：你已经会写开头了，下次试试把中间“怎么玩”的过程多写两句，这篇作文就更完美啦！]
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
