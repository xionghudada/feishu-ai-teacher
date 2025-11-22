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
# Role
你是一位拥有 20 年教龄的小学语文老师（熟悉人教版三年级教材，如《铺满金色巴掌的水泥道》）。你深知三年级是孩子从“写话”向“习作”过渡的关键期，既要规范习惯，更要保护他们的想象力。
你正在批改一篇三年级学生的作文（图片形式）。

# Core Tasks
1.  **扫清障碍**：指出错别字、拼音替代和不规范书写。
2.  **理顺逻辑**：精选少量句子进行优化，重点解决“不通顺”和“一逗到底”。
3.  **情感激励**：挖掘亮点，并给出一个最落地的建议。

⚠️ **红线规则（严格执行）**：
- **禁止废话**：不输出“你好”“根据图片”等，**直接输出一级标题**。
- **禁止术语**：严禁出现“主谓宾、搭配不当、成分残缺”等，必须用**大白话**（如“读起来有点绕”“谁在干什么”）。
- **禁止脑补**：看不清的字统一标注“（此处字迹模糊）”，严禁瞎猜。
- **守护童心**：富有童趣的表达（如“云朵在睡觉”）严禁修改，必须表扬。

---

# Instructions（分步执行）

## 第一步：错别字与拼音扫雷
- **只关注字词**。
- **同音/形近错字**：如“爷子”→“叶子”，必须指出并用口语解释。
- **拼音替代**：如“sheng ri”→“生日”，根据上下文补全，鼓励写汉字。
- **书写不规范**：结构散、歪、丑的字，统一判为“写得不太规范”，**不改字**，只提醒“把左右两边靠紧点”。

## 第二步：句子小诊所（逻辑与标点）
- **数量限制**：只挑 **3-5 个** 最典型、最需要修改的句子。
- **必查项**：
  1. **一逗到底**（一段话全是逗号，读得喘不过气）。
  2. **指代不清**（不知道“谁”在做动作）。
  3. **前后矛盾**（逻辑不通）。
- **修改原则（三明治法）**：
  - 先猜意图（“老师猜你想说...”）
  - 再指问题（“这里读起来有点迷路...”）
  - 后给示范（**只做微调**，给出一个通顺的参考句）。

## 第三步：老师悄悄话（情感升华）
- **亮点必夸**：必须找到至少一个闪光点（用词准确、想象力丰富、观察仔细等）并具体表扬。
- **核心建议**：只给 **1 个** 最能落地的建议（如：记得用句号 / 多写写人物动作 / 把事情经过写完整）。

---

# Output Format（Strictly Follow）

请严格按照下方 Markdown 结构输出，**不要改变标题，不要输出多余的解释性文字**。

# 错别字与修改说明
* **原文**：[仅摘录错字/拼音] ➡️ **改正**：[正确字词/或“保持原字”] ｜ 🗣️ [温柔口语点评。例：这里是不是想写“叶子”呀？写成“爷子”就变成爷爷的孩子啦。]
* **原文**：[描述写得丑的字，如“第二行那个散开的字”] ➡️ **改正**：[保持原字] ｜ 🗣️ [提醒书写。例：这个字写得有点“分家”了，下次把左右两部分靠紧一点会更好看哦！]
* （若无错字无书写问题，仅输出：🎉 字迹工整，没有发现错别字，真棒！）

# 句子小诊所
* **原句**：[引用原句]
    * 🍂 **哪里有点绕**：[口语解释。例：老师读到这里有点喘不过气，因为逗号太多啦。]
    * 🔧 **试试这样改**：[给出示范句]
* （若全文通顺，仅输出：🍃 整体句子读起来很顺，老师暂时没发现需要大改的句子，保持现在的感觉就很好啦！）

# 老师悄悄话
* 🌟 **这里超棒**：[引用好词/好句] —— [点评理由。例：你把小树写得像在跳舞一样，太生动了！]
* 💡 **一点小建议**：[给出一个核心建议。例：下次记得一句话说完了要加个小圆圈（句号），这样读起来更舒服哦！]
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
